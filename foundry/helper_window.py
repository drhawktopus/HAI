"""Helper WINDOW: one compute order per lead run on the helper box, replacing per-piece desk orders.

Protocol:
- Input: items in `<items_dir>`; output in `{ORDER_OUT}/out.json`.
- Item schema: `{"id", "prompt"}` -> `<id>.out.json`; Errand items include `messages`/`tools`.
- Close signals: `CLOSE` file in `<items_dir>`, or lead's desk order terminates.

Constraints & Invariants:
- Residency: Model remains resident for the duration; do not spawn per-piece.
- Liveness: Not a desk order; use `OWNER.json` heartbeat (silent 3x period = DEAD).
- Visibility: Must appear as registered compute order in Foundry/Deck.
- Streaming: If `"stream": true`, write `<id>.stream.jsonl` (chunks flushed individually). Restarting generation invalidates previous stream (new `attempt` ID).
- Cancellation: `<id>.cancel` file stops generation within `CANCEL_CHECK_S`.
- Error Handling: rc 2 (refusal) holds order in Foundry; do not crash."""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import infer_ollama as IO  # noqa: E402  the same _chat (streamed, loop-cut, 5xx retry), residency check and meter

# The reader for every per-box address. This payload is spawned from <root>\workspace\<dir>,
# so the foundry's bin is two levels up; if it cannot be imported (no foundry tree), only the
# environment is read and no address is assumed.
try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
    _ROOT = os.environ.get("FOUNDRY_ROOT") or os.path.dirname(os.path.dirname(_HERE))
    _BIN = os.path.join(_ROOT, "bin")
    if _BIN not in sys.path:
        sys.path.append(_BIN)
    import foundry_paths as _FP
    _setting = _FP.setting
    _head_url = _FP.head_url
except ImportError:
    _setting = lambda name, default="": os.environ.get(name, default)
    _head_url = lambda: (os.environ.get("HAI_URL") or "").rstrip("/")

POLL_S = 1.0            # the items dir is a directory listing on the share, not a model request
DESK_EVERY_S = 30.0     # how often the lead's desk order is asked whether it is still alive (a status, not a deadline)
TERMINAL = ("done", "failed", "cancelled", "expired")
ITEM, OUT, CLOSE, STREAM = ".item.json", ".out.json", "CLOSE", ".stream.jsonl"
OWNER, IDLE, CANCEL = "OWNER.json", "IDLE", ".cancel"
# The factory wants this window to end (a stop slip: a deploy, an operator). The chat's state is saved and the window
# exits. A turn in flight that may be parked (see CUT) is cut and saved mid-answer; any other finishes first. Nothing is
# lost either way. p20_foundry.yield_live_window writes it.
YIELD = "YIELD"
# Parked mid-answer: saved as-is (not an error) and resumable via lab_continue. Only valid for ConvSlots with `conv`; other turns must finish.
CUT = ".cut"
CANCEL_CHECK_S = 1.0   # a Stop or a park is honoured within a second of streaming; one stat of a share file, not per token


class TurnCancelled(Exception):
    """A live turn's caller stopped it (its <id>.cancel marker)."""


class TurnCut(Exception):
    """A live turn was parked mid-answer (CUT): its state is kept and its caller asks it again later."""
OWNER_DOWN_PERIODS = 3  # the deck's reporter rule (SILENT at 2x its declared period, DOWN at 3x)


# A live window yields its card by saving the runner's slot; the next window restores it before answering to read only new tokens (not the whole conversation).
# Slot files are one per model/context, overwritten on save, and retired after restore. Failure is fail-closed: log only, never raise, and mismatched restores fall back to standard reads.
_HAI_DIR = os.path.join(os.environ.get("ProgramData") or r"C:\ProgramData", "HAI")   # the lab's folder on a Windows box
SLOT_DIR = os.path.join(_HAI_DIR, "slots")

# LENDING THE CARD: When McCoy preempts a live window, the window saves its slot and writes LENT. This allows McCoy to use the GPU, while the original window retains its context. Upon resuming, the window must restore its saved slot before processing new tokens to prevent data loss or hallucination. LEND/LENT markers act as a lock to ensure idempotency and prevent double-processing if the preempting process fails. Failure to save or restore is fail-safe: the system falls back to reading the full conversation history, ensuring no data is lost even if the GPU swap fails.
LEND, LENT = "LEND", "LENT"


def slot_file(model, num_ctx):
    return "win-%s-%d.bin" % (re.sub(r"[^A-Za-z0-9.]+", "-", model), int(num_ctx))


def _runner_cmdlines():
    try:
        return subprocess.run(["powershell", "-NoProfile", "-Command",
                               "Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | "
                               "ForEach-Object { $_.CommandLine }"], capture_output=True, text=True, errors="replace",
                              timeout=60).stdout or ""
    except Exception:                                                    # noqa: BLE001
        return ""


def runner_port():
    """ollama's runner port from its command line (one model per card = one runner), or None."""
    ports = re.findall(r"--port (\d+)", _runner_cmdlines())
    return int(ports[0]) if len(ports) == 1 else None


def batch_sizes(cmdline):
    """(n_batch, n_ubatch) off a llama-server command line, its own defaults when a flag is absent. -> the read grid (HotStarts)."""
    b = re.search(r"(?:^|\s)(?:-b|--batch-size)\s+(\d+)", cmdline)
    ub = re.search(r"(?:^|\s)(?:-ub|--ubatch-size)\s+(\d+)", cmdline)
    return int(b.group(1)) if b else 2048, int(ub.group(1)) if ub else 512


def runner_batch():
    """The one runner's read grid, or None when there is not exactly one runner to ask."""
    lines = [l for l in _runner_cmdlines().splitlines() if "--port" in l]
    return batch_sizes(lines[0]) if len(lines) == 1 else None


OLLAMA_LAUNCHERS = (os.path.join(_HAI_DIR, "start-ollama.ps1"),          # a server run under a user account
                    os.path.join(_HAI_DIR, "start-ollama-system.ps1"))   # a server run as SYSTEM: each box has exactly one


def runner_slot_dir(launchers=OLLAMA_LAUNCHERS):
    """Where this box's ollama runners save: the LLAMA_ARG_SLOT_SAVE_PATH line in its launcher, the very line that tells the engine, so
    the window and the engine cannot disagree. A box whose launcher cannot be read keeps the fleet's default."""
    for path in launchers:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                m = re.search(r"LLAMA_ARG_SLOT_SAVE_PATH\s*=\s*['\"]([^'\"\r\n]+)['\"]", f.read())
        except OSError:
            continue
        if m:
            return m.group(1)
    return SLOT_DIR


def window_slot_dir(a):
    """Returns the chat state directory.

    For Strata windows, this reads `slot_save_path` from the serving plan JSON.
    This value is load-bearing for I/O performance: the path MUST point to a
    non-system volume (e.g., a data drive) to prevent disk throttling during
    large session saves. It cannot be derived from the system default."""
    plan = getattr(a, "strata", None)
    if plan:
        try:
            with open(plan, encoding="utf-8") as f:
                folder = json.load(f).get("slot_save_path")
            if folder:
                return folder
        except (OSError, ValueError, TypeError):
            pass
    return runner_slot_dir()


def own_engine(a):
    """The window serves on the engine it was handed (`a.strata` or `a.llama`), not Ollama; both expose the same OpenAI-compatible wire at `a.host` and `/slots/0` API."""
    return bool(getattr(a, "strata", None) or getattr(a, "llama", False))


def engine_port(a, port_fn=runner_port):
    """The port of the engine this window's slot lives on. A window on its own engine knows it (a.host): Strata speaks the
    same /slots/0 API (Strata docs\\DETAILS.md "Session files"; its files are its own format, bound to its version and
    settings, so a mismatch is refused and the turn reads as today), rigby's llama-server is the API's home. An ollama
    window asks for the runner."""
    if own_engine(a):
        return int(str(a.host).rstrip("/").rsplit(":", 1)[1])
    return port_fn()


def _slot_call(port, action, filename, timeout=600, **extra):
    req = urllib.request.Request("http://127.0.0.1:%d/slots/0?action=%s" % (port, action),
                                 data=json.dumps(dict({"filename": filename}, **extra)).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


NEW = ".new"        # a save in progress: the engine writes here and the file replaces the last good one only when it is whole


def save_slot(a, port_fn=runner_port, call=_slot_call, name=None, slot_dir=None):
    """Saves the conversation's read state to the slot directory before yielding the card. Returns the answer or None.
    `name`: The target file (defaults to the window's model + context file). The engine writes to `<name>.new` and only replaces `<name>` upon a finished save to ensure atomicity and prevent data loss from partial writes. The cut-turn sidecar moves with the file; a finished save clears any stale sidecar."""
    target = name or slot_file(a.model, a.num_ctx)
    tmp, folder = target + NEW, None
    try:
        folder = slot_dir or window_slot_dir(a)
        port = engine_port(a, port_fn)
        if not port:
            print("[window] slot not saved: no single ollama runner to save from", flush=True)
            return None
        r = call(port, "save", tmp)
        src, dst = os.path.join(folder, tmp), os.path.join(folder, target)
        if not os.path.exists(src):
            print("[window] slot not saved: the engine wrote no %s in %s (the last good state is kept)" % (tmp, folder),
                  flush=True)
            return None
        os.replace(src, dst)
        if os.path.exists(src + ".labturn"):
            os.replace(src + ".labturn", dst + ".labturn")
        elif os.path.exists(dst + ".labturn"):
            os.remove(dst + ".labturn")
        print("[window] slot saved: %s tokens, %s bytes, %s ms" % (
            r.get("n_saved"), r.get("n_written"), (r.get("timings") or {}).get("save_ms")), flush=True)
        return r
    except Exception as e:                                               # noqa: BLE001 - never worse than today
        for leftover in ((tmp, tmp + ".labturn") if folder else ()):
            _drop(os.path.join(folder, leftover))
        print("[window] slot not saved (%s: %s); the last good state is kept" % (type(e).__name__, str(e)[:160]),
              flush=True)
        return None


def restore_slot(a, port_fn=runner_port, call=_slot_call, slot_dir=SLOT_DIR, name=None, retire=True, lab_continue=False):
    """After the model is resident, before the first turn: put back the slot a yield saved here, once. The file is
    retired either way, so a restore that cannot apply (another context, a newer engine) is not retried forever.
    A chat's own file (ConvSlots, `name`, retire=False) stays: its next save overwrites it, retention ages it out.
    `lab_continue`: the state was saved mid-answer (a parked turn) - ask the engine to go on from the cut when the same
    request is asked next (lab patch #4). An engine without the patch ignores the field: the turn is then just re-asked."""
    name = name or slot_file(a.model, a.num_ctx)
    path = os.path.join(slot_dir, name)
    if not os.path.exists(path):
        return None
    try:
        port = engine_port(a, port_fn)
        if not port:
            print("[window] slot not restored: no single ollama runner", flush=True)
            return None
        r = call(port, "restore", name, lab_continue=True) if lab_continue else call(port, "restore", name)
        print("[window] slot restored: %s tokens in %s ms%s" % (
            r.get("n_restored"), (r.get("timings") or {}).get("restore_ms"),
            ("; the engine %s continue the cut answer" % ("WILL" if r.get("lab_continue_armed") else "will NOT"))
            if lab_continue else ""), flush=True)
        return r
    except Exception as e:                                               # noqa: BLE001
        print("[window] slot not restored (%s: %s)" % (type(e).__name__, str(e)[:160]), flush=True)
        return None
    finally:
        if retire:
            try:
                os.remove(path)
            except OSError:
                pass


# Save/restore the conversation slot under its own file when idle or before a turn switch. Restore first if the file exists; if the prefix doesn't match, fall back to reading current tokens. Each file has a sidecar .json for retention.
CHATS_KEPT_PER_USER = 5      # Count of recent chats retained per user (based on recent usage, not creation)
# Where older chats are archived: a shared store on the array, addressed by the array's share root.
# Empty means no array store, in which case retention keeps every slot in the box's own slot dir.
BIG_SHARED = _setting("BIG_SHARED_UNC", "")
ARRAY_SLOT_DIR = BIG_SHARED + r"\HAI\chat-slots" if BIG_SHARED else ""


def conv_slot_file(model, num_ctx, conv):
    import hashlib
    return "conv-%s-%d-%s.bin" % (re.sub(r"[^A-Za-z0-9.]+", "-", model), int(num_ctx),
                                   hashlib.sha1(str(conv).encode("utf-8")).hexdigest()[:16])


def retain(user, slot_dir=SLOT_DIR, array_dir=None, keep=CHATS_KEPT_PER_USER, box=None):
    """Keep this user's `keep` most recently USED chat slots in the slot dir (the box's NVMe); move the older ones -
    file and sidecar - to the array, never delete them. An unreachable array leaves them where they are, said."""
    import shutil
    array_dir = array_dir or os.path.join(ARRAY_SLOT_DIR, box or socket.gethostname().lower())
    mine = []
    for f in os.listdir(slot_dir) if os.path.isdir(slot_dir) else []:
        if f.startswith("conv-") and f.endswith(".bin.json"):
            try:
                meta = json.load(open(os.path.join(slot_dir, f), encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if meta.get("user") == user:
                mine.append((float(meta.get("t") or 0), f[:-len(".json")]))
    moved = []
    for n, (_t, name) in enumerate(sorted(mine, reverse=True)[keep:]):
        # the array's own folder must already be there: a box that cannot reach it (no mount, no credential) keeps its
        # files, and a path the box cannot read is never created locally (on Linux the Windows share path would be)
        if n == 0 and not os.path.isdir(os.path.dirname(os.path.normpath(array_dir))):
            print("[window] older chat slots of %s stay on this box: the array (%s) is not reachable from here" % (
                user, os.path.dirname(os.path.normpath(array_dir))), flush=True)
            break
        try:
            os.makedirs(array_dir, exist_ok=True)
            for part in (name, name + ".json", name + ".labturn"):   # .labturn: Strata's cut turn (strata_continue_patch)
                if os.path.exists(os.path.join(slot_dir, part)):
                    shutil.move(os.path.join(slot_dir, part), os.path.join(array_dir, part))
            moved.append(name)
        except OSError as e:
            print("[window] chat slot %s stays on this box (array not writable: %s)" % (name, str(e)[:120]), flush=True)
            break
    if moved:
        print("[window] %d older chat slot(s) of %s moved to the array" % (len(moved), user), flush=True)
    return moved


LEGACY_CUT = ".cut"


def retain_newest(slot_dir, prefix, keep):
    """A box with no array to move old chats to (rigby, a Linux box: the array is a Windows share path): keep the `keep`
    newest files under `prefix` with their sidecars, remove the rest. Never raises."""
    try:
        rows = sorted((os.path.getmtime(os.path.join(slot_dir, f)), f) for f in os.listdir(slot_dir)
                      if f.startswith(prefix) and f.endswith(".bin"))
    except OSError:
        return
    for _t, f in rows[:-keep] if keep else rows:
        for part in (f, f + ".json", f + ".labturn", f + LEGACY_CUT):
            _drop(os.path.join(slot_dir, part))


# Fetches older chats from the array only if copy time (size / last MEASURED array read) beats re-read time (tokens / read rate). Aborts copy if projected to lose, capping cost to one chunk over re-read. Handles missing credentials by asking once per window.
ARRAY_READ_FILE = "array-read.json"
FETCH_CHUNK = 8 << 20
FETCH = ".fetch.json"        # Kirk's prefetch ask in the window's items dir (chat_window.prefetch): {"conv", "read_tok_s"}


class _CopyLoses(Exception):
    def __init__(self, rate):
        Exception.__init__(self, rate)
        self.rate = rate


def _array_rate(slot_dir):
    try:
        return float(json.load(open(os.path.join(slot_dir, ARRAY_READ_FILE), encoding="utf-8"))["bytes_s"]) or None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _record_array_rate(slot_dir, rate, clock):
    try:
        _write_json(os.path.join(slot_dir, ARRAY_READ_FILE), {"bytes_s": round(rate, 1), "t": clock()})
    except OSError:
        pass


def fetch_from_array(name, read_tok_s=None, slot_dir=SLOT_DIR, array_dir=None, clock=time.time, chunk=FETCH_CHUNK):
    """Bring a chat's file back from the array when that beats re-reading it -> "back" (restore it), "reread" (the
    turn reads as today), "absent" (the array has no file for it) or "refused" (the array refused this account)."""
    array_dir = array_dir or os.path.join(ARRAY_SLOT_DIR, socket.gethostname().lower())
    src = os.path.join(array_dir, name)
    try:
        size = os.path.getsize(src)
    except FileNotFoundError:
        return "absent"
    except OSError as e:
        print("[window] the array refused this window (%s): older chats here are re-read" % str(e)[:120], flush=True)
        return "refused"
    try:
        n_saved = json.load(open(src + ".json", encoding="utf-8")).get("n_saved")
    except (OSError, ValueError, AttributeError):
        n_saved = None
    reread_s = float(n_saved) / float(read_tok_s) if n_saved and read_tok_s else None
    rate = _array_rate(slot_dir)
    if reread_s is not None and rate and size / rate >= reread_s:
        print("[window] chat %s stays on the array: copying %.0f MB at %.0f MB/s (%.1f s) is slower than re-reading "
              "its %s tokens (%.1f s)" % (name, size / 1e6, rate / 1e6, size / rate, n_saved, reread_s), flush=True)
        return "reread"
    part = os.path.join(slot_dir, name + ".part")
    t0, done = clock(), 0
    try:
        os.makedirs(slot_dir, exist_ok=True)
        with open(src, "rb") as fi, open(part, "wb") as fo:
            while True:
                buf = fi.read(chunk)
                if not buf:
                    break
                fo.write(buf)
                done += len(buf)
                spent = clock() - t0
                if reread_s is not None and done < size and spent > 0 and spent * size / done >= reread_s:
                    raise _CopyLoses(done / spent)
        rate_now = done / max(clock() - t0, 1e-6)
        os.replace(part, os.path.join(slot_dir, name))
    except _CopyLoses as lost:
        _drop(part)
        _record_array_rate(slot_dir, lost.rate, clock)
        print("[window] chat %s stays on the array: the copy ran at %.0f MB/s, slower than re-reading it (%.1f s)" % (
            name, lost.rate / 1e6, reread_s), flush=True)
        return "reread"
    except OSError as e:
        _drop(part)
        print("[window] chat %s could not come back from the array (%s): it is re-read" % (name, str(e)[:120]),
              flush=True)
        return "reread"
    _record_array_rate(slot_dir, rate_now, clock)
    import shutil
    try:                                   # the sidecar comes back too (retention reads it): a move, not a copy
        shutil.move(src + ".json", os.path.join(slot_dir, name + ".json"))
        os.remove(src)
    except OSError:
        pass
    try:                                   # a Strata cut's sidecar, when this chat has one: without it the cut is a plain re-ask
        if os.path.exists(src + ".labturn"):
            shutil.move(src + ".labturn", os.path.join(slot_dir, name + ".labturn"))
    except OSError:
        pass
    print("[window] chat %s back from the array: %.0f MB at %.0f MB/s%s" % (
        name, size / 1e6, rate_now / 1e6, "" if reread_s is None else " (re-reading it: %.1f s)" % reread_s), flush=True)
    return "back"


def _drop(path):
    try:
        os.remove(path)
    except OSError:
        pass


# Hot-start slot files cache a shared prefix so subsequent chats skip re-reading it. The prefix is derived from the engine's rendering: it extends to the longest common text across chats, stopping at the first tool that differs (qwen3.5+ templates may place tools before the system prompt). Files are named by the hash of the prefix to allow sharing. A slot file is only restored if it matches the current prefix; otherwise, a cold read is performed.
#
# The cut point must be a multiple of `n_batch` to ensure the hot start is bit-identical to a cold read. The engine processes reads in batches of `n_batch` from token 0; a misaligned cut causes a batch boundary to shift, altering internal state/rounding and producing different outputs. To capture the state at the cut, the slot save prompt appends a throwaway tail of `min(n_batch, 4 + n_ubatch)` tokens. This forces the engine to create a rollback checkpoint exactly at the cut (where the real prompt diverges) and read the tail, allowing a subsequent real prompt to roll back and continue from the correct grid alignment without generating tokens (`n_predict=0`).
HOT_KEPT = 8                 # per model and size; a start nobody asks for is only disk, and it is rebuilt when asked for
HOT_BUILT = "HOT_BUILT.json"  # the window's own word that it just built one, or why it could not: {"file", "n_saved"} | {"error"}
_BOUNDARY_TOOL = {"type": "function", "function": {"name": "zz_hot_start_boundary", "description": "",
                                                   "parameters": {"type": "object", "properties": {}}}}


def _post_json(url, body, timeout=600):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _head_messages(item):
    """The leading system messages: what every chat of this user on this path opens with."""
    out = []
    for m in item.get("messages") or []:
        if m.get("role") != "system":
            break
        out.append(m)
    return out


def _common_start(x, y):
    n = 0
    for p, q in zip(x, y):
        if p != q:
            break
        n += 1
    return x[:n]


class HotStarts:
    """One window's hot starts (see the block above). Every failure is a log line: a turn then reads as it would today."""

    def __init__(self, a, slot_dir=SLOT_DIR, post=None, save=None, restore=None, port_fn=runner_port, clock=time.time,
                 batch_fn=runner_batch):
        self.a, self.slot_dir, self.post, self.clock, self.port_fn = a, slot_dir, post or _post_json, clock, port_fn
        self.batch_fn = batch_fn
        self.specs = {}          # the shared tools and effort -> their start: the renders are paid once per window
        self.save_fn = save or (lambda name: save_slot(a, name=name, slot_dir=slot_dir))
        self.restore_fn = restore or (lambda name: restore_slot(a, slot_dir=slot_dir, name=name, retire=False))
        self.wanted, self.tried, self.built = None, set(), None

    def _render(self, messages, tools, think, kwargs=None):
        """The engine's own rendering of a prompt, the exact text it would be given: ollama's for an ollama window, the
        llama-server's /apply-template (with the turn's template kwargs) for rigby's."""
        if getattr(self.a, "llama", False):
            body = {"messages": messages}
            if tools:
                body["tools"] = tools
            if kwargs:
                body["chat_template_kwargs"] = kwargs
            return self.post(self.a.host + "/apply-template", body, 60).get("prompt") or ""
        body = {"model": self.a.model, "messages": messages, "stream": False, "_debug_render_only": True,
                "options": {"num_ctx": int(self.a.num_ctx)}}
        if tools:
            body["tools"] = tools
        if think is not None:
            body["think"] = think
        return (self.post(self.a.host + "/api/chat", body, 60).get("_debug_info") or {}).get("rendered_template") or ""

    def _spec(self, item):
        """-> (the file's name, what to feed it) for this item's shared start, or None when it has none to offer."""
        tools, k = item.get("tools") or [], item.get("hot_tools")
        head = _head_messages(item)
        if not tools and not head:
            return None
        if tools and (not isinstance(k, int) or k <= 0):
            return None          # nothing in this list is known to be shared: a start built from it would serve this chat alone
        partial = k < len(tools) if tools else False
        if getattr(self.a, "strata", None):
            kw = item.get("chat_template_kwargs") or {}
            if partial:
                # Strata checkpoints must pin the prefix after the first `k` tools (before system text).
                # Servers without this patch reject the key, so `partial` logic is specific to the patched Strata setup.
                digest = json.dumps(["tools", tools[:k], kw], sort_keys=True, ensure_ascii=False)
                return self._file(digest), {"strata": True, "head": head, "tools": tools, "kwargs": kw,
                                            "prefix": {"tools": k}}
            if not head:
                return None
            digest = json.dumps([head, tools, kw], sort_keys=True, ensure_ascii=False)
            return self._file(digest), {"strata": True, "head": head, "tools": tools, "kwargs": kw}
        think = item["think"] if "think" in item else None
        # rigby's llama-server takes the effort as Kirk sent it (the template's kwargs), ollama as `think`
        kw = (item.get("chat_template_kwargs") or {}) if getattr(self.a, "llama", False) else {}
        ask = [{"role": "user", "content": "x"}]
        if partial:
            # a cut inside the tool list never reaches the system text, so every chat with these tools and this effort has this start
            key = json.dumps([tools[:k], think] + ([kw] if kw else []), sort_keys=True)
            if key in self.specs:
                return self.specs[key]
            one = self._render(head + ask, tools[:k], think, kw)
            two = self._render(head + ask, tools[:k] + [_BOUNDARY_TOOL], think, kw)
            text = _common_start(one, two)
            self.specs[key] = (self._file("on the read grid\n" + text), {"text": text}) if text else None
            return self.specs[key]
        else:
            one = self._render(head + ask, tools, think, kw)
            two = self._render(head + [{"role": "user", "content": "y"}], tools, think, kw)
        text = _common_start(one, two)
        return (self._file("on the read grid\n" + text), {"text": text}) if text else None

    def take_built(self):
        """The window's word on its last build, once (HOT_BUILT.json)."""
        built, self.built = self.built, None
        return built

    def _file(self, digest):
        import hashlib
        return "hot-%s-%d-%s.bin" % (re.sub(r"[^A-Za-z0-9.]+", "-", self.a.model), int(self.a.num_ctx),
                                     hashlib.sha1(digest.encode("utf-8")).hexdigest()[:16])

    def before_new_chat(self, item):
        """A chat with no state on this box is about to turn: restore its shared start when that beats re-reading it, and
        remember to build one when there is none. -> the engine's answer or None."""
        try:
            spec = self._spec(item)
        except Exception as e:                                           # noqa: BLE001 - a hot start never costs a turn
            print("[window] hot start: the shared start could not be read (%s)" % str(e)[:160], flush=True)
            return None
        if not spec:
            return None
        name, feed = spec
        path = os.path.join(self.slot_dir, name)
        if not os.path.exists(path):
            if name not in self.tried:
                self.wanted = (name, feed)
            return None
        try:
            with open(path + ".json", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            meta = {}
        rate, tokens, cost = item.get("read_tok_s"), meta.get("n_shared"), meta.get("restore_s")
        if rate and tokens and cost and float(tokens) / float(rate) <= float(cost):
            print("[window] hot start %s not restored: re-reading %s tokens (%.1f s) is no slower than restoring it (%.1f s)"
                  % (name, tokens, float(tokens) / float(rate), float(cost)), flush=True)
            return None
        t0 = self.clock()
        r = self.restore_fn(name)
        if r:
            meta["restore_s"] = round(self.clock() - t0, 3)
            meta["t"] = self.clock()
            try:
                _write_json(path + ".json", meta)
            except OSError:
                pass
            print("[window] hot start %s: %s tokens already read" % (name, r.get("n_restored")), flush=True)
        return r

    def build_wanted(self):
        """While the card is idle: read the wanted start into the slot and save it. -> True when the slot now holds it, so
        the caller knows the chat it held must be restored before its next turn. Tried once per start per window."""
        if not self.wanted:
            return False
        name, feed = self.wanted
        self.wanted = None
        self.tried.add(name)
        self.built = {"file": name, "error": "not built"}
        try:
            n_shared, grid = None, None
            if feed.get("strata"):
                self.post(self.a.host + "/v1/chat/completions", {
                    "model": self.a.model, "stream": False, "max_tokens": 1, "tools": feed["tools"],
                    "messages": feed["head"] + [{"role": "user", "content": "."}],
                    "strata_prefix": feed.get("prefix") or {"messages": len(feed["head"])},
                    "chat_template_kwargs": feed["kwargs"]})
            else:
                if getattr(self.a, "llama", False):
                    # rigby: the engine Tolkien started, read on the grid its launch set (tolkien_llm.read_grid)
                    port, grid = engine_port(self.a), getattr(self.a, "read_grid", None)
                else:
                    port, grid = self.port_fn(), self.batch_fn()
                if not port or not grid:
                    print("[window] hot start not built: no single ollama runner", flush=True)
                    self.built = {"file": name, "error": "no single ollama runner to read it"}
                    return False
                base = "http://127.0.0.1:%d" % port
                tokens = self.post(base + "/tokenize", {"content": feed["text"], "add_special": True, "parse_special": True},
                                   60).get("tokens") or []
                n_batch, n_ubatch = grid
                n_shared = ((len(tokens) - 1) // n_batch) * n_batch      # keeps tokens[n_shared], so the tail can differ from it
                if n_shared <= 0:
                    print("[window] hot start not built: %d shared tokens is under one read batch (%d)"
                          % (len(tokens), n_batch), flush=True)
                    self.built = {"file": name, "error": "%d shared tokens is under one read batch (%d)" % (len(tokens), n_batch)}
                    return False
                tail = [tokens[0] if tokens[0] != tokens[n_shared] else tokens[1]] * min(n_batch, 4 + n_ubatch)
                # cache_prompt off: from token 0, whatever the slot held, or the batches would start at the shared prefix
                self.post(base + "/completion", {"prompt": tokens[:n_shared] + tail, "n_predict": 0, "cache_prompt": False,
                                                 "stream": False})
            r = self.save_fn(name)
            if not r:
                return True
            n_shared = n_shared or r.get("n_saved")
            _write_json(os.path.join(self.slot_dir, name + ".json"),
                        {"model": self.a.model, "num_ctx": self.a.num_ctx, "n_saved": r.get("n_saved"), "n_shared": n_shared,
                         "read_grid": grid, "t": self.clock()})
            print("[window] hot start %s built: %s shared tokens" % (name, n_shared), flush=True)
            self.built = {"file": name, "n_saved": r.get("n_saved"), "n_shared": n_shared}
            self.retain()
            return True
        except Exception as e:                                           # noqa: BLE001
            print("[window] hot start not built (%s: %s)" % (type(e).__name__, str(e)[:160]), flush=True)
            self.built = {"file": name, "error": "%s: %s" % (type(e).__name__, str(e)[:160])}
            return True          # the slot may have been touched: the chat's own file is restored before its next turn

    def retain(self):
        stem = "hot-%s-%d-" % (re.sub(r"[^A-Za-z0-9.]+", "-", self.a.model), int(self.a.num_ctx))
        rows = []
        for f in os.listdir(self.slot_dir):
            if f.startswith(stem) and f.endswith(".bin"):
                try:
                    rows.append((os.path.getmtime(os.path.join(self.slot_dir, f)), f))
                except OSError:
                    pass
        for _t, f in sorted(rows, reverse=True)[HOT_KEPT:]:
            for part in (f, f + ".json"):
                _drop(os.path.join(self.slot_dir, part))


def start_sig(item):
    """What a chat's state STARTS with, as the engine reads it FIRST: the tool list and the effort (RUNBOOK-Ollama-Host-
    Operations § THE HOT START: "tools render first"; the effort sentence comes before them). The engine reuses a restored
    state only up to the first token that differs from the request, so a state whose tools or effort are not this turn's
    gives back almost nothing. NOT the system text: it renders after the tools, and it differs between the plain and the
    lead path (9,235 against 11,404 characters, 11-04) - a state with this turn's tools is still worth its tool block."""
    import hashlib
    body = [item.get("tools") or [], item.get("think"), item.get("chat_template_kwargs") or {}]
    return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


class ConvSlots:
    """Which conversation the slot holds, and whether it changed since its last save.

    Restore is conditional: if the state's tools/effort signature (start_sig) differs from the
    current turn's, do not restore the state file, as the tools may be incompatible and
    re-reading is safer. A turn parked mid-answer (continue state) must always be restored
    to allow continuation."""

    def __init__(self, a, save=None, restore=None, slot_dir=SLOT_DIR, clock=time.time, retain_fn=None, fetch=None, hot=None,
                 cont=None):
        self.a, self.slot_dir, self.clock = a, slot_dir, clock
        self.save_fn = save or (lambda name: save_slot(a, name=name, slot_dir=slot_dir))
        self.restore_fn = restore or (lambda name: restore_slot(a, slot_dir=slot_dir, name=name, retire=False))
        # a chat whose state was saved mid-answer (a parked turn): restored so the engine goes on from the cut
        self.continue_fn = cont or (lambda name: restore_slot(a, slot_dir=slot_dir, name=name, retire=False,
                                                              lab_continue=True))
        self.cut = None          # the chat whose parked turn this window saved and has not been asked again yet
        self.retain_fn = retain_fn or (lambda user: retain(user, slot_dir=slot_dir))
        self.fetch_fn = fetch or (lambda name, rate: fetch_from_array(name, rate, slot_dir=slot_dir))
        self.current, self.dirty, self.array_ok = None, False, True
        self.hot = hot
        self.last_start = None   # what the last turn started from, for its row: "in the slot", "own", "hot", or None = read all
        self.sig = None          # start_sig of the chat in the slot, as its last turn was asked

    def _sidecar(self, conv):
        try:
            with open(os.path.join(self.slot_dir, self.name(conv) + ".json"), encoding="utf-8") as f:
                meta = json.load(f)
            return meta if isinstance(meta, dict) else {}
        except (OSError, ValueError):
            return {}

    def _why_not_restore(self, meta, item):
        """Why this chat's saved state is NOT worth restoring for this turn, or None. Unknown is None: restore, as before."""
        if meta.get("start") and meta["start"] != start_sig(item):
            return "its tools or effort are not this turn's, and they render first: almost none of it could be reused"
        return None

    def name(self, conv):
        return conv_slot_file(self.a.model, self.a.num_ctx, conv)

    def save_cut(self):
        """A turn parked mid-answer (CUT): save the chat as it stands NOW - the answer so far and the engine's pending
        token - whatever `dirty` says -> the engine's answer or None. The sidecar says `cut`, so whichever window
        restores this file next restores it to CONTINUE."""
        if not self.current:
            return None
        self.dirty = True
        r = self.save_current("a turn parked mid-answer", cut=True)
        self.cut = self.current if r else None
        return r

    def _was_cut(self, conv):
        """The chat's file on disk was saved mid-answer (its sidecar says so, or the mark rigby's own window left before
        it ran this program). Unreadable = no: it restores as before."""
        path = os.path.join(self.slot_dir, self.name(conv))
        try:
            with open(path + ".json", encoding="utf-8") as f:
                return bool(json.load(f).get("cut"))
        except (OSError, ValueError, AttributeError):
            return os.path.exists(path + LEGACY_CUT)

    @staticmethod
    def _started(r, cut):
        if not r:
            return None
        return "own, continuing the cut answer" if cut and r.get("lab_continue_armed") else "own"

    def save_current(self, why, cut=False):
        """Save the chat in the slot if it changed since its last save -> the engine's answer or None."""
        if not self.current or not self.dirty:
            return None
        r = self.save_fn(self.name(self.current))
        if r:
            user, _sep, chat = self.current.partition("/")
            try:
                _write_json(os.path.join(self.slot_dir, self.name(self.current) + ".json"),
                            {"conv": self.current, "user": user, "chat": chat, "model": self.a.model,
                             "num_ctx": self.a.num_ctx, "t": self.clock(), "n_saved": r.get("n_saved"), "why": why,
                             "cut": bool(cut), "start": self.sig})
            except OSError:
                pass
            _drop(os.path.join(self.slot_dir, self.name(self.current) + LEGACY_CUT))   # the sidecar says it now
            self.dirty = False
            try:
                self.retain_fn(user)
            except Exception as e:                                       # noqa: BLE001 - retention never breaks a turn
                print("[window] chat slot retention failed (%s)" % type(e).__name__, flush=True)
        return r

    def before(self, item):
        """Before a turn: a different chat -> save the outgoing one (if changed), restore the incoming one (if saved).
        A turn with NO chat (a one-off call) overwrites the slot too, so it counts as a different one."""
        conv = item.get("conv") or None
        parked_here, self.cut = bool(conv) and self.cut == conv, None
        if conv and conv == self.current and not parked_here:
            self.last_start = "in the slot"
            return None
        if conv and conv == self.current:
            # this chat's own parked turn, asked again while its state is still in the slot: the engine continues only
            # from a RESTORE that asks it to, so the file this window just saved is put back
            r = self.continue_fn(self.name(conv))
            self.last_start = self._started(r, True)
            return r
        self.last_start = None
        self.save_current("another chat is next" if conv else "a call outside any chat is next")
        self.current, self.dirty = conv, False
        if not conv:
            return None
        here = os.path.exists(os.path.join(self.slot_dir, self.name(conv)))
        if not here and item.get("state_here") and self.array_ok:
            got = self.fetch_fn(self.name(conv), item.get("read_tok_s"))     # an older chat: back from the array?
            self.array_ok = got != "refused"
            here = got == "back"
        if here:
            cut = self._was_cut(conv)            # saved mid-answer by any window: restore it to continue
            meta = self._sidecar(conv)
            why = None if cut else self._why_not_restore(meta, item)
            if why:
                print("[window] chat state of %s not restored: %s" % (conv, why), flush=True)
            else:
                r = (self.continue_fn if cut else self.restore_fn)(self.name(conv))
                self.last_start = self._started(r, cut)
                return r
        r = self.hot.before_new_chat(item) if self.hot is not None else None   # no state of its own: the shared start
        self.last_start = "hot" if r else None
        return r

    def build_hot_start(self):
        """Idle, the chat's own state saved: build the shared start this window was asked for. The slot then holds that,
        so the chat in it is restored from its own file before its next turn."""
        if self.hot is None or self.dirty or not self.hot.wanted:
            return False
        if self.hot.build_wanted():
            self.current = None
            return True
        return False

    def after(self, item):
        if item.get("conv"):
            self.current, self.dirty = item["conv"], True
            self.sig = start_sig(item)

    def prefetch_asked(self, items_dir):
        """Pre-load FETCH file states from the array before the turn begins. This allows the subsequent restore step to update state in-place rather than re-fetching or copying data, keeping the critical path efficient."""
        try:
            names = sorted(f for f in os.listdir(items_dir) if f.endswith(FETCH))
        except OSError:
            return []
        out = []
        for f in names:
            path = os.path.join(items_dir, f)
            try:
                with open(path, encoding="utf-8") as fh:
                    ask = json.load(fh)
            except (OSError, ValueError):
                continue                                 # shown early by the share: whole on the next poll
            _drop(path)
            conv = ask.get("conv") if isinstance(ask, dict) else None
            if not conv or not self.array_ok:
                continue
            name = self.name(conv)
            if os.path.exists(os.path.join(self.slot_dir, name)):
                got = "here"
            else:
                got = self.fetch_fn(name, ask.get("read_tok_s"))
                self.array_ok = got != "refused"
            print("[window] prefetch for %s: %s" % (conv, got), flush=True)
            out.append((conv, got))
        return out

    def ensure_saved(self, why):
        """Save if changed; if the chat's state is already on disk (saved when it went idle), say so -> truthy or None."""
        r = self.save_current(why)
        if r:
            return r
        if self.current and os.path.exists(os.path.join(self.slot_dir, self.name(self.current))):
            return {"n_saved": None, "already_saved": True}
        return None

    def restore_current(self):
        """Taking the card back after a lend: the chat the lend saved."""
        if self.current and os.path.exists(os.path.join(self.slot_dir, self.name(self.current))):
            return self.restore_fn(self.name(self.current))
        return None


def _write_json(path, body):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False)
    os.replace(tmp, path)


def lend_card(a, items_dir, save=None, resident=None):
    """LEND asked while this window is idle: save the slot (only if this window's model is the one on the card - a slot
    saved from another runner would be restored into the wrong conversation) and answer LENT. -> True once lent."""
    if not os.path.exists(os.path.join(items_dir, LEND)) or os.path.exists(os.path.join(items_dir, LENT)):
        return False
    if own_engine(a):
        _write_json(os.path.join(items_dir, LENT), {"t": time.time(), "refused": "a Strata window runs its own engine"
                                                    if getattr(a, "strata", None) else "this window runs its own engine"})
        return False
    if not os.path.exists(os.path.join(items_dir, IDLE)):
        return False                                    # a run is using the window: the lend waits for it to finish
    try:
        # Only check residency via /api/ps; avoid the heavy spill read as saving waits for the slot to go idle anyway.
        here = (resident or (lambda: IO._residency(a.host, a.model)[0] is not None))()
    except Exception:                                                    # noqa: BLE001
        here = False
    r = (save or save_slot)(a) if here else None
    _write_json(os.path.join(items_dir, LENT), {"t": time.time(), "saved": bool(r), "n_saved": (r or {}).get("n_saved"),
                                                "why": "" if here else "this window's model was not on the card"})
    print("[window] card lent (%s)" % ("slot saved" if r else "nothing saved"), flush=True)
    return True


IDLE_SAMPLE_S = 3       # the idle-power sample a measurement window has always taken before serving


def idle_gap(rows, t_open, t_close, span=IDLE_SAMPLE_S):
    """Returns the first quiet IDLE_SAMPLE_S stretch of a live window (start, end), or None. This source is used for the live window's idle-power reading instead of a pre-serve sleep."""
    edges = [t_open] + [x for r in sorted((r for r in rows if r.get("t0") and r.get("t1")), key=lambda r: r["t0"])
                        for x in (r["t0"], r["t1"])] + [t_close]
    for g0, g1 in zip(edges[0::2], edges[1::2]):
        if g1 - g0 >= span:
            return g0, g0 + span
    return None


def reclaim_card(a, items_dir, load=None, restore=None):
    """The first item after a lend: this window's model back on the card, then the slot the lend saved, then both
    markers gone. A load that cannot fit exits rc 2 as at the window's start, and the foundry holds the window."""
    if load:
        load(a.host, a.model, a.num_ctx)
    else:
        IO._assert_resident(a.host, a.model, a.num_ctx, offload_ok=getattr(a, "offload_ok", False))   # as at the start
    (restore or restore_slot)(a)
    for m in (LENT, LEND):
        try:
            os.remove(os.path.join(items_dir, m))
        except OSError:
            pass
    print("[window] card taken back", flush=True)


def desk_status_fn(kirk, lead_id):
    """() -> the lead order's desk status; "" when the desk cannot be asked (never rendered as a verdict)."""
    if not lead_id:
        return None

    def status():
        headers = {}
        try:
            import p20_foundry                      # deployed beside this file in bin
            tok = p20_foundry._worker_token()
            if tok:
                headers["X-P20-Token"] = tok
        except Exception:                                                # noqa: BLE001
            pass
        try:
            req = urllib.request.Request("%s/p20/order/%s" % (kirk.rstrip("/"), lead_id), headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                return str((json.loads(r.read().decode("utf-8") or "{}")).get("status") or "")
        except Exception:                                                # noqa: BLE001
            return ""
    return status


def card_work_waiting_fn(kirk, host):
    """() -> True when the desk holds GPU work this box could take now (GET /p20/jobs?host=: read-only, claims
    nothing); None when the desk cannot be asked."""
    def waiting():
        headers = {}
        try:
            import p20_foundry
            tok = p20_foundry._worker_token()
            if tok:
                headers["X-P20-Token"] = tok
        except Exception:                                                # noqa: BLE001
            pass
        try:
            req = urllib.request.Request("%s/p20/jobs?host=%s" % (kirk.rstrip("/"), host), headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                jobs = (json.loads(r.read().decode("utf-8") or "{}")).get("jobs") or []
        except Exception:                                                # noqa: BLE001
            return None
        return any(((j.get("spec") or {}).get("load") or "gpu") != "cpu" for j in jobs)
    return waiting


SWAP_LOCAL = "http://127.0.0.1:11435"   # llama-swap's daily Flash-Next serving (RUNBOOK-Ollama-Host-Operations § llama-swap)
# A Strata engine does not unload itself the way ollama does after keep_alive: an idle Strata window ends after the
# daily engine's own idle rule - llama-swap's `ttl: 300` = the box's OLLAMA_KEEP_ALIVE=5m (RUNBOOK § llama-swap).
STRATA_IDLE_TTL_S = 300


def strata_plan(spec, model, num_ctx):
    """`auto` -> this box's serving plan for `model` from strata_served.json (the file the box's model.<name> capability
    is declared from), refusing (rc 2) when it does not serve that model at this context; a path passes through."""
    if spec != "auto":
        return spec
    import p20_foundry
    try:
        with open(p20_foundry.STRATA_SERVED, encoding="utf-8") as f:
            row = (json.load(f).get(p20_foundry.FLEET_HOST) or {}).get(model) or {}
    except (OSError, ValueError):
        row = {}
    if not row.get("plan") or int(row.get("ctx") or 0) != int(num_ctx):
        print("REFUSED (rc 2): this box's strata_served.json has no plan for %s at context %s" % (model, num_ctx),
              flush=True)
        raise SystemExit(2)
    return row["plan"]


def free_swap(base=SWAP_LOCAL):
    """llama-swap's daily engine lets go of the card before this window's own engine starts - the rule Kirk applies
    before every ollama call on the box (ollama_client._free_swap): one GET /running, an unload only when something
    runs. llama-swap not running = nothing to free."""
    try:
        with urllib.request.urlopen(base + "/running", timeout=5) as r:
            running = (json.loads(r.read().decode("utf-8") or "{}")).get("running") or []
    except Exception:                                                    # noqa: BLE001
        return
    if running:
        req = urllib.request.Request(base + "/api/models/unload", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=180) as r:
            r.read()
        print("[window] llama-swap unloaded %s before this window's engine" % running, flush=True)


def strata_chat(a):
    """A turn on the window's own engine (Strata, or rigby's llama-server): the openai wire (infer_ollama._chat_openai),
    a Strata plan's vendor sampler under the turn's own options, no num_ctx / num_predict (the engine serves the context
    it was launched at; rigby's own window sent none either)."""
    def chat(host, model, messages, options, think=False, tools=None, on_chunk=None, keep_alive=None):
        opts = dict(getattr(a, "strata_sampler", None) or {})
        opts.update({k: v for k, v in (options or {}).items() if k not in ("num_ctx", "num_predict")})
        return IO._chat_openai(a.host, messages, opts, think=think, tools=tools, model=model,
                               **({"on_chunk": on_chunk} if on_chunk else {}))
    return chat


own_engine_chat = strata_chat      # the name rigby's caller uses: the same turn on any OpenAI-wire engine the window holds


def _fleet_host():
    """The desk's name for this box - the factory's own constant, not the OS hostname."""
    import p20_foundry                      # deployed beside this file in bin
    return p20_foundry.FLEET_HOST


def owner_verdict(items_dir, clock=time.time, live=False):
    """Returns the verdict for a live owner's heartbeat: '' (alive/no owner), a reason to close, or None if unreadable. A --live window with no owner file is considered abandoned and closes."""
    path = os.path.join(items_dir, OWNER)
    if not os.path.exists(path):
        return "a live window with no owner heartbeat" if live else ""
    try:
        with open(path, encoding="utf-8") as f:
            o = json.load(f)
        age, period = clock() - float(o["t"]), float(o["period"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if age > OWNER_DOWN_PERIODS * period:
        return "owner %s silent %.0f s (declared period %.0f s)" % (o.get("run", "?"), age, period)
    return ""


def answer_item(a, item, meter, chat=None, stream_path=None, can_cut=False):
    """One item -> one row, the shape infer_ollama writes (plus gpu_j when the meter samples live). `stream_path` =
    a live turn: every chunk is relayed to that file as it arrives (module docstring). `can_cut`: this window keeps the
    turn's chat state, so the turn may be PARKED mid-answer (CUT) - its row then says `cut` and carries no error."""
    chat = chat or IO._chat
    sf = None
    if stream_path:
        sf = open(stream_path, "w", encoding="utf-8")       # a re-answered item starts its stream over
        sf.write(json.dumps({"attempt": uuid.uuid4().hex, "id": item.get("id")}) + "\n")
        sf.flush()
        cancel = stream_path[:-len(STREAM)] + CANCEL
        cut = stream_path[:-len(STREAM)] + CUT
        yielding = os.path.join(os.path.dirname(stream_path), YIELD)
        checked = [0.0]
        relayed = [0]

        def relay(chunk):
            sf.write(json.dumps(chunk, ensure_ascii=False) + "\n")
            sf.flush()
            relayed[0] += 1
            # a user's Stop (Kirk writes <id>.cancel): ending the stream here closes the engine's connection, and
            # ollama abandons a generation whose client is gone - the card is free for the next turn
            if time.time() - checked[0] >= CANCEL_CHECK_S:
                checked[0] = time.time()
                if os.path.exists(cancel):
                    raise TurnCancelled("the turn was stopped by its caller")
                # a park: the same hang-up, but the state is kept and the turn is asked again later (CUT)
                if can_cut and os.path.exists(cut):
                    raise TurnCut("parked by its caller")
                if can_cut and item.get("cut_ok") and os.path.exists(yielding):
                    raise TurnCut("the factory asked for the card")
    try:
        row = _answer(a, item, meter, chat, relay if sf else None)
    finally:
        if sf is not None:
            sf.close()
    if sf is not None:
        # how many chunks the stream file holds: a reader over a caching mount (Kirk, CIFS) knows it has them all even
        # when the turn ended without the engine's done chunk (a loop cut, a Stop)
        row["stream_chunks"] = relayed[0]
    return row


def _answer(a, item, meter, chat, relay):
    options = {"num_ctx": int(a.num_ctx), "num_predict": int(a.num_ctx)}
    if a.temperature is not None:
        options["temperature"] = float(a.temperature)
    if a.seed_base:
        options["seed"] = int(a.seed_base) + 1         # = infer_ollama --rounds 1: seed_base + round
    options.update(item.get("options") or {})
    # an ERRAND turn (Phase 7 "Two shapes of delegation") carries the whole conversation and the tools it may call;
    # the reply's tool calls go back to the lead's harness, which runs them beside the workspace
    turn = item.get("messages") or [{"role": "user", "content": item.get("prompt") or ""}]
    messages = ([{"role": "system", "content": item["system"]}] if item.get("system") else []) + list(turn)
    row = {"id": item.get("id"), "round": 1, "seed": options.get("seed"), "answer": "", "thinking": "",
           "served_model": None, "done_reason": None, "prompt_eval_count": None, "eval_count": None,
           "wall_s": None, "error": ""}
    # a LIVE turn names its own effort and model (Kirk's effort is per call). An effort label this window cannot send as
    # itself would reach ollama as a bare true = MEDIUM on qwen3.8 (the 10-07 effort sandbag), and a model the window does
    # not hold is another job on the card: both are refused as row errors, never coerced
    if "think" not in item and isinstance(item.get("chat_template_kwargs"), dict):
        # a Strata turn arrives with Kirk's chat_template_kwargs (ollama_client.swap_think_kwargs): read back its effort
        kw = item["chat_template_kwargs"]
        item = dict(item, think=(False if kw.get("enable_thinking") is False
                                 else kw.get("reasoning_effort") if kw.get("enable_thinking") else True))
    think = item["think"] if "think" in item else a.think
    if isinstance(think, str) and think not in IO.THINK_LEVELS:
        row["error"] = "effort %r is not one this window sends as itself (%s)" % (think, ", ".join(IO.THINK_LEVELS))
        return row
    if item.get("model") and item["model"] != a.model:
        row["error"] = "this window holds %s, the turn asked for %s" % (a.model, item["model"])
        return row
    if "num_ctx" in (item.get("options") or {}) and int(item["options"]["num_ctx"]) != int(a.num_ctx):
        row["error"] = "this window was placed at num_ctx %s, the turn asked for %s" % (a.num_ctx,
                                                                                     item["options"]["num_ctx"])
        return row
    t0 = time.time()
    try:
        resp = chat(a.host, a.model, messages, options, think=think,
                    **({"tools": item["tools"]} if item.get("tools") else {}),
                    **({"on_chunk": relay} if relay else {}),
                    **({"keep_alive": item["keep_alive"]} if item.get("keep_alive") else {}))
        msg = resp.get("message") or {}
        if msg.get("tool_calls"):
            row["tool_calls"] = msg["tool_calls"]
        row.update({"answer": msg.get("content") or "", "thinking": msg.get("thinking") or "",
                    "served_model": resp.get("model"), "done_reason": resp.get("done_reason"),
                    "prompt_eval_count": resp.get("prompt_eval_count"), "eval_count": resp.get("eval_count"),
                    "eval_duration": resp.get("eval_duration"),
                    # the READ time: ollama's prompt_eval_count is the whole prompt even when the cache (or a
                    # restored slot, B11b) served most of it - only the duration says what was actually read
                    "prompt_eval_s": round(resp["prompt_eval_duration"] / 1e9, 3)
                    if resp.get("prompt_eval_duration") else None})
        if row["eval_count"] and row["eval_duration"]:
            row["tok_s"] = round(row["eval_count"] / (row["eval_duration"] / 1e9), 1)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:                                                # noqa: BLE001
            body = ""
        row["error"] = "HTTP %s: %s" % (e.code, body or e.reason)
    except TurnCut as e:
        row["cut"] = str(e)              # parked, not failed: the caller asks this turn again and the engine continues it
    except Exception as e:                                               # noqa: BLE001
        # the probe speaks the engine's own wire: OpenAI for a window on its own engine (Strata, rigby), ollama otherwise
        if IO._silence(e) and not IO._engine_alive(a.host, a.model, a.num_ctx, strata=own_engine(a)):
            print("REFUSED (rc 2): engine hung - item %s got no token for %ds and a 4-token probe of %s got nothing; "
                  "the window is held for a human (restart ollama), answered items kept" % (
                      item.get("id"), IO.IDLE_S, a.model), flush=True)
            raise SystemExit(2)
        row["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
    row["t0"], row["t1"] = round(t0, 2), round(time.time(), 2)
    row["wall_s"] = round(row["t1"] - t0, 1)
    # a live meter (nvidia-smi samples as they come) prices the row now, so the lead reads the joules with the answer;
    # the AMD telemetry log is only readable at stop: those rows are priced at close (None here, never 0)
    row["gpu_j"] = meter.joules(row["t0"], row["t1"]) if meter.source and meter.source != "telemetry-csv" else None
    return row


def serve(a, items_dir, prog_dir, partial, meter, desk_status=None, sleep=time.sleep, clock=time.time, chat=None,
          card_waiting=None, idle_ttl=None, conv_slots=None, poll_s=POLL_S, on_row=None):
    """Answer items as they appear until CLOSE, until the lead's order is no longer alive, until a live owner falls
    silent, or - while that owner waits on its user - until card work waits at the desk. Returns (rows, why).
    `poll_s`: how often the items dir is listed (the box's own measured choice: rigby polls its share every 0.2 s);
    `on_row(row)`: after each answered row (rigby's caller beats its work clock there). Never raises from on_row."""
    rows, answered = [], set()
    last_desk = clock()
    os.makedirs(prog_dir, exist_ok=True)
    # RESUME (the factory's yield contract: a window ended by Plex demand or a lost card restarts in the same
    # {ORDER_OUT}): rows already answered are kept from the partial file, their out files are already beside the items
    if os.path.exists(partial):
        for line in open(partial, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue                                # a line cut by the stop is not a result
            if isinstance(r, dict) and r.get("id") and not r.get("error"):
                rows.append(r)
                answered.add(str(r["id"]))
        if rows:
            print("[window] resuming: %d answered item(s) kept from %s" % (len(rows), os.path.basename(partial)), flush=True)
        with open(partial, "rb") as f:               # a line the stop cut has no newline: start the next row on its own
            f.seek(0, os.SEEK_END)                   # line, or the two merge and BOTH are lost to the next resume
            if f.tell() and (f.seek(-1, os.SEEK_END) or True) and f.read(1) != b"\n":
                with open(partial, "a", encoding="utf-8") as pf0:
                    pf0.write("\n")
    lent = False
    with open(partial, "a", encoding="utf-8") as pf:
        while True:
            if os.path.exists(os.path.join(items_dir, CLOSE)):
                return rows, "closed by the lead"
            try:
                names = sorted(f for f in os.listdir(items_dir) if f.endswith(ITEM))
            except OSError:
                names = []                              # the share hiccuped: ask again next poll
            pending = [f for f in names if f[:-len(ITEM)] not in answered
                       and not os.path.exists(os.path.join(items_dir, f[:-len(ITEM)] + OUT))]
            if not pending and os.path.exists(os.path.join(items_dir, YIELD)):
                return rows, "yielded between turns: the factory asked (a deploy or a stop)"
            if not pending and not lent and card_waiting is not None:
                lent = lend_card(a, items_dir,           # live windows only: Kirk lends its own idle window's card
                                 save=(lambda _a: conv_slots.ensure_saved("card lent")) if conv_slots else None)
            if pending and lent:
                reclaim_card(a, items_dir, restore=(lambda _a: conv_slots.restore_current()) if conv_slots else None)
                lent = False
            if pending:
                served = 0
                for f in pending:
                    iid = f[:-len(ITEM)]
                    try:
                        with open(os.path.join(items_dir, f), encoding="utf-8") as fh:
                            item = json.load(fh)
                    except (OSError, ValueError):
                        continue                        # shown early by the share: whole on the next poll
                    served += 1
                    item.setdefault("id", iid)
                    if os.path.exists(os.path.join(items_dir, iid + CANCEL)):
                        # If a CANCEL marker exists, the turn was stopped before starting. Skip engine restoration to avoid consuming the single continue token for a dead attempt that will be overwritten by the active re-ask.
                        row = {"id": item.get("id"), "round": 1, "answer": "", "thinking": "", "served_model": None,
                               "done_reason": None, "wall_s": 0.0,
                               "error": "TurnCancelled: the turn was stopped by its caller before the window started it"}
                        _write_json(os.path.join(items_dir, iid + OUT), row)
                        rows.append(row)
                        answered.add(iid)
                        pf.write(json.dumps(row, ensure_ascii=False) + "\n")
                        pf.flush()
                        open(os.path.join(prog_dir, iid + ".done"), "w").close()
                        print("[window] %d %s cancelled before it started: not sent to the engine" % (len(rows), iid),
                              flush=True)
                        continue
                    t_state = clock()
                    if conv_slots is not None:
                        conv_slots.before(item)          # a different chat: save the outgoing, restore the incoming
                    state_s = clock() - t_state
                    row = answer_item(a, item, meter, chat=chat,
                                      stream_path=os.path.join(items_dir, iid + STREAM) if item.get("stream") else None,
                                      can_cut=conv_slots is not None and bool(item.get("conv")))
                    if conv_slots is not None:
                        conv_slots.after(item)
                        row["state_s"] = round(state_s, 3)   # what saving, restoring and rendering cost before this turn's engine call
                        if conv_slots.last_start:
                            row["state_from"] = conv_slots.last_start
                        if row.get("cut"):
                            # parked mid-answer: the state is saved as it stands (the engine finishes the few tokens it was
                            # writing first - the save waits for the slot), so the same request asked again continues it
                            sv = conv_slots.save_cut()
                            row["cut_saved"] = bool(sv)
                            if sv:
                                row["cut_tokens"], row["cut_mid_answer"] = sv.get("n_saved"), sv.get("pending_token") is not None
                            print("[window] turn %s parked mid-answer (%s): state %s" % (
                                iid, row["cut"], ("saved, %s tokens" % sv.get("n_saved")) if sv else "NOT saved - a re-ask "
                                "starts the answer over"), flush=True)
                    _write_json(os.path.join(items_dir, iid + OUT), row)
                    rows.append(row)
                    answered.add(iid)
                    pf.write(json.dumps(row, ensure_ascii=False) + "\n")
                    pf.flush()
                    open(os.path.join(prog_dir, iid + ".done"), "w").close()
                    if on_row is not None:
                        try:
                            on_row(row)
                        except Exception:                                # noqa: BLE001 - a beat never costs a turn
                            pass
                    print("[window] %d %s %s %.1fs%s%s" % (
                        len(rows), iid, row["served_model"] or "?", row["wall_s"],
                        (" %.1f tok/s" % row["tok_s"]) if row.get("tok_s") else "",
                        (" ERROR " + row["error"]) if row["error"] else ""), flush=True)
                    if row.get("cut") and os.path.exists(os.path.join(items_dir, YIELD)):
                        # the factory wants the card and the turn in flight is parked: go NOW. The caller asks that turn
                        # again at once; answering it here would park it again on its first chunk, for ever
                        return rows, "yielded between turns: the factory asked, and the turn in flight was parked mid-answer"
                if served:
                    continue                            # look again at once; a half-written item waits one poll
            if conv_slots is not None and not pending:
                conv_slots.prefetch_asked(items_dir)     # the user is typing in an older chat: bring its state back now
            if conv_slots is not None and not pending and conv_slots.dirty \
                    and os.path.exists(os.path.join(items_dir, IDLE)):
                # the run waits on its USER (Kirk's IDLE, never mid tool call): save the chat now, off anyone's critical
                # path and while the model is still loaded - the window's own idle end coincides with ollama's unload
                conv_slots.save_current("idle: the chat waits on its user")
            if conv_slots is not None and not pending and not lent and os.path.exists(os.path.join(items_dir, IDLE)):
                conv_slots.build_hot_start()             # the same quiet moment, once per shared start
                hot = getattr(conv_slots, "hot", None)
                said = hot.take_built() if hot is not None else None
                if said:
                    try:
                        _write_json(os.path.join(items_dir, HOT_BUILT), said)
                    except OSError:
                        pass
            # Heartbeat and idle marks must be read on every pass; relying on the desk's 30s timer delays window state updates.
            silent = owner_verdict(items_dir, clock, live=card_waiting is not None)
            if silent:
                return rows, silent
            if idle_ttl and not pending:
                try:
                    idle_s = clock() - os.path.getmtime(os.path.join(items_dir, IDLE))
                except OSError:
                    idle_s = None                        # not idle (a run is using it), or could not tell
                if idle_s is not None and idle_s > idle_ttl:
                    return rows, "idle %.0f s, past the engine's own idle rule (%d s)" % (idle_s, idle_ttl)
            if clock() - last_desk >= DESK_EVERY_S:      # the two questions that go to Kirk over the network
                last_desk = clock()
                if desk_status is not None:
                    st = desk_status()
                    if st in TERMINAL:
                        return rows, "the lead's order is %s" % st
                if (card_waiting is not None and os.path.exists(os.path.join(items_dir, IDLE))
                        and not pending and card_waiting() is True):
                    return rows, "yielded between turns: card work is waiting at the desk"
            sleep(poll_s)


def idle_rule(a):
    """TTL source by engine: Strata uses its own TTL; ollama uses the box's keep_alive (--idle-ttl) so the window releases when the model unloads. Ollama windows must never default to holding a cold card as busy."""
    if not getattr(a, "live", False):
        return None
    if getattr(a, "strata", None):
        return STRATA_IDLE_TTL_S
    ttl = getattr(a, "idle_ttl", None)
    return ttl if ttl and ttl > 0 else None


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("items_dir")
    ap.add_argument("out")
    ap.add_argument("--model", required=True)
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--num-ctx", type=int, default=8192)
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--seed-base", type=int, default=0)
    ap.add_argument("--think", action="store_true")
    ap.add_argument("--think-level", choices=IO.THINK_LEVELS, default=None,
                    help="thinking ON at this effort (infer_ollama's rule); a placed window's policy row sends it")
    ap.add_argument("--lead-order", default=None, help="the lead's desk record id: the window closes when it ends")
    ap.add_argument("--kirk", default=_head_url())
    ap.add_argument("--live", action="store_true", help="a HAI run's window: yield between user turns to waiting card work")
    ap.add_argument("--offload-ok", action="store_true",
                    help="the model runs partially resident ON PURPOSE (menu_fill.OFFLOAD_OK, carried in policy.json): "
                         "the window accepts the planned split instead of refusing it, as infer_ollama --offload-ok does")
    ap.add_argument("--idle-ttl", type=int, default=None, metavar="SECONDS",
                    help="an ollama live window ends when it has been idle this long - the box's own keep_alive, sent by "
                         "Kirk (chat_window), so a window never holds a card after the engine has unloaded its model "
                         "(#34 design s 4 a, 2026-10-23; Strata windows keep their engine's own idle rule)")
    ap.add_argument("--strata", default=None, metavar="PLAN.json|auto",
                    help="serve the model on Strata (Flash-Next): `auto` = this box's strata_served.json row for --model; "
                         "the window frees llama-swap, starts its own engine through strata_arm's one lifecycle (daily "
                         "use: no frontier mode) and stops it by any route")
    a, _extra = ap.parse_known_args(argv)                # the runner appends the order name; ignore it
    if a.think_level:
        a.think = a.think_level
    os.makedirs(a.items_dir, exist_ok=True)
    box = socket.gethostname().lower()
    print("[window] %s model=%s num_ctx=%d temp=%s items_dir=%s lead_order=%s strata=%s" % (
        box, a.model, a.num_ctx, a.temperature, a.items_dir, a.lead_order or "-", a.strata or "-"), flush=True)
    if a.live:
        # Live windows with no owner or closed ones must exit before engine load to prevent orphaned Strata processes or stale requeues after deploys.
        gone = "it already closed" if os.path.exists(os.path.join(a.items_dir, CLOSE)) else owner_verdict(a.items_dir, live=True)
        if gone:
            print("[window] not starting: %s" % gone, flush=True)
            try:
                open(os.path.join(a.items_dir, CLOSE), "w").close()
            except OSError:
                pass
            return 0
    server, chat = None, None
    if a.strata:
        a.strata = strata_plan(a.strata, a.model, a.num_ctx)            # rc 2 -> held: this box cannot serve it now
        free_swap()
        server, placement = IO._strata_up(a, frontier=False)            # sets a.host to the engine's own port
        chat = strata_chat(a)
    else:
        placement = IO._assert_resident(a.host, a.model, a.num_ctx, offload_ok=a.offload_ok)
        if a.live:
            restore_slot(a, slot_dir=window_slot_dir(a))                # B11b: the conversation a yield saved here
    meter = IO.PowerMeter()
    idle = (time.time(), None)
    if meter.source and not a.live:
        # Measurement windows sample idle power before serving, but live chat windows must not add latency to the first token; they capture idle readings in the gap between turns. (The 3s sleep was removed from the live path to reduce TTFB.)
        time.sleep(IDLE_SAMPLE_S)
        idle = (idle[0], time.time())
    prog_dir = a.out[:-5] + ".progress" if a.out.endswith(".json") else a.out + ".progress"
    partial = a.out + ".partial.jsonl"
    t_open = time.time()
    slots = window_slot_dir(a)
    conv_slots = ConvSlots(a, slot_dir=slots, hot=HotStarts(a, slot_dir=slots)) if a.live else None  # chat quick resume + hot start
    try:
        rows, why = serve(a, a.items_dir, prog_dir, partial, meter, desk_status_fn(a.kirk, a.lead_order), chat=chat,
                          card_waiting=card_work_waiting_fn(a.kirk, _fleet_host()) if a.live else None,
                          idle_ttl=idle_rule(a), conv_slots=conv_slots)
        # (a lent card was saved at the lend and now holds another model: saving its runner would be the wrong slot)
        if not os.path.exists(os.path.join(a.items_dir, LENT)):
            if conv_slots is not None and conv_slots.current:
                conv_slots.save_current("window closed: " + why)       # the chat in the slot, under its own name
            elif why.startswith("yielded between turns"):
                save_slot(a)                                            # B11b: before the waiting work takes the card
    finally:
        if server is not None and server[0] is not None:
            server[0].stop(server[1], server[2])         # the engine ends with the window, by ANY route (never raises)
        try:
            meter.stop()
        except Exception:                                                # noqa: BLE001
            pass
        # by ANY route the window ends, it says so in its items dir: a client waiting on the share (the Terminal-Bench
        # relay) then stops waiting instead of hanging on answers that will never come (10-10: a stopped order left
        # Harbor and the relay running on Sundae)
        try:
            close = os.path.join(a.items_dir, CLOSE)
            if not os.path.exists(close):
                open(close, "w").close()
        except OSError:
            pass
    for r in rows:
        if r.get("gpu_j") is None and meter.source and r.get("t0") and r.get("t1"):
            r["gpu_j"] = meter.joules(r["t0"], r["t1"])
    if meter.source and not idle[1]:
        idle = idle_gap(rows, t_open, time.time()) or idle
    idle_j = meter.joules(*idle) if meter.source and idle[1] else None
    energy = {"source": meter.source, "samples": len(meter.samples),
              "idle_w": round(idle_j / (idle[1] - idle[0]), 1) if idle_j is not None else None,
              # a row the meter never reached (a window cancelled or a card lost before its turn ended) has no gpu_j:
              # the window's out.json is still written, never lost to a KeyError on the way out
              "gpu_j_total": round(sum(r["gpu_j"] for r in rows if r.get("gpu_j") is not None), 1)
                             if any(r.get("gpu_j") is not None for r in rows) else None,
              "rows_metered": sum(1 for r in rows if r.get("gpu_j") is not None)}
    outd = {"host": box, "model": a.model, "served": next((r["served_model"] for r in rows if r.get("served_model")), None),
            "num_ctx": a.num_ctx, "temperature": a.temperature, "placement": placement, "window": True,
            "closed_by": why, "open_s": round(time.time() - t_open, 1), "rows": rows,
            "summary": {"items": len(rows), "rounds": 1, "rows": len(rows),
                        "errors": sum(1 for r in rows if r["error"]), "priced_out": 0,
                        "wall_mean_s": round(sum(r["wall_s"] or 0 for r in rows) / max(1, len(rows)), 1),
                        "energy": energy}}
    _write_json(a.out, outd)
    print("[window] closed (%s): %d item(s) answered in %.0f s open" % (why, len(rows), outd["open_s"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
