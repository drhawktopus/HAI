"""map_chat_swarm.py — Phase 5 chat/reasoning swarm payload.

Runs ON a Fellowship tablet under the foundry map contract:
    python map_chat_swarm.py items.txt out.json

Same seed/replicate/model contract as map_swarm_code.py; task battery + scoring
is the CHAT division lifted from fellow_gauntlet.py sha256
    10aadccf9722f59825224799a67005356b61a5a22505dfb4909ec4415c7733b6
(short factual/reasoning questions scored by whole-word gold-token match, case
insensitive). Model held constant at the Phase-4 general/tool champion:
Qwen2.5-1.5B-Instruct-Q4_K_M.

Item contract:
    arm=chat-swarm;task=<name>;replicate=<n>;seed=<n>;model=Qwen2.5-1.5B-Instruct-Q4_K_M
    arm=control;task=control_chat;replicate=0;seed=1;model=<same>

Control sentinel CHAT_CONTROL_PASS is emitted at row["control_result"] IFF the
row is the control row AND the gold-token match succeeded. Case-insensitive
redaction on answer + error fields (foundry lowercases the row JSON before
substring-matching the expect string — tablet_fulfil.py line 330).
"""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request

LLM_DIR = os.environ.get("SWARM_LLM_DIR", os.path.expanduser("~/llm"))
STAGE_URL = os.environ.get("SWARM_STAGE_URL",
                           os.environ.get("FELLOW_STAGE_URL", "")).rstrip("/")
LLAMA_CLI = os.environ.get("SWARM_LLAMA_CLI", os.path.join(LLM_DIR, "llama-cli"))

MODEL_NAME_FIXED = "Qwen2.5-1.5B-Instruct-Q4_K_M"
ARM_EXPERIMENT = "chat-swarm"
ARM_CONTROL = "control"
CONTROL_TASK = "control_chat"
CONTROL_SENTINEL = "CHAT_CONTROL_PASS"

TEMPERATURE = 0.6
TOKEN_LIMIT = -1  # generate until model EOS
CONTEXT = 2048
THREADS = min(os.cpu_count() or 4, 8)  # per-tablet: class-C has 4 cores, class-A/B 8
GENERATION_TIMEOUT_S = 400

REQUIRED_ARCH = "aarch64"
KEEP_FREE_MB = 500

# --- 8 chat tasks (lifted verbatim from gauntlet CHAT_TASKS, minus the CTRL_ entry).
# Gold tokens are matched case-insensitively as whole words: "3" won't match "30".
TASKS = {
    "cap_france": {"prompt": "What is the capital of France? Answer with only the city name.", "gold": "paris"},
    "cap_japan":  {"prompt": "What is the capital of Japan? Answer with only the city name.",  "gold": "tokyo"},
    "math_add":   {"prompt": "What is 47 + 58? Answer with only the number.",                   "gold": "105"},
    "math_mul":   {"prompt": "What is 12 times 12? Answer with only the number.",               "gold": "144"},
    "antonym_hot":{"prompt": "What is the opposite of the word 'hot'? Answer with one word.",   "gold": "cold"},
    "plural_mouse":{"prompt":"What is the plural of the word 'mouse' (the animal)? One word.", "gold": "mice"},
    "seq_next":   {"prompt": "What number comes next: 2, 4, 6, 8, ...? Answer with only the number.", "gold": "10"},
    "elem_symbol":{"prompt": "What is the chemical symbol for gold? Answer with only the symbol.", "gold": "au"},
}
EXPERIMENT_TASK_NAMES = tuple(TASKS.keys())

CONTROL_TASKS = {
    "control_chat": {"prompt": "What is the capital of Italy? Answer with only the city name.", "gold": "rome"},
}

_ITEM_KEY_ORDER = ("arm", "task", "replicate", "seed", "model")


def parse_item(line):
    if "|" in line or "\n" in line or "\r" in line:
        return (None, None, None, None, None, "bad_item:forbidden_char")
    parts = line.split(";")
    if len(parts) != 5:
        return (None, None, None, None, None, "bad_item:wrong_field_count")
    d = {}
    for i, part in enumerate(parts):
        if "=" not in part:
            return (None, None, None, None, None, "bad_item:missing_equals")
        k, v = part.split("=", 1)
        if k != _ITEM_KEY_ORDER[i]:
            return (None, None, None, None, None, "bad_item:wrong_key_or_order")
        d[k] = v
    try:
        replicate = int(d["replicate"])
        seed = int(d["seed"])
    except ValueError:
        return (None, None, None, None, None, "bad_item:non_integer")
    if replicate < 0:
        return (None, None, None, None, None, "bad_item:negative_replicate")
    if seed <= 0:
        return (None, None, None, None, None, "bad_item:non_positive_seed")
    if seed != replicate + 1:
        return (None, None, None, None, None, "bad_item:seed_not_replicate_plus_one")
    if d["model"] != MODEL_NAME_FIXED:
        return (None, None, None, None, None, "bad_item:model_not_fixed")
    arm, task = d["arm"], d["task"]
    if arm == ARM_EXPERIMENT:
        if task not in TASKS:
            return (None, None, None, None, None, "bad_item:unknown_experiment_task")
    elif arm == ARM_CONTROL:
        if task != CONTROL_TASK:
            return (None, None, None, None, None, "bad_item:unknown_control_task")
    else:
        return (None, None, None, None, None, "bad_item:unknown_arm")
    return (arm, task, replicate, seed, d["model"], None)


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


def check_ram():
    """No-op — the previous MemAvailable/SwapFree thresholds were arbitrary and
    rejected healthy tablets under normal load pressure. Kept
    as a hook so a future real signal (OOM kill, explicit tablet health beacon)
    can slot in without a schema change."""
    return None


def check_disk():
    return None  # pre-flight no-op; stage_model does reactive eviction


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


def run_llama(model_basename, prompt, seed, prompt_path):
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
        "-c", str(CONTEXT),
        "-t", str(THREADS),
    ]
    t0 = time.time()
    try:
        r = subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL,
                           env=env, cwd=LLM_DIR, timeout=GENERATION_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None, round(time.time() - t0, 1), "generation_timeout"
    except FileNotFoundError:
        return None, round(time.time() - t0, 1), "llama_cli_missing"
    wall = round(time.time() - t0, 1)
    return r.stdout.decode(errors="replace"), wall, None


def isolate(out, prompt):
    """Trim llama-cli chatter and the re-echoed prompt tail from stdout.
    Lifted verbatim from fellow_gauntlet.py."""
    for mk in ("[ Prompt:", "\nExiting", "\n> /", "\n> \n"):
        i = out.find(mk)
        if i != -1:
            out = out[:i]
    plines = [l.rstrip() for l in prompt.splitlines() if l.strip()]
    if plines:
        idx = out.rfind(plines[-1])
        if idx != -1:
            out = out[idx + len(plines[-1]):]
    return out.strip()


def score_chat(answer, gold):
    """Whole-word case-insensitive match, per gauntlet's chat scoring."""
    hit = re.search(r"\b" + re.escape(gold) + r"\b", answer, re.I) is not None
    return (1, 1, "") if hit else (0, 1, "no_gold_match")


_ROW_KEYS_REQUIRED = (
    "item", "arm", "task", "replicate", "seed", "model", "temperature", "token_limit",
    "ran", "ok", "checks_passed", "checks_total", "wall_s", "error", "answer",
)


def _base_row(item, arm, task, replicate, seed, model):
    return {
        "item": item, "arm": arm, "task": task, "replicate": replicate, "seed": seed,
        "model": model, "temperature": TEMPERATURE, "token_limit": TOKEN_LIMIT,
        "ran": False, "ok": False, "checks_passed": 0, "checks_total": 0,
        "wall_s": 0.0, "error": "", "answer": "",
    }


def _fail_row(item, arm, task, replicate, seed, model, err, wall=0.0):
    row = _base_row(item, arm, task, replicate, seed, model)
    row["error"] = err
    row["wall_s"] = wall
    return row


def _bad_item_row(item, err):
    row = _base_row(item, "", "", 0, 0, "")
    row["error"] = err
    return row


_SENTINEL_ANY_CASE_RE = re.compile(re.escape(CONTROL_SENTINEL), re.IGNORECASE)


def _redact_sentinel(text):
    if not text:
        return text
    return _SENTINEL_ANY_CASE_RE.sub("[REDACTED_SENTINEL]", text)


def process_item(item, prompt_path, work_dir):
    arm, task, replicate, seed, model, perr = parse_item(item)
    if perr is not None:
        return _bad_item_row(item, perr)

    e = check_arch()
    if e:
        return _fail_row(item, arm, task, replicate, seed, model, e)
    e = check_ram()
    if e:
        return _fail_row(item, arm, task, replicate, seed, model, e)
    e = ensure_llm_dir()
    if e:
        return _fail_row(item, arm, task, replicate, seed, model, e)
    e = check_disk()
    if e:
        return _fail_row(item, arm, task, replicate, seed, model, e)

    _path, staging_err = stage_model(model)
    if staging_err:
        return _fail_row(item, arm, task, replicate, seed, model, staging_err)

    tdef = CONTROL_TASKS[task] if arm == ARM_CONTROL else TASKS[task]
    prompt = tdef["prompt"] + "\n"
    stdout, wall, err = run_llama(model, prompt, seed, prompt_path)
    if err is not None:
        return _fail_row(item, arm, task, replicate, seed, model, err, wall=wall)

    answer = isolate(stdout, prompt)
    passed, total, serr = score_chat(answer, tdef["gold"])
    row = _base_row(item, arm, task, replicate, seed, model)
    row["wall_s"] = wall
    row["ran"] = True
    row["checks_passed"] = passed
    row["checks_total"] = total
    row["ok"] = (total > 0 and passed == total)
    row["error"] = _redact_sentinel(serr)
    row["answer"] = _redact_sentinel(answer[:400])
    row["gold"] = tdef["gold"]

    if arm == ARM_CONTROL and row["ok"] and passed == total and total > 0:
        row["control_result"] = CONTROL_SENTINEL

    return row


def _atomic_write_json(rows, path):
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    os.replace(tmp, path)


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 3:
        sys.stderr.write("usage: map_chat_swarm.py items.txt out.json\n")
        return 2
    items_path, out_path = argv[1], argv[2]
    work_dir = LLM_DIR if os.path.isdir(LLM_DIR) else os.getcwd()
    prompt_path = os.path.join(work_dir, "_swarm_prompt.txt")

    with open(items_path, encoding="utf-8") as f:
        items = [line.strip() for line in f if line.strip()]

    rows = []
    for item in items:
        try:
            rows.append(process_item(item, prompt_path, work_dir))
        except Exception as e:
            rows.append(_bad_item_row(item, "unhandled:%s:%s" % (type(e).__name__, e)))

    _atomic_write_json(rows, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
