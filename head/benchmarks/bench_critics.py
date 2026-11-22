"""Critic benchmark: score adversarial-critic models on a fixture with KNOWN planted bugs.

Turns "is model X a good critic?" into a measurable RECALL number (how many of the planted bugs it
catches) instead of a subjective read. Re-runnable as models change. Run in the orchestrator
container so it uses the real ollama_client/deliberation path:

    docker cp bench_critics.py orchestrator:/tmp/ && \
    docker exec -e PYTHONPATH=/app orchestrator python /tmp/bench_critics.py

Each bug has keyword sets; a model "catches" a bug if any keyword appears in any finding. Keyword
matching is a proxy (it can miss a correct-but-differently-worded catch or over-credit a vague
mention), so the raw findings are printed too for a human spot-check.
"""
import sys
import time

import config
import deliberation
import ollama_client
import wol


def ensure_lead_up():
    """Wake the lead if it's asleep and wait until Ollama answers -- the SAME thing production's
    _ensure_lead does. Makes this benchmark self-sufficient so it never fails just because the
    box idle-slept between runs (the recurring 'connection timed out' at startup)."""
    host = config.ADVERSARY_HOST
    if ollama_client.reachable(host):
        return True
    print("%s asleep -> sending WoL + waiting for Ollama..." % config.LEAD_BOX_LABEL, flush=True)
    wol.wake(config.device_mac(config.LEAD_BOX))
    deadline = time.time() + max(60, int(config.WAKE_TIMEOUT))
    while time.time() < deadline:
        if ollama_client.reachable(host):
            time.sleep(max(0, float(config.LEAD_BOX_WAKE_SETTLE)))   # let ROCm settle after a cold wake
            print("%s up." % config.LEAD_BOX_LABEL, flush=True)
            return True
        time.sleep(3)
    print("WARNING: %s still unreachable after wake; running anyway." % config.LEAD_BOX_LABEL, flush=True)
    return False


# A realistic in-memory job store + SQLite audit, with 7 intentional bugs of varying subtlety.
BUGGY_CODE = '''
import sqlite3, threading

_jobs = {}
_lock = threading.Lock()

def add_job(job_id, payload, tags=[]):
    tags.append("new")
    _jobs[job_id] = {"payload": payload, "tags": tags, "status": "pending"}
    return _jobs[job_id]

def average_runtime(runtimes):
    return sum(runtimes) / len(runtimes)

def find_job(db_path, name):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE name = '%s'" % name)
    return cur.fetchall()

def mark_done(job_id):
    if job_id in _jobs:
        _jobs[job_id]["status"] = "done"

def load_config(path):
    try:
        return open(path).read()
    except:
        return None

def nth_job(job_ids, n):
    return job_ids[n]
'''

# Answer key: each planted bug, its severity, and keyword sets that signal a catch.
BUGS = [
    {"id": "mutable-default", "sev": "medium",
     "desc": "add_job(tags=[]) mutable default arg shared across calls",
     "keys": ["mutable default", "default argument", "default list", "tags=[]", "shared default",
              "mutable arg"]},
    {"id": "div-by-zero", "sev": "high",
     "desc": "average_runtime divides by len() with no empty-list guard",
     "keys": ["zerodivision", "division by zero", "divide by zero", "empty list", "len(runtimes)",
              "empty input", "len() == 0", "len is zero"]},
    {"id": "sql-injection", "sev": "critical",
     "desc": "find_job formats name into SQL string (injection)",
     "keys": ["sql injection", "injection", "parameteri", "sanitiz", "string formatting into",
              "%s", "untrusted input", "prepared statement"]},
    {"id": "conn-leak", "sev": "medium",
     "desc": "find_job never closes the sqlite connection",
     "keys": ["never closed", "not closed", "connection leak", "resource leak", "context manager",
              "conn.close", "leak", "with sqlite3", "close the connection"]},
    {"id": "race-condition", "sev": "high",
     "desc": "mark_done does check-then-act on _jobs without using _lock",
     "keys": ["race", "lock", "concurren", "thread-saf", "atomic", "check-then-act", "not acquired",
              "without the lock", "_lock"]},
    {"id": "bare-except", "sev": "medium",
     "desc": "load_config uses bare except that swallows all errors",
     "keys": ["bare except", "except:", "broad except", "swallow", "catches all", "silently",
              "catch-all", "too broad", "hides errors", "overly broad"]},
    {"id": "index-bounds", "sev": "medium",
     "desc": "nth_job indexes job_ids[n] with no bounds check",
     "keys": ["bounds", "index error", "indexerror", "out of range", "out-of-range", "length check",
              "no bounds", "valid index", "range check"]},
]


# A second fixture: code that LOOKS correct and passes naive tests, with 6 SUBTLE bugs that need
# actual reasoning (algorithm/concurrency/numeric) to spot -- the regime where a bigger dense
# reasoner *should* pull ahead of a smaller one if it's ever going to.
HARD_CODE = '''
import threading, time

_buf_a, _buf_b = [], []
_lock_a, _lock_b = threading.Lock(), threading.Lock()

def bisect_left(a, x):
    """Leftmost insertion index keeping a sorted."""
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x:
            lo = mid
        else:
            hi = mid
    return lo

def push_left(item):
    with _lock_a:
        with _lock_b:
            _buf_a.append(item); _buf_b.append(item)

def push_right(item):
    with _lock_b:
        with _lock_a:
            _buf_b.append(item); _buf_a.append(item)

def cache_is_stale(entry_ts_seconds, ttl_ms=30000):
    return (time.time() - entry_ts_seconds) > ttl_ms

def median(nums):
    s = sorted(nums)
    return s[len(s) // 2]

class Counter:
    seen = set()
    def record(self, key):
        self.seen.add(key)
        return len(self.seen)

def total_equals(values, target):
    total = 0.0
    for v in values:
        total += v
    return total == target
'''

HARD_BUGS = [
    {"id": "bsearch-infinite-loop", "sev": "high",
     "desc": "bisect_left sets lo=mid (not mid+1) -> infinite loop when a[mid] < x and mid==lo",
     "keys": ["infinite loop", "off-by-one", "off by one", "mid + 1", "mid+1", "never terminat",
              "non-terminat", "does not advance", "endless", "won't terminate", "fails to terminate",
              "lo never"]},
    {"id": "lock-order-deadlock", "sev": "high",
     "desc": "push_left locks a->b, push_right locks b->a -> classic lock-ordering deadlock",
     "keys": ["deadlock", "lock order", "lock-order", "different order", "opposite order",
              "reverse order", "inconsistent lock", "circular wait", "acquire .* reverse"]},
    {"id": "ttl-unit-mismatch", "sev": "high",
     "desc": "cache_is_stale compares a SECONDS delta to a MILLISECONDS ttl (off by 1000x)",
     "keys": ["millisecond", "ms vs", "1000", "unit mismatch", "wrong unit", "time unit",
              "scale mismatch", "factor of 1000", "ttl_ms", "seconds and millisecond"]},
    {"id": "median-even", "sev": "medium",
     "desc": "median returns upper-middle for even-length input instead of averaging the two middles",
     "keys": ["even-length", "even length", "even number of", "two middle", "average the two",
              "average of the two", "even-sized", "even case", "even list"]},
    {"id": "shared-class-attr", "sev": "high",
     "desc": "Counter.seen is a class attribute -> shared across ALL instances",
     "keys": ["class attribute", "class-level", "class variable", "shared across", "all instances",
              "mutable class", "shared between instances", "shared state across", "instance attribute"]},
    {"id": "float-equality", "sev": "medium",
     "desc": "total_equals uses == on accumulated floats (precision failure)",
     "keys": ["floating point", "floating-point", "float equality", "exact equal", "precision",
              "math.isclose", "isclose", "epsilon", "exact float", "compare float"]},
]

FIXTURES = {
    "easy": (BUGGY_CODE, BUGS, "a small job-store module"),
    "hard": (HARD_CODE, HARD_BUGS, "small algorithm/concurrency/numeric utilities"),
}


def _catches(finding_text, bug):
    t = (finding_text or "").lower()
    return any(k in t for k in bug["keys"])


def bench(model, code, bugs, desc):
    messages = [
        {"role": "system", "content": deliberation.CRITIQUE_SYSTEM},
        {"role": "user", "content": (
            f"Objective: {desc}\nAcceptance: correct + safe\n\nReview this code ADVERSARIALLY -- "
            "your job is to BREAK it: find bugs, edge cases, security and concurrency issues. "
            "Return the JSON contract (verdict + findings).\n\n" + code)},
    ]
    t0 = time.time()
    ans, _ = ollama_client.chat(config.ADVERSARY_HOST, model, messages, [], config.ADVERSARY_NUM_CTX,
                                user=config.OWNER_USER or "admin", timeout=1800)
    dt = time.time() - t0
    crit = deliberation.parse_critique(ans)
    findings = crit.get("findings") or []
    blob = " || ".join((f.get("explanation") or "") + " " + (f.get("recommended_action") or "")
                       for f in findings)
    caught = {b["id"]: _catches(blob, b) for b in bugs}
    return {"dt": dt, "n": len(findings), "caught": caught, "findings": findings}


def run_fixture(name, models):
    code, bugs, desc = FIXTURES[name]
    print(f"\n######## FIXTURE: {name}  ({len(bugs)} planted: "
          f"{', '.join(b['id'] for b in bugs)}) ########\n", flush=True)
    rows = []
    for m in models:
        try:
            r = bench(m, code, bugs, desc)
            hits = sum(r["caught"].values())
            rows.append((m, r, hits))
            got = [bid for bid, ok in r["caught"].items() if ok]
            missed = [bid for bid, ok in r["caught"].items() if not ok]
            print(f"===== {m}  |  {r['dt']:.0f}s  |  {r['n']} findings  |  "
                  f"RECALL {hits}/{len(bugs)} =====", flush=True)
            print("  caught: " + (", ".join(got) if got else "(none)"))
            print("  MISSED: " + (", ".join(missed) if missed else "(none)"))
            for f in r["findings"]:
                print(f"    - [{f.get('severity')}] {(f.get('explanation') or '')[:180]}")
            print(flush=True)
        except Exception as e:
            print(f"===== {m}  |  FAILED: {e!r} =====\n", flush=True)
    if rows:
        print(f"=== SUMMARY [{name}] (recall / time) ===")
        for m, r, hits in sorted(rows, key=lambda x: (-x[2], x[1]["dt"])):
            print(f"  {hits}/{len(bugs)}  {r['dt']:5.0f}s  {m}")
        print(flush=True)


def main(argv):
    # usage: bench_critics.py [easy|hard|both] [model ...]
    sel = "hard"
    rest = argv[1:]
    if rest and rest[0] in ("easy", "hard", "both"):
        sel = rest.pop(0)
    models = rest or [config.HEAVY_MODEL, "qwen3-coder:30b", "qwen3.6:35b-a3b", "deepseek-r1:70b"]
    ensure_lead_up()   # self-wake so a slept box never fails the run at startup
    for fx in (["easy", "hard"] if sel == "both" else [sel]):
        run_fixture(fx, models)


if __name__ == "__main__":
    main(sys.argv)
