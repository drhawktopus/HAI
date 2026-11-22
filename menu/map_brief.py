"""map_brief.py - a Fellowship tablet answers ONE free-text brief (S10c: the lead on rigby delegates, a tablet drafts).

Runs ON a tablet under the foundry map contract:
    python map_brief.py items.txt out.json

Unlike the task-battery payloads (map_swarm_code*, map_chat_swarm), the work is not a named task from a built-in
table: each item CARRIES its brief, base64-encoded so the single-line item contract holds whatever the lead wrote
(newlines, semicolons, pipes). The answer comes back whole in `answer` - the lead, not this payload, judges it.

Item contract:
    arm=brief;id=<id>;seed=<n>;model=<gguf basename>;ctx=<n>;b64=<base64 of the brief, UTF-8>
    arm=control;id=control;seed=1;model=<same>;ctx=<same>;b64=<base64 of CONTROL_PROMPT>

`model` is any gguf the Fellowship store serves (stage_model pulls it), `ctx` is the ORDER's stated context for the
helper (the team block's helper_ctx; the GPU helpers run at their tested max the same way). Control: the known-answer
prompt below; the sentinel BRIEF_CONTROL_PASS is emitted at row["control_result"] only when the gold token matched,
and redacted case-insensitively from every answer and error (the foundry lowercases the row JSON before matching).
Staging and guards are the chat payload's (map_chat_swarm.py). The llama-cli call has NO clock: it ends when the
process's CPU time stops moving for IDLE_S or its output loops
a long answer is the helper's answer, never a timeout. Rows carry `finish_reason` ("stop" | "loop")."""
import base64
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zlib

LLM_DIR = os.environ.get("SWARM_LLM_DIR", os.path.expanduser("~/llm"))
STAGE_URL = os.environ.get("SWARM_STAGE_URL",
                           os.environ.get("FELLOW_STAGE_URL", "")).rstrip("/")   # the factory passes it
LLAMA_CLI = os.environ.get("SWARM_LLAMA_CLI", os.path.join(LLM_DIR, "llama-cli"))

ARM_BRIEF = "brief"
ARM_CONTROL = "control"
CONTROL_ID = "control"
CONTROL_PROMPT = "What is 2 + 2? Answer with only the number."
CONTROL_GOLD = "4"
CONTROL_SENTINEL = "BRIEF_CONTROL_PASS"

TEMPERATURE = 0.6
TOKEN_LIMIT = -1  # generate until model EOS (no artificial truncation)
THREADS = min(os.cpu_count() or 4, 8)  # per-tablet: class-C has 4 cores, class-A/B 8
# No clock on the answer (): generation ends when llama-cli's CPU time stops moving or its output loops
# tool-payload rule, same constants as infer_ollama.looping / tolkien_llm.looping.
IDLE_S = 120          # CPU time frozen this long = llama-cli has stopped working (not a budget on the answer)
IDLE_POLL_S = 5
LOOP_WINDOW, LOOP_RATIO = 4000, 0.10

REQUIRED_ARCH = "aarch64"
KEEP_FREE_MB = 500

_ITEM_KEY_ORDER = ("arm", "id", "seed", "model", "ctx", "b64")


def encode_brief(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def item_line(arm, item_id, seed, model, ctx, text):
    """The ONE spelling of an item, shared with the lead's delegator (a1_fellowship imports it): producer and
    consumer cannot drift. Raises on a value that could not round-trip."""
    for name, v in (("arm", arm), ("id", item_id), ("model", model)):
        if not str(v).strip() or any(c in str(v) for c in ";=|\n\r"):
            raise ValueError("bad %s for an item line: %r" % (name, v))
    line = "arm=%s;id=%s;seed=%d;model=%s;ctx=%d;b64=%s" % (arm, item_id, int(seed), model, int(ctx), encode_brief(text))
    parsed = parse_item(line)
    if parsed[-1] is not None:
        raise ValueError("item line does not parse back: %s" % parsed[-1])
    return line


def control_line(model, ctx):
    return item_line(ARM_CONTROL, CONTROL_ID, 1, model, ctx, CONTROL_PROMPT)


def parse_item(line):
    """Return (arm, id, seed, model, ctx, text, error). On failure the values are None and error is bad_item:<why>."""
    bad = (None,) * 6
    if "|" in line or "\n" in line or "\r" in line:
        return bad + ("bad_item:forbidden_char",)
    parts = line.split(";")
    if len(parts) != len(_ITEM_KEY_ORDER):
        return bad + ("bad_item:wrong_field_count",)
    d = {}
    for i, part in enumerate(parts):
        if "=" not in part:
            return bad + ("bad_item:missing_equals",)
        k, v = part.split("=", 1)
        if k != _ITEM_KEY_ORDER[i]:
            return bad + ("bad_item:wrong_key_or_order",)
        d[k] = v
    try:
        seed, ctx = int(d["seed"]), int(d["ctx"])
    except ValueError:
        return bad + ("bad_item:non_integer",)
    if seed <= 0:
        return bad + ("bad_item:non_positive_seed",)
    if ctx <= 0:
        return bad + ("bad_item:non_positive_ctx",)
    if not d["model"] or "/" in d["model"] or "\\" in d["model"]:
        return bad + ("bad_item:bad_model",)
    if not d["id"]:
        return bad + ("bad_item:empty_id",)
    try:
        text = base64.b64decode(d["b64"], validate=True).decode("utf-8")
    except Exception:                                                   # noqa: BLE001
        return bad + ("bad_item:undecodable_brief",)
    if not text.strip():
        return bad + ("bad_item:empty_brief",)
    if d["arm"] == ARM_CONTROL:
        if d["id"] != CONTROL_ID or text != CONTROL_PROMPT:
            return bad + ("bad_item:unknown_control",)
    elif d["arm"] != ARM_BRIEF:
        return bad + ("bad_item:unknown_arm",)
    return (d["arm"], d["id"], seed, d["model"], ctx, text, None)


# ---------------------- staging + guards: map_chat_swarm.py's, unchanged

def _meminfo_kb():
    d = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, _, rest = line.partition(":")
            v = rest.strip().split()
            if v and v[0].isdigit():
                d[k] = int(v[0])
    return d


def _free_mb(path=None):
    p = path or LLM_DIR
    try:
        st = os.statvfs(p)
        return st.f_bavail * st.f_frsize / (1024 * 1024)
    except OSError:
        return None


def check_arch():
    m = platform.machine()
    if m != REQUIRED_ARCH:
        return "unsupported_arch:%s" % m
    return None


def ensure_llm_dir():
    try:
        os.makedirs(LLM_DIR, exist_ok=True)
        return None
    except OSError as e:
        return "cannot_create_llm_dir:%s:%s" % (type(e).__name__, e)


def _remote_mb(model):
    try:
        req = urllib.request.Request("%s/%s.gguf" % (STAGE_URL, model), method="HEAD")
        with urllib.request.urlopen(req, timeout=20) as r:
            cl = r.headers.get("Content-Length")
            return int(cl) / (1024 * 1024) if cl else None
    except Exception:
        return None


def _evict_pulled(need_mb):
    try:
        pulled = [f for f in os.listdir(LLM_DIR)
                  if f.endswith(".gguf") and os.path.exists(os.path.join(LLM_DIR, f + ".pulled"))]
    except FileNotFoundError:
        return
    pulled.sort(key=lambda f: os.path.getsize(os.path.join(LLM_DIR, f)), reverse=True)
    for f in pulled:
        free = _free_mb()
        if free is not None and free >= need_mb:
            break
        for x in (f, f + ".pulled"):
            try:
                os.remove(os.path.join(LLM_DIR, x))
            except OSError:
                pass


def stage_model(model):
    path = os.path.join(LLM_DIR, model + ".gguf")
    if os.path.exists(path):
        return path, None
    if not STAGE_URL:
        return None, "staging_failed:no_stage_url"
    need = _remote_mb(model)
    if need is None:
        return None, "staging_failed:model_not_in_store"
    free = _free_mb()
    if free is not None and free < need + KEEP_FREE_MB:
        _evict_pulled(need + KEEP_FREE_MB)
    free = _free_mb()
    if free is not None and free < need + KEEP_FREE_MB:
        return None, "staging_failed:model_too_big_for_node"
    tmp = path + ".part"
    try:
        with urllib.request.urlopen("%s/%s.gguf" % (STAGE_URL, model), timeout=1200) as r, \
                open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1024 * 1024)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None, "staging_failed:transfer_failed"
    if os.path.exists(tmp) and os.path.getsize(tmp) > 1_000_000:
        os.replace(tmp, path)
        try:
            open(path + ".pulled", "w").close()
        except OSError:
            pass
        return path, None
    try:
        os.remove(tmp)
    except OSError:
        pass
    return None, "staging_failed:transfer_short"


def _cpu_ticks(pid):
    try:
        with open("/proc/%d/stat" % pid) as f:
            fields = f.read().rsplit(")", 1)[1].split()
        return int(fields[11]) + int(fields[12])      # utime + stime
    except Exception:                                                   # noqa: BLE001
        return None


def looping(tail):
    """The last two LOOP_WINDOW chars both compress below LOOP_RATIO (digits normalised): the model is repeating."""
    if len(tail) < 2 * LOOP_WINDOW:
        return False

    def r(s):
        b = re.sub(r"\d+", "0", s).encode("utf-8", "replace")
        return len(zlib.compress(b, 9)) / len(b)
    return r(tail[-LOOP_WINDOW:]) < LOOP_RATIO and r(tail[-2 * LOOP_WINDOW:-LOOP_WINDOW]) < LOOP_RATIO


def _output_tail(out_path):
    """The last 2 * LOOP_WINDOW chars written so far; NUL padding (merry/pippin while the model loads) dropped."""
    try:
        with open(out_path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 64 * LOOP_WINDOW))
            return f.read().replace(b"\x00", b"").decode("utf-8", "replace")[-2 * LOOP_WINDOW:]
    except OSError:
        return ""


def _run_until_idle(argv, env, cwd, out_path):
    """Run argv with stdout to out_path; end it only when its CPU time stops moving for IDLE_S, or its output loops.
    Returns (returncode or None, None | "stalled" | "loop")."""
    with open(out_path, "wb") as out:
        proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                env=env, cwd=cwd)
        last, still_since = None, time.time()
        while proc.poll() is None:
            time.sleep(IDLE_POLL_S)
            if looping(_output_tail(out_path)):
                proc.kill()
                proc.wait()
                return None, "loop"
            ticks = _cpu_ticks(proc.pid)
            if ticks is None or ticks != last:
                last, still_since = ticks, time.time()
            elif time.time() - still_since >= IDLE_S:
                proc.kill()
                proc.wait()
                return None, "stalled"
        return proc.returncode, None


def run_llama(model_basename, prompt, seed, ctx, prompt_path):
    """Argv-based llama-cli invocation, ended by idleness or a loop, never by a clock.
    Returns (stdout_or_None, wall_s, error_or_None, finish) with finish in "stop" | "loop"."""
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt)
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LLM_DIR
    argv = [
        LLAMA_CLI,
        "-m", os.path.join(LLM_DIR, model_basename + ".gguf"),
        "-f", prompt_path,
        "-st", "--no-display-prompt",
        "--temp", str(TEMPERATURE),
        "--seed", str(seed),
        "-n", str(TOKEN_LIMIT),
        "-c", str(ctx),
        "-t", str(THREADS),
    ]
    t0 = time.time()
    out_path = prompt_path + ".out"
    try:
        rc, why = _run_until_idle(argv, env, LLM_DIR, out_path)
    except FileNotFoundError:
        return None, round(time.time() - t0, 1), "llama_cli_missing", "stop"
    wall = round(time.time() - t0, 1)
    with open(out_path, "rb") as f:
        stdout = f.read().decode(errors="replace")
    if why == "stalled":
        return None, wall, "generation_stalled", "stop"
    if why == "loop":
        # the model's answer is what it wrote before the cut: close it the way llama-cli would, so the boundary holds
        stdout += LLAMA_CLI_STATS + " cut: output looping ]\n"
    return stdout, wall, None, ("loop" if why == "loop" else "stop")


_SENTINEL_ANY_CASE_RE = re.compile(re.escape(CONTROL_SENTINEL), re.IGNORECASE)


def _redact_sentinel(text):
    return _SENTINEL_ANY_CASE_RE.sub("[REDACTED_SENTINEL]", text) if text else text


def _base_row(item, arm, item_id, seed, model, ctx):
    return {"item": item, "arm": arm, "id": item_id, "task": item_id, "replicate": 0, "seed": seed, "model": model,
            "ctx": ctx, "temperature": TEMPERATURE, "token_limit": TOKEN_LIMIT,
            "ran": False, "ok": False, "checks_passed": 0, "checks_total": 0, "wall_s": 0.0, "error": "", "answer": ""}


def _fail_row(item, arm, item_id, seed, model, ctx, err, wall=0.0):
    row = _base_row(item, arm, item_id, seed, model, ctx)
    row["error"], row["wall_s"] = err, wall
    return row


def _bad_item_row(item, err):
    return _fail_row(item, "", "", 0, "", 0, err)


# ---------------------- Answer boundary (generated from cli_answer.py - measured; see its docstring)
LLAMA_CLI_ECHO_LIMIT = 500
LLAMA_CLI_TRUNCATED = ' ... (truncated)\n'
LLAMA_CLI_STATS = '\n\n[ Prompt:'


def llama_cli_answer(stdout, prompt):
    end = stdout.rfind(LLAMA_CLI_STATS)
    if end < 0:
        return None, "answer_boundary:stats_line_missing"
    echo = "\n> " + prompt[:LLAMA_CLI_ECHO_LIMIT]
    n = stdout.count(echo, 0, end)
    if n != 1:
        return None, "answer_boundary:prompt_echo_found_%d_times" % n
    i = stdout.find(echo) + len(echo)
    tail = LLAMA_CLI_TRUNCATED if len(prompt) > LLAMA_CLI_ECHO_LIMIT else "\n"
    if not stdout.startswith(tail, i):
        return None, "answer_boundary:prompt_echo_end_unrecognised"
    return stdout[i + len(tail):end].strip("\n"), ""


def mtmd_answer(stdout):
    answer = stdout.strip()
    if not answer:
        return None, "answer_boundary:empty_stdout"
    return answer, ""



def process_item(item, prompt_path, work_dir):
    arm, item_id, seed, model, ctx, text, perr = parse_item(item)
    if perr is not None:
        return _bad_item_row(item, perr)
    for check in (check_arch, ensure_llm_dir):
        e = check()
        if e:
            return _fail_row(item, arm, item_id, seed, model, ctx, e)
    _path, staging_err = stage_model(model)
    if staging_err:
        return _fail_row(item, arm, item_id, seed, model, ctx, staging_err)
    # llama-cli trims a trailing newline from its echo, so a prompt ending in one never matches the measured boundary
    prompt = text.rstrip("\n")
    stdout, wall, err, finish = run_llama(model, prompt, seed, ctx, prompt_path)
    if err is not None:
        return _fail_row(item, arm, item_id, seed, model, ctx, err, wall=wall)
    # the answer is cut at the MEASURED boundary (cli_answer.py, embedded below) and stored whole; an unrecognised
    # output shape is an instrument failure (error "answer_boundary:..."), never a model failure, raw stdout kept
    answer, aerr = llama_cli_answer(stdout, prompt)
    if aerr:
        row = _fail_row(item, arm, item_id, seed, model, ctx, aerr, wall=wall)
        row["raw_stdout"] = _redact_sentinel(stdout)
        return row
    row = _base_row(item, arm, item_id, seed, model, ctx)
    # a looping answer is the model's answer to this brief, cut where it started repeating - recorded, never hidden
    row.update(wall_s=wall, ran=True, answer=_redact_sentinel(answer), finish_reason=finish)
    if arm == ARM_CONTROL:
        hit = re.search(r"\b" + re.escape(CONTROL_GOLD) + r"\b", answer) is not None
        row.update(checks_passed=1 if hit else 0, checks_total=1, ok=hit, gold=CONTROL_GOLD,
                   error="" if hit else "no_gold_match")
        if hit:
            row["control_result"] = CONTROL_SENTINEL
    else:
        ok = bool(answer.strip())
        row.update(checks_passed=1 if ok else 0, checks_total=1, ok=ok, error="" if ok else "empty_answer")
    return row


def _atomic_write_json(rows, path):
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    os.replace(tmp, path)


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 3:
        sys.stderr.write("usage: map_brief.py items.txt out.json\n")
        return 2
    items_path, out_path = argv[1], argv[2]
    work_dir = LLM_DIR if os.path.isdir(LLM_DIR) else os.getcwd()
    prompt_path = os.path.join(work_dir, "_brief_prompt.txt")
    with open(items_path, encoding="utf-8") as f:
        items = [line.strip() for line in f if line.strip()]
    rows = []
    for item in items:
        try:
            rows.append(process_item(item, prompt_path, work_dir))
        except Exception as e:                                          # noqa: BLE001
            rows.append(_bad_item_row(item, "unhandled:%s:%s" % (type(e).__name__, e)))
    _atomic_write_json(rows, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
