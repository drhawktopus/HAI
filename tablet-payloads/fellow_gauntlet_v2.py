"""Fellowship gauntlet payload (v2) — runs ON a tablet under the foundry map contract
`python fellow_gauntlet.py items.txt out.json`. Each item is a cell `<SKU>~<model_basename>~<division>~<taskid>`.
Emits a JSON list of rows; each row carries `item` (the coverage key) + scoring. The foundry adds `_host`.

Divisions (all deterministically scored — no LLM judge):
  tool   grammar-constrained JSON tool-calling; scores tool selection + argument correctness.
  chat   short factual/reasoning questions; scores a whole-word match of the gold token.
  coding generate a function, run it against fixed asserts on-node; exit 0 = pass.
  vlm    stub for now (needs image staging + llama-mtmd-cli) — recorded ran:false division-not-implemented.

Fit + staging: a model whose gguf is absent is pulled from FELLOW_STAGE_URL (a rigby HTTP store) when set;
if that is unset or the pull fails it records ran:false reason:model-not-staged and never crashes the batch.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

LLM = os.path.expanduser("~/llm")
# Central model store served over HTTP from rigby's HDD. Overridable, but defaulted so the payload
# needs no per-run env injection from the foundry. A model absent on this node is pulled from here.
STAGE_URL = os.environ.get("FELLOW_STAGE_URL", "").rstrip("/")
KEEP_FREE_MB = 500   # headroom to leave free after a pull (llama-cli scratch)

# ---------------------------------------------------------------- tool division

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

TOOL_TASKS = {
    "restart_nginx": ("The nginx service has crashed. Restart it.", "restart_service", {"service": "nginx"}),
    "restart_apigw": ("The api-gateway is unresponsive; please restart it.", "restart_service", {"service": "api-gateway"}),
    "readlog_pg": ("Show me the last 50 lines of the postgres service log.", "read_log", {"service": "postgres", "lines": 50}),
    "readlog_redis": ("Get the last 100 lines of the redis service log.", "read_log", {"service": "redis", "lines": 100}),
    "querydb_user": ("Look up user id 42 in the users table.", "query_db", {"table": "users", "id": 42}),
    "querydb_order": ("Fetch record 7 from the orders table.", "query_db", {"table": "orders", "id": 7}),
    "nofit_weather": ("What's the weather in Paris today?", "none", None),
    "nofit_poem": ("Write me a short poem about the ocean.", "none", None),
    "CTRL_restart": ("Restart the redis service.", "restart_service", {"service": "redis"}),
}

# ---------------------------------------------------------------- chat division
# (question, gold_token). Gold is matched as a WHOLE WORD so "3" does not match "30".
CHAT_TASKS = {
    "cap_france": ("What is the capital of France? Answer with only the city name.", "paris"),
    "cap_japan": ("What is the capital of Japan? Answer with only the city name.", "tokyo"),
    "math_add": ("What is 47 + 58? Answer with only the number.", "105"),
    "math_mul": ("What is 12 times 12? Answer with only the number.", "144"),
    "antonym_hot": ("What is the opposite of the word 'hot'? Answer with one word.", "cold"),
    "plural_mouse": ("What is the plural of the word 'mouse' (the animal)? One word.", "mice"),
    "seq_next": ("What number comes next: 2, 4, 6, 8, ...? Answer with only the number.", "10"),
    "elem_symbol": ("What is the chemical symbol for gold? Answer with only the symbol.", "au"),
    "CTRL_chat": ("What is the capital of Italy? Answer with only the city name.", "rome"),
}

# ---------------------------------------------------------------- coding division
# (prompt, funcname, test_body). test_body asserts against the generated function; exit 0 = pass.
CODE_TASKS = {
    "is_prime": ("Write a Python function `is_prime(n)` that returns True if n is a prime number, else False. Output only the function code.",
                 "is_prime",
                 "assert is_prime(7) and is_prime(2) and not is_prime(1) and not is_prime(8) and not is_prime(9)"),
    "reverse_str": ("Write a Python function `reverse_string(s)` that returns the string s reversed. Output only the function code.",
                    "reverse_string",
                    "assert reverse_string('hello')=='olleh' and reverse_string('')=='' and reverse_string('ab')=='ba'"),
    "fib": ("Write a Python function `fib(n)` returning the nth Fibonacci number, with fib(0)=0 and fib(1)=1. Output only the function code.",
            "fib",
            "assert fib(0)==0 and fib(1)==1 and fib(10)==55"),
    "sum_list": ("Write a Python function `sum_list(xs)` that returns the sum of a list of numbers. Output only the function code.",
                 "sum_list",
                 "assert sum_list([1,2,3])==6 and sum_list([])==0 and sum_list([-1,1])==0"),
    "count_vowels": ("Write a Python function `count_vowels(s)` that returns the number of vowels (aeiou) in s. Output only the function code.",
                     "count_vowels",
                     "assert count_vowels('hello')==2 and count_vowels('xyz')==0 and count_vowels('aeiou')==5"),
    "CTRL_code": ("Write a Python function `add(a, b)` that returns a plus b. Output only the function code.",
                  "add",
                  "assert add(2,3)==5 and add(-1,1)==0"),
}


def sh(cmd, timeout):
    return subprocess.run(cmd, shell=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)


def _free_mb(path=LLM):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / (1024 * 1024)


def _remote_mb(model):
    """Content-Length of the store's copy in MB, or None. Sizes the disk guard before pulling.
    urllib, not curl: Termux's curl is linker-broken on these tablets (missing SSL_set_quic symbol)."""
    try:
        req = urllib.request.Request("%s/%s.gguf" % (STAGE_URL, model), method="HEAD")
        with urllib.request.urlopen(req, timeout=20) as r:
            cl = r.headers.get("Content-Length")
            return int(cl) / (1024 * 1024) if cl else None
    except Exception:
        return None


def _evict_pulled(need_mb):
    """Free space by deleting ONLY models THIS payload pulled (marked .pulled), largest first.
    Models already on the tablet are never touched -- they carry no .pulled marker."""
    pulled = [f for f in os.listdir(LLM)
              if f.endswith(".gguf") and os.path.exists(os.path.join(LLM, f + ".pulled"))]
    pulled.sort(key=lambda f: os.path.getsize(os.path.join(LLM, f)), reverse=True)
    for f in pulled:
        if _free_mb() >= need_mb:
            break
        for x in (f, f + ".pulled"):
            try:
                os.remove(os.path.join(LLM, x))
            except OSError:
                pass


def stage_model(model):
    """Return the gguf path if present, else pull it from the central store. On a small-disk node,
    evict previously-pulled models first. Returns (path, None) on success or (None, reason) where
    reason distinguishes a storage non-fit from a transfer failure -- both are real matrix signals."""
    path = os.path.join(LLM, model + ".gguf")
    if os.path.exists(path):
        return path, None
    if not STAGE_URL:
        return None, "model-not-staged"
    need = _remote_mb(model)
    if need is None:
        return None, "model-not-in-store"
    if _free_mb() < need + KEEP_FREE_MB:
        _evict_pulled(need + KEEP_FREE_MB)
    if _free_mb() < need + KEEP_FREE_MB:
        return None, "model-too-big-for-node"          # fit:false by storage
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
        return None, "stage-transfer-failed"
    if os.path.exists(tmp) and os.path.getsize(tmp) > 1_000_000:
        os.replace(tmp, path)
        open(path + ".pulled", "w").close()            # mark evictable; never evicts a model that was on the tablet before
        return path, None
    try:
        os.remove(tmp)
    except OSError:
        pass
    return None, "stage-transfer-failed"


def _run_llama(model, prompt, grammar=None, n=160):
    """Run llama-cli once with the prompt staged to a file; returns (stdout_text, wall_s) or (None, wall)."""
    with open(os.path.join(LLM, "_g_prompt.txt"), "w", encoding="utf-8") as f:
        f.write(prompt)
    g = ""
    if grammar:
        with open(os.path.join(LLM, "_g.gbnf"), "w", encoding="utf-8") as f:
            f.write(grammar)
        g = "--grammar-file _g.gbnf"
    cmd = ("cd %s && LD_LIBRARY_PATH=. ./llama-cli -m %s.gguf -f _g_prompt.txt "
           "-st --no-display-prompt --temp 0 --seed 1 -n %d -c 2048 -t 8 %s"
           % (LLM, model, n, g))
    t0 = time.time()
    try:
        r = sh(cmd, timeout=400)
    except subprocess.TimeoutExpired:
        return None, round(time.time() - t0, 1)
    return r.stdout.decode(errors="replace"), round(time.time() - t0, 1)


def isolate(out, prompt):
    for m in ("[ Prompt:", "\nExiting", "\n> /", "\n> \n"):
        i = out.find(m)
        if i != -1:
            out = out[:i]
    plines = [l.rstrip() for l in prompt.splitlines() if l.strip()]
    if plines:
        idx = out.rfind(plines[-1])
        if idx != -1:
            out = out[idx + len(plines[-1]):]
    return out.strip()


def extract_json(ans):
    s = ans.find("{")
    if s == -1:
        return None
    d = 0
    for i in range(s, len(ans)):
        if ans[i] == "{":
            d += 1
        elif ans[i] == "}":
            d -= 1
            if d == 0:
                try:
                    return json.loads(ans[s:i + 1])
                except Exception:
                    return None
    return None


def _norm(v):
    return v.strip().lower() if isinstance(v, str) else v


def _args_ok(got, gold):
    if gold is None:
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


def run_tool_cell(model, taskid):
    req, gold_tool, gold_args = TOOL_TASKS[taskid]
    prompt = PREAMBLE.format(req=req)
    out, wall = _run_llama(model, prompt, grammar=GBNF)
    if out is None:
        return {"ran": False, "reason": "timeout", "ok": None, "wall_s": wall}
    ans = isolate(out, prompt)
    parsed = extract_json(ans)
    tool_ok = bool(parsed and parsed.get("tool") == gold_tool)
    args_ok = _args_ok((parsed or {}).get("arguments"), gold_args)
    return {"ran": True, "ok": tool_ok, "tool_ok": tool_ok, "args_ok": args_ok, "wall_s": wall,
            "tool": (parsed or {}).get("tool"), "answer": ans[:160]}


def run_chat_cell(model, taskid):
    q, gold = CHAT_TASKS[taskid]
    prompt = q + "\n"
    out, wall = _run_llama(model, prompt, n=120)
    if out is None:
        return {"ran": False, "reason": "timeout", "ok": None, "wall_s": wall}
    ans = isolate(out, prompt)
    hit = re.search(r"\b" + re.escape(gold) + r"\b", ans, re.I) is not None
    return {"ran": True, "ok": hit, "wall_s": wall, "gold": gold, "answer": ans[:200]}


def extract_code(ans):
    m = re.search(r"```(?:python)?\s*(.*?)```", ans, re.S | re.I)
    if m:
        return m.group(1).strip()
    return ans.strip()


def run_coding_cell(model, taskid):
    prompt, funcname, test = CODE_TASKS[taskid]
    out, wall = _run_llama(model, prompt, n=256)
    if out is None:
        return {"ran": False, "reason": "timeout", "ok": None, "wall_s": wall}
    ans = isolate(out, prompt)
    code = extract_code(ans)
    if funcname not in code:
        return {"ran": True, "ok": False, "wall_s": wall, "reason": "no-func", "answer": ans[:200]}
    prog = code + "\n" + test + "\nprint('CODE_OK')\n"
    p = os.path.join(LLM, "_g_code.py")
    with open(p, "w", encoding="utf-8") as f:
        f.write(prog)
    try:
        r = sh("cd %s && python _g_code.py" % LLM, timeout=30)
        ok = r.returncode == 0 and b"CODE_OK" in r.stdout
        err = "" if ok else (r.stderr.decode(errors="replace").strip().splitlines() or [""])[-1][:120]
    except subprocess.TimeoutExpired:
        ok, err = False, "exec-timeout"
    return {"ran": True, "ok": ok, "wall_s": wall, "err": err, "answer": ans[:200]}


DIVISIONS = {"tool": (TOOL_TASKS, run_tool_cell),
             "chat": (CHAT_TASKS, run_chat_cell),
             "coding": (CODE_TASKS, run_coding_cell)}


def main():
    items_file, out_file = sys.argv[1], sys.argv[2]
    with open(items_file, encoding="utf-8") as f:
        cells = [l.strip() for l in f if l.strip()]
    rows = []
    for cell in cells:
        parts = (cell.split("~") + ["", "", "", ""])[:4]
        sku, model, division, taskid = parts
        row = {"item": cell, "sku": sku, "model": model, "division": division, "task": taskid}
        try:
            tasks_fn = DIVISIONS.get(division)
            if division == "vlm":
                row.update({"ran": False, "reason": "division-not-implemented", "ok": None})
            elif tasks_fn and taskid in tasks_fn[0]:
                _path, _why = stage_model(model)
                if _path is None:
                    row.update({"ran": False, "reason": _why, "ok": None})
                else:
                    row.update(tasks_fn[1](model, taskid))
            else:
                row.update({"ran": False, "reason": "unknown-cell", "ok": None})
        except Exception as e:
            row.update({"ran": False, "reason": "payload-error:%s" % type(e).__name__, "ok": None})
        rows.append(row)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(rows, f)


if __name__ == "__main__":
    main()
