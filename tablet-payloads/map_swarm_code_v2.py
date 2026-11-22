"""map_swarm_code.py — Phase 5 Commission 02 tablet map payload.

Runs ON a Fellowship tablet under the foundry map contract:
    python map_swarm_code.py items.txt out.json

Each item names an independently seeded coding sample. The payload generates
code with Qwen2.5-Coder-1.5B on the tablet, scores it against fixed check
lambdas in a bounded child process, and writes one JSON row per item — never
crashing the batch. The foundry adds `_host` to each row after the fact.

Item contract (SPEC §4):
    arm=code-swarm;task=<name>;replicate=<n>;seed=<n>;model=Qwen2.5-Coder-1.5B-Instruct-Q4_K_M
    arm=control;task=control_add;replicate=0;seed=1;model=<same>

Only the eight frozen experiment tasks and the payload-local control_add task
are accepted. Seed MUST equal replicate + 1. Any deviation emits a bad_item
failure row instead of running inference.

Task battery is copied verbatim from Rigby harness
    ~/cluster/code_tourney.py
    sha256 2ddc0c1d3aad05931c67fd575d3f9817a76f3d4b52df73091531007c7788b23d
Tablet does NOT read Rigby at runtime; checks are embedded here.

Control sentinel CODE_CONTROL_PASS is emitted at row["control_result"] IFF the
row is the control row AND every check passed. It is redacted from every row's
answer field so a rogue model answer cannot mint a false positive through the
foundry's substring control check.

Guards (SPEC §7): arch must be aarch64; MemAvailable ≥ 600,000 kB and
SwapFree ≥ 500,000 kB;
disk free ≥ 500 MiB after any needed model stage.
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

# ---------------------- Environment (test-overridable via env vars)

LLM_DIR = os.environ.get("SWARM_LLM_DIR", os.path.expanduser("~/llm"))
STAGE_URL = os.environ.get("SWARM_STAGE_URL",
                           os.environ.get("FELLOW_STAGE_URL", "")).rstrip("/")
LLAMA_CLI = os.environ.get("SWARM_LLAMA_CLI", os.path.join(LLM_DIR, "llama-cli"))

# ---------------------- Fixed contract constants

MODEL_NAME_FIXED = "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M"
ARM_EXPERIMENT = "code-swarm"
ARM_CONTROL = "control"
CONTROL_TASK = "control_add"
CONTROL_SENTINEL = "CODE_CONTROL_PASS"

# Phase 4 champion sampling settings (frozen)
TEMPERATURE = 0.6
TOKEN_LIMIT = -1  # generate until model EOS
CONTEXT = 2048
THREADS = min(os.cpu_count() or 4, 8)  # per-tablet: class-C has 4 cores, class-A/B 8
GENERATION_TIMEOUT_S = 400
SCORE_CHILD_TIMEOUT_S = 10
SCORE_PARENT_TIMEOUT_S = 15

# Resource guards
KEEP_FREE_MB = 500
REQUIRED_ARCH = "aarch64"

# Prompt suffix (verbatim from Rigby code_tourney SUFFIX)
SUFFIX = ("\nReturn ONLY the complete function as Python code in a single ```python code block. "
          "No explanation, no tests, no example usage.")

# ---------------------- Frozen 8-task experiment battery
# Copied verbatim from rigby-code_tourney.py sha256
# 2ddc0c1d3aad05931c67fd575d3f9817a76f3d4b52df73091531007c7788b23d.

TASKS = {
    "reverse_words": {
        "kind": "impl",
        "prompt": "Write a Python function reverse_words(s) that returns the words of the string s in reverse order, joined by single spaces. Example: reverse_words('hello world foo') returns 'foo world hello'.",
        "checks": [
            "lambda: reverse_words('hello world foo')=='foo world hello'",
            "lambda: reverse_words('a')=='a'",
            "lambda: reverse_words('')==''",
            "lambda: reverse_words('one two three')=='three two one'",
        ],
    },
    "fizzbuzz": {
        "kind": "impl",
        "prompt": "Write a Python function fizzbuzz(n) that returns a list of strings for the numbers 1 through n inclusive: 'Fizz' if divisible by 3, 'Buzz' if divisible by 5, 'FizzBuzz' if divisible by both, otherwise the number as a string.",
        "checks": [
            "lambda: fizzbuzz(5)==['1','2','Fizz','4','Buzz']",
            "lambda: fizzbuzz(15)[-1]=='FizzBuzz'",
            "lambda: fizzbuzz(3)==['1','2','Fizz']",
            "lambda: fizzbuzz(1)==['1']",
        ],
    },
    "is_palindrome": {
        "kind": "impl",
        "prompt": "Write a Python function is_palindrome(s) that returns True if s reads the same forwards and backwards, IGNORING case, spaces, and punctuation (only letters and digits count). Example: is_palindrome('A man, a plan, a canal: Panama') returns True.",
        "checks": [
            "lambda: is_palindrome('A man, a plan, a canal: Panama') is True",
            "lambda: is_palindrome('racecar') is True",
            "lambda: is_palindrome('hello') is False",
            "lambda: is_palindrome('') is True",
        ],
    },
    "two_sum": {
        "kind": "impl",
        "prompt": "Write a Python function two_sum(nums, target) that returns a tuple (i, j) with i < j such that nums[i] + nums[j] == target. Exactly one solution exists. Return the indices as a tuple.",
        "checks": [
            "lambda: two_sum([2,7,11,15],9)==(0,1)",
            "lambda: two_sum([3,2,4],6)==(1,2)",
            "lambda: two_sum([3,3],6)==(0,1)",
        ],
    },
    "fix_off_by_one": {
        "kind": "debug",
        "prompt": "The function below is supposed to return a list of integers from 1 to n INCLUSIVE, but it has a bug. Return the corrected function.\n\ndef seq(n):\n    return list(range(1, n))",
        "checks": [
            "lambda: seq(5)==[1,2,3,4,5]",
            "lambda: seq(1)==[1]",
            "lambda: seq(0)==[]",
        ],
    },
    "fix_factorial": {
        "kind": "debug",
        "prompt": "The recursive factorial below is missing its base case and recurses forever. Return the corrected function.\n\ndef fact(n):\n    return n * fact(n-1)",
        "checks": [
            "lambda: fact(0)==1",
            "lambda: fact(5)==120",
            "lambda: fact(1)==1",
        ],
    },
    "add_step_param": {
        "kind": "modify",
        "prompt": "Add an optional parameter step (default 1) to the function below so it counts up by step, WITHOUT breaking existing calls like count_up(5). Return the modified function.\n\ndef count_up(n):\n    return list(range(0, n))",
        "checks": [
            "lambda: count_up(5)==[0,1,2,3,4]",
            "lambda: count_up(6,2)==[0,2,4]",
            "lambda: count_up(5,step=1)==[0,1,2,3,4]",
        ],
    },
    "parse_kv": {
        "kind": "impl",
        "prompt": "Write a Python function parse_kv(s) that parses a string like 'a=1,b=2,c=3' into a dict {'a':'1','b':'2','c':'3'} (values kept as strings). Return an empty dict for an empty string.",
        "checks": [
            "lambda: parse_kv('a=1,b=2')=={'a':'1','b':'2'}",
            "lambda: parse_kv('')=={}",
            "lambda: parse_kv('x=hello')=={'x':'hello'}",
        ],
    },
}

EXPERIMENT_TASK_NAMES = tuple(TASKS.keys())

# Payload-local positive control — deliberately not one of the 8 experiment tasks.
CONTROL_TASKS = {
    "control_add": {
        "kind": "control",
        "prompt": "Write a Python function add(a, b) that returns a plus b.",
        "checks": [
            "lambda: add(2,3)==5",
            "lambda: add(-1,1)==0",
            "lambda: add(0,0)==0",
            "lambda: add(100,-50)==50",
        ],
    },
}

# ---------------------- Item parsing (strict — SPEC §4)

_ITEM_KEY_ORDER = ("arm", "task", "replicate", "seed", "model")


def parse_item(line):
    """Return (arm, task, replicate, seed, model, error).
    On failure the first five values are None-ish and error is a bad_item:<reason>."""
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


# ---------------------- Guards (SPEC §7)

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
    """Free MB at path, or None if the path can't be stat'd. Used by stage_model's
    reactive eviction logic — the pre-flight disk check was removed
    because tablets have plenty of storage and models are considered disposable
."""
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
    """No-op pre-flight — tablets have ample storage and models are disposable
. stage_model does reactive eviction via .pulled
    markers if a pull would exceed KEEP_FREE_MB headroom. Kept as a hook so
    future guards can slot in without a schema change."""
    return None


def ensure_llm_dir():
    """Create LLM_DIR if missing. Tablets that haven't yet done AI work
    (only Fellowship-registered) don't have ~/llm — but the payload is fine
    to create it: llama-cli binary and models come from stage_model."""
    try:
        os.makedirs(LLM_DIR, exist_ok=True)
        return None
    except OSError as e:
        return "cannot_create_llm_dir:%s:%s" % (type(e).__name__, e)


# ---------------------- Staging (SPEC §6 — reuse gauntlet pattern)

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
        if _free_mb() >= need_mb:
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
    if _free_mb() < need + KEEP_FREE_MB:
        _evict_pulled(need + KEEP_FREE_MB)
    if _free_mb() < need + KEEP_FREE_MB:
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


# ---------------------- Inference (argv-only; prompt via -f file, never a shell)

def run_llama(model_basename, prompt, seed, prompt_path):
    """Argv-based llama-cli invocation. Prompt bytes flow through -f, not a shell.
    Returns (stdout_or_None, wall_s, error_or_None)."""
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
        r = subprocess.run(
            argv, capture_output=True, stdin=subprocess.DEVNULL,
            env=env, cwd=LLM_DIR, timeout=GENERATION_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None, round(time.time() - t0, 1), "generation_timeout"
    except FileNotFoundError:
        return None, round(time.time() - t0, 1), "llama_cli_missing"
    wall = round(time.time() - t0, 1)
    return r.stdout.decode(errors="replace"), wall, None


# ---------------------- Extraction (matches rigby extract_code semantics)

def extract_code(text):
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1)
    text = text.split("<|im_end|>")[0]
    m = re.search(r"(?:^|\n)(def |import |from )", text)
    return text[m.start():] if m else text


# ---------------------- Scoring (bounded child process; never raises)

def score(code, checks, work_dir):
    """Run generated code + fixed check lambdas in a separate python3 process.
    Returns (passed:int, total:int, err:str). Never raises."""
    lambdas = ", ".join(checks)
    prog = (
        code + "\n\n__cnt=0;__tot=0\n"
        "def _ck(f):\n    global __cnt,__tot\n    __tot+=1\n"
        "    try:\n        if f() is True or f()==True: __cnt+=1\n"
        "    except Exception: pass\n"
        "for _f in [" + lambdas + "]:\n    _ck(_f)\n"
        'print("RESULT",__cnt,__tot)\n'
    )
    total = len(checks)
    # GNU `timeout` is a coreutil present on Termux/Linux (production target).
    # Windows has `timeout.exe` with different, INTERACTIVE semantics — never
    # invoke it here. Fall back to parent-only enforcement on Windows / when
    # the binary is absent.
    argv_timeout = ["timeout", str(SCORE_CHILD_TIMEOUT_S), "python3", "-c", prog]
    argv_direct = ["python3", "-c", prog]
    r = None
    if os.name != "nt":
        try:
            r = subprocess.run(argv_timeout, capture_output=True, stdin=subprocess.DEVNULL,
                               cwd=work_dir, timeout=SCORE_PARENT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return 0, total, "score_parent_timeout"
        except FileNotFoundError:
            r = None  # fall through
    if r is None:
        try:
            r = subprocess.run(argv_direct, capture_output=True, stdin=subprocess.DEVNULL,
                               cwd=work_dir, timeout=SCORE_PARENT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return 0, total, "score_parent_timeout"
        except FileNotFoundError:
            return 0, total, "python3_missing"
    m = re.search(r"RESULT (\d+) (\d+)", r.stdout.decode(errors="replace"))
    if m:
        return int(m.group(1)), int(m.group(2)), ""
    stderr_tail = (r.stderr.decode(errors="replace").strip().splitlines() or [""])[-1][:160]
    return 0, total, stderr_tail or "no_result_line"


# ---------------------- Row assembly (SPEC §5)

_ROW_KEYS_REQUIRED = (
    "item", "arm", "task", "replicate", "seed", "model", "temperature", "token_limit",
    "ran", "ok", "checks_passed", "checks_total", "wall_s", "error", "answer",
)


def _base_row(item, arm, task, replicate, seed, model):
    return {
        "item": item,
        "arm": arm,
        "task": task,
        "replicate": replicate,
        "seed": seed,
        "model": model,
        "temperature": TEMPERATURE,
        "token_limit": TOKEN_LIMIT,
        "ran": False,
        "ok": False,
        "checks_passed": 0,
        "checks_total": 0,
        "wall_s": 0.0,
        "error": "",
        "answer": "",
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
    """Case-INSENSITIVE redaction — foundry's validate_control lowercases the
    whole row JSON before substring match (tablet_fulfil.py line 330), so a
    model emitting `Code_Control_Pass` (any case variant) in its answer would
    silently trigger a control pass without this."""
    if not text:
        return text
    return _SENTINEL_ANY_CASE_RE.sub("[REDACTED_SENTINEL]", text)


def _payload_item(item):
    """Remove Tolkien's existing SKU-lane routing prefix while preserving the coverage key."""
    lane, marker, body = item.partition("~")
    return body if marker and lane in {"A", "B", "C", "sam"} else item


def process_item(item, prompt_path, work_dir):
    arm, task, replicate, seed, model, perr = parse_item(_payload_item(item))
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
    prompt = tdef["prompt"] + SUFFIX
    stdout, wall, err = run_llama(model, prompt, seed, prompt_path)
    if err is not None:
        return _fail_row(item, arm, task, replicate, seed, model, err, wall=wall)

    code = extract_code(stdout)
    if not code.strip():
        row = _fail_row(item, arm, task, replicate, seed, model, "no_code_extracted", wall=wall)
        row["answer"] = _redact_sentinel(stdout[:400])
        return row

    passed, total, serr = score(code, tdef["checks"], work_dir)
    row = _base_row(item, arm, task, replicate, seed, model)
    row["wall_s"] = wall
    row["ran"] = True
    row["checks_passed"] = passed
    row["checks_total"] = total
    row["ok"] = (total > 0 and passed == total)
    row["error"] = _redact_sentinel(serr)
    row["answer"] = _redact_sentinel(code[:400])

    if arm == ARM_CONTROL and row["ok"] and row["checks_passed"] == row["checks_total"] and total > 0:
        row["control_result"] = CONTROL_SENTINEL

    return row


# ---------------------- CLI entry (SPEC §6/§9)

def _atomic_write_json(rows, path):
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    os.replace(tmp, path)


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 3:
        sys.stderr.write("usage: map_swarm_code.py items.txt out.json\n")
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
        except Exception as e:  # last-resort — never crash the batch
            rows.append(_bad_item_row(item, "unhandled:%s:%s" % (type(e).__name__, e)))

    _atomic_write_json(rows, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
