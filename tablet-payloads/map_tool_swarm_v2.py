"""map_tool_swarm.py — Phase 5 tool/agent swarm payload.

Grammar-constrained JSON tool-calling. Scoring: parse JSON, compare tool name
+ argument dict against gold. TOOL_TASKS + GBNF + PREAMBLE lifted verbatim from
fellow_gauntlet.py sha256 10aadccf9722f59825224799a67005356b61a5a22505dfb4909ec4415c7733b6.
Model held constant at the Phase-4 tool champion Qwen2.5-1.5B-Instruct-Q4_K_M.

Item contract:
    arm=tool-swarm;task=<name>;replicate=<n>;seed=<n>;model=Qwen2.5-1.5B-Instruct-Q4_K_M
    arm=control;task=control_tool;replicate=0;seed=1;model=<same>

Control sentinel TOOL_CONTROL_PASS.
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
ARM_EXPERIMENT = "tool-swarm"
ARM_CONTROL = "control"
CONTROL_TASK = "control_tool"
CONTROL_SENTINEL = "TOOL_CONTROL_PASS"

TEMPERATURE = 0.6
TOKEN_LIMIT = -1  # generate until model EOS
CONTEXT = 2048
THREADS = min(os.cpu_count() or 4, 8)  # per-tablet: class-C has 4 cores, class-A/B 8
GENERATION_TIMEOUT_S = 400

REQUIRED_ARCH = "aarch64"
KEEP_FREE_MB = 500

# GBNF verbatim from gauntlet.py — forces the model to emit strict JSON.
GBNF = '''root ::= "{" ws "\\"tool\\"" ws ":" ws toolname ws "," ws "\\"arguments\\"" ws ":" ws object ws "}"
toolname ::= "\\"read_log\\"" | "\\"restart_service\\"" | "\\"query_db\\"" | "\\"none\\""
object ::= "{" ws (member (ws "," ws member)*)? ws "}"
member ::= string ws ":" ws value
value ::= string | number | "true" | "false" | "null"
string ::= "\\"" schar* "\\""
schar ::= [^"\\\\]
number ::= "-"? [0-9]+ ("." [0-9]+)?
ws ::= [ \\t\\n]*
'''

PREAMBLE = (
    "You are a tool-using agent. You have exactly these tools:\n"
    "- read_log(service, lines): read the last N lines of a service's log\n"
    "- restart_service(service): restart a system service\n"
    "- query_db(table, id): look up a record by id in a table\n"
    'If no tool fits the request, use the tool "none" with empty arguments.\n\n'
    "Request: {req}\n\n"
    'Respond with ONLY a JSON object of the form {{"tool": "<name>", "arguments": {{...}}}} and nothing else.'
)

# 8 experiment tasks (lifted verbatim from gauntlet TOOL_TASKS, minus the CTRL_ entry).
TASKS = {
    "restart_nginx":  {"req": "The nginx service has crashed. Restart it.",              "gold_tool": "restart_service", "gold_args": {"service": "nginx"}},
    "restart_apigw":  {"req": "The api-gateway is unresponsive; please restart it.",     "gold_tool": "restart_service", "gold_args": {"service": "api-gateway"}},
    "readlog_pg":     {"req": "Show me the last 50 lines of the postgres service log.",  "gold_tool": "read_log",        "gold_args": {"service": "postgres", "lines": 50}},
    "readlog_redis":  {"req": "Get the last 100 lines of the redis service log.",        "gold_tool": "read_log",        "gold_args": {"service": "redis", "lines": 100}},
    "querydb_user":   {"req": "Look up user id 42 in the users table.",                  "gold_tool": "query_db",        "gold_args": {"table": "users", "id": 42}},
    "querydb_order":  {"req": "Fetch record 7 from the orders table.",                   "gold_tool": "query_db",        "gold_args": {"table": "orders", "id": 7}},
    "nofit_weather":  {"req": "What's the weather in Paris today?",                      "gold_tool": "none",            "gold_args": None},
    "nofit_poem":     {"req": "Write me a short poem about the ocean.",                  "gold_tool": "none",            "gold_args": None},
}
EXPERIMENT_TASK_NAMES = tuple(TASKS.keys())

CONTROL_TASKS = {
    "control_tool": {"req": "Restart the redis service.", "gold_tool": "restart_service", "gold_args": {"service": "redis"}},
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


def run_llama_with_grammar(model_basename, prompt, seed, prompt_path, grammar_path):
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt)
    with open(grammar_path, "w", encoding="utf-8") as f:
        f.write(GBNF)
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LLM_DIR
    argv = [
        LLAMA_CLI,
        "-m", os.path.join(LLM_DIR, model_basename + ".gguf"),
        "-f", prompt_path,
        "--grammar-file", grammar_path,
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


def extract_json(ans):
    """Balanced-brace scan for the first complete JSON object in the answer."""
    s = ans.find("{")
    if s == -1:
        return None
    depth = 0
    for i in range(s, len(ans)):
        if ans[i] == "{":
            depth += 1
        elif ans[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(ans[s:i + 1])
                except Exception:
                    return None
    return None


def _norm(v):
    return v.strip().lower() if isinstance(v, str) else v


def _args_ok(got, gold):
    """Gold=None means 'no arguments required' (only used by the 'none' tool).
    Otherwise every gold key must match, with strings normalised case-insensitively
    and numeric-looking strings compared via str-cast."""
    if gold is None:
        # For the "none" tool, any dict (empty preferred) is acceptable per gauntlet.
        return None
    if not isinstance(got, dict):
        return False
    for k, v in gold.items():
        g = got.get(k, got.get(k.lower()))
        if g is None:
            return False
        if _norm(g) != _norm(v) and str(_norm(g)) != str(_norm(v)):
            return False
    return True


def score_tool(answer, gold_tool, gold_args):
    """Two checks: tool name match, arguments match.
    'none' tool has gold_args=None; only tool name is scored (args_ok=None → skipped).
    Returns (passed, total, err_or_empty)."""
    parsed = extract_json(answer)
    if parsed is None:
        return 0, (1 if gold_args is None else 2), "no_json"
    tool_ok = (parsed.get("tool") == gold_tool)
    if gold_args is None:
        # 1 check: tool name only
        return (1 if tool_ok else 0), 1, "" if tool_ok else "wrong_tool"
    args_ok = _args_ok(parsed.get("arguments"), gold_args)
    passed = int(tool_ok) + int(bool(args_ok))
    err = "" if passed == 2 else ("wrong_tool" if not tool_ok else "wrong_args")
    return passed, 2, err


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


def _payload_item(item):
    """Remove Tolkien's existing SKU-lane routing prefix while preserving the coverage key."""
    lane, marker, body = item.partition("~")
    return body if marker and lane in {"A", "B", "C", "sam"} else item


def process_item(item, prompt_path, grammar_path, work_dir):
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
    prompt = PREAMBLE.format(req=tdef["req"])
    stdout, wall, err = run_llama_with_grammar(model, prompt, seed, prompt_path, grammar_path)
    if err is not None:
        return _fail_row(item, arm, task, replicate, seed, model, err, wall=wall)

    answer = isolate(stdout, prompt)
    passed, total, serr = score_tool(answer, tdef["gold_tool"], tdef["gold_args"])
    row = _base_row(item, arm, task, replicate, seed, model)
    row["wall_s"] = wall
    row["ran"] = True
    row["checks_passed"] = passed
    row["checks_total"] = total
    row["ok"] = (total > 0 and passed == total)
    row["error"] = _redact_sentinel(serr)
    row["answer"] = _redact_sentinel(answer[:400])
    row["gold_tool"] = tdef["gold_tool"]

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
        sys.stderr.write("usage: map_tool_swarm.py items.txt out.json\n")
        return 2
    items_path, out_path = argv[1], argv[2]
    work_dir = LLM_DIR if os.path.isdir(LLM_DIR) else os.getcwd()
    prompt_path = os.path.join(work_dir, "_swarm_prompt.txt")
    grammar_path = os.path.join(work_dir, "_swarm_tool.gbnf")

    with open(items_path, encoding="utf-8") as f:
        items = [line.strip() for line in f if line.strip()]

    rows = []
    for item in items:
        try:
            rows.append(process_item(item, prompt_path, grammar_path, work_dir))
        except Exception as e:
            rows.append(_bad_item_row(item, "unhandled:%s:%s" % (type(e).__name__, e)))

    _atomic_write_json(rows, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
