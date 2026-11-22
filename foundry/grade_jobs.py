"""Grade saved coding answers in parallel - the Project 34 menu's grading, as a compute order or a local pool.

One copy of the grading logic: score_answer() (fence-tolerant extraction, then the payload's own checks) and the cache
key live HERE; build_menu.py imports them. A jobs file comes from `build_menu.py --jobs-out` and says which grader
fingerprint made it; a payload whose fingerprint differs refuses (exit 2), because its grades would key a different
instrument.

    python grade_jobs.py JOBS.json OUT.jsonl [--workers N]      a GPU factory compute order; N = 0 (default): every core
    python grade_jobs.py items.txt out.json                     a Tolkien map order (scorer payloads staged beside it
                                                                 via support_files; one base64 job per item line)

JOBS.json: {"fingerprint", "payload_dir", "controls": [job + "expect"], "jobs": [{"key", "payload", "task", "answer"}]}
OUT.jsonl: one [key, grade] per line, flushed per grade. Progress: `[grade] N/M <task> PASS|fail` per answer.
Before any job, the controls (answers whose grade is already known from another box) are re-graded here; one
disagreement refuses the order (exit 2): `score()` runs answers with `python3` from PATH, and a box where that name is
not a real interpreter grades every answer "fail" without an error.
"""
import base64
import concurrent.futures
import hashlib
import importlib.util
import inspect
import json
import os
import re
import sys
import tempfile
import time

FENCE = re.compile(r"```(?:python)?[^\n]*\n(.*?)```", re.DOTALL)
ANSWER_LINE = re.compile(r"^[ \t]*ANSWER[ \t]*=.*$", re.MULTILINE)


def answer_code(m, answer):
    """Extract the code to grade based on ANSWER presence and format.

    ANSWER tasks: Uses the LAST fence that assigns ANSWER to distinguish the final answer from earlier illustrative code blocks. Falls back to the last bare `ANSWER = ...` line outside any fence.

    Code tasks (no ANSWER): Uses the first fence.

    Note: `ANSWER = NOT_STATED` is a valid sentinel for C1 tasks. Format compliance (e.g., single fence) is reported separately via `format_fail` and does not affect answer correctness."""
    answer = answer or ""
    fences = FENCE.findall(answer)
    assigned = [f for f in fences if ANSWER_LINE.search(f)]
    if assigned:
        values = {re.sub(r"\s+", "", l) for f in assigned for l in ANSWER_LINE.findall(f)}
        code = assigned[-1]
    else:
        bare = [l.strip().rstrip(" .") for l in ANSWER_LINE.findall(FENCE.sub("", answer))]
        values = {re.sub(r"\s+", "", l) for l in bare}
        if bare:
            code = bare[-1] + "\n"
        else:
            code = fences[0] if fences else m.extract_code(answer)
    if len(values) > 1:                         # two different answers is a hedge, never a pick
        return 'raise ValueError("hedged: %d different ANSWER values")\n' % len(values)
    if re.search(r"\bNOT_STATED\b", code):
        code = 'NOT_STATED = "NOT_STATED"\n' + code
    return code


def score_answer(m, tdef, answer, work):
    """The grading logic itself (the cache fingerprint covers this, answer_code and both patterns): the answer as
    written, then the payload's own checks."""
    return m.score(answer_code(m, answer), tdef["checks"], work)


def fingerprint():
    return hashlib.sha256((inspect.getsource(answer_code) + inspect.getsource(score_answer) + FENCE.pattern
                           + ANSWER_LINE.pattern).encode()).hexdigest()[:12]


def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_payload(path):
    """-> (module, first 12 of its sha256)."""
    spec = importlib.util.spec_from_file_location("p_" + os.path.basename(path).replace(".", "_"), path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m, file_sha(path)[:12]


def task_def(m, task):
    if hasattr(m, "CONTROL_TASKS") and task in m.CONTROL_TASKS:
        return m.CONTROL_TASKS[task]
    return m.TASKS.get(task)


def cache_key(psha, fp, task, answer):
    return "%s|%s|%s|%s" % (psha, fp, task, hashlib.sha256((answer or "").encode("utf-8")).hexdigest())


def grade_one(m, psha, task, answer, work):
    tdef = task_def(m, task)
    if tdef is None:
        return None
    passed, total, err = score_answer(m, tdef, answer, work)
    return {"ok": total > 0 and passed == total, "checks_passed": passed, "checks_total": total,
            "grader": "fence-tolerant:%s@%s" % (os.path.basename(m.__file__), psha), "error": err}


_W = {}


def _init(payload_dir):
    _W["dir"], _W["mods"], _W["work"] = payload_dir, {}, tempfile.mkdtemp(prefix="grade_w_")


def _work(job):
    p = job["payload"]
    if p not in _W["mods"]:
        _W["mods"][p] = load_payload(os.path.join(_W["dir"], p))
    m, psha = _W["mods"][p]
    return job["key"], grade_one(m, psha, job["task"], job["answer"], _W["work"])


def run(jobs, payload_dir, workers, on_grade):
    """Grade `jobs` on `workers` processes (0 = every core); on_grade(i, n, job, grade) after each one."""
    n = len(jobs)
    workers = workers or os.cpu_count() or 1
    if workers == 1:
        _init(payload_dir)
        for i, j in enumerate(jobs, 1):
            on_grade(i, n, j, _work(j)[1])
        return
    by_key = {j["key"]: j for j in jobs}
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers, initializer=_init,
                                                initargs=(payload_dir,)) as ex:
        for i, (key, g) in enumerate(ex.map(_work, jobs, chunksize=4), 1):
            on_grade(i, n, by_key[key], g)


SELFTEST = (("def add(a, b):\n    return a + b\n", True), ("def add(a, b):\n    return a - b\n", False))
MAP_PREFIX = "G1:"


def selftest(m, psha, work):
    """The scorer's own control task on a known-right and a known-wrong answer: proves `python3` here really runs code."""
    got = [bool((grade_one(m, psha, "control_add", ans, work) or {}).get("ok")) for ans, _ in SELFTEST]
    return got == [want for _, want in SELFTEST], got


def map_item(job):
    """One job as one Tolkien map item: base64 JSON on a single line (no '|' or newline)."""
    return MAP_PREFIX + base64.b64encode(json.dumps(job).encode("utf-8")).decode("ascii")


def map_main(items_path, out_path):
    """Tolkien map contract: `python grade_jobs.py items.txt out.json`, the scorer payloads staged beside this file.
    One row per item, echoing it; a control item's row says GRADE_CONTROL_OK only if it grades as its known answer."""
    here = os.path.dirname(os.path.abspath(__file__))
    lines = [ln.strip() for ln in open(items_path, encoding="utf-8") if ln.strip()]
    work, mods, tested, rows = tempfile.mkdtemp(prefix="grade_m_"), {}, {}, []
    for i, line in enumerate(lines, 1):
        row = {"item": line, "key": None, "grade": None, "error": ""}
        try:
            job = json.loads(base64.b64decode(line[len(MAP_PREFIX):]).decode("utf-8"))
            row["key"] = job["key"]
            if job.get("fingerprint") != fingerprint():
                raise ValueError("grader fingerprint %s here, %s in the job" % (fingerprint(), job.get("fingerprint")))
            p = job["payload"]
            if p not in mods:
                mods[p] = load_payload(os.path.join(here, p))
                tested[p] = selftest(mods[p][0], mods[p][1], work)
            if not tested[p][0]:
                raise RuntimeError("SELFTEST_FAILED: control_add right/wrong graded %s here - python3 on this node "
                                   "does not run code" % tested[p][1])
            m, psha = mods[p]
            row["grade"] = grade_one(m, psha, job["task"], job["answer"], work)
            if "expect" in job:
                row["control"] = ("GRADE_CONTROL_OK" if bool(row["grade"]["ok"]) == bool(job["expect"])
                                  else "GRADE_CONTROL_WRONG")
        except Exception as e:                                                   # noqa: BLE001
            row["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
        rows.append(row)
        g = row["grade"]
        print("[grade] %d/%d %s" % (i, len(lines), ("PASS" if g and g["ok"] else "fail") if g else row["error"][:80]),
              flush=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    if len(args) == 2 and args[0].endswith(".txt"):
        return map_main(args[0], args[1])
    workers = 0
    if "--workers" in args:
        workers = int(args[args.index("--workers") + 1])
        del args[args.index("--workers"):args.index("--workers") + 2]
    jobs_path, out_path = args[0], args[1]
    with open(jobs_path, encoding="utf-8") as f:
        spec = json.load(f)
    if spec.get("fingerprint") != fingerprint():
        print("REFUSED: grader fingerprint %s here, %s in the jobs file - a different scoring instrument"
              % (fingerprint(), spec.get("fingerprint")), flush=True)
        raise SystemExit(2)
    pdir = spec["payload_dir"]
    controls = spec.get("controls") or []
    assert controls, "a jobs file without controls cannot prove this box grades correctly"
    bad = []
    _init(pdir)
    for p in sorted({j["payload"] for j in spec["jobs"]} | {c["payload"] for c in controls}):
        m, psha = load_payload(os.path.join(pdir, p))
        ok, got = selftest(m, psha, _W["work"])
        if not ok:
            print("REFUSED: SELFTEST_FAILED for %s: control_add right/wrong graded %s here - check that `python3` on "
                  "PATH is a real interpreter" % (p, got), flush=True)
            raise SystemExit(2)
    for c in controls:
        g = _work(c)[1]
        if g is None or bool(g["ok"]) != bool(c["expect"]):
            bad.append((c["task"], c["expect"], g))
    print("[grade] controls: %d of %d agree with their known grade" % (len(controls) - len(bad), len(controls)),
          flush=True)
    if bad:
        print("REFUSED: this box grades known answers differently %s - check that `python3` on PATH is a real "
              "interpreter (score() runs answers with it)" % bad[:2], flush=True)
        raise SystemExit(2)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    done = set()
    if os.path.exists(out_path):                 # a restarted order (a yield, a requeue) resumes: keep what is graded
        with open(out_path, encoding="utf-8") as f:
            for ln in f:
                try:
                    done.add(json.loads(ln)[0])
                except (ValueError, IndexError):
                    pass
    jobs = [j for j in spec["jobs"] if j["key"] not in done]
    if done:
        print("[grade] resuming: %d already graded in %s, %d to go" % (len(done), out_path, len(jobs)), flush=True)
    t0, stats = time.time(), {"pass": 0}
    with open(out_path, "a", encoding="utf-8") as out:
        def on_grade(i, n, job, g):
            if g is not None:
                out.write(json.dumps([job["key"], g]) + "\n")
                out.flush()
                stats["pass"] += bool(g["ok"])
            el = time.time() - t0
            print("[grade] %d/%d %s %s | %.0fs, ETA %.0fs" % (
                i, n, job["task"], "PASS" if g and g["ok"] else "fail", el, el / i * (n - i)), flush=True)
        run(jobs, pdir, workers, on_grade)
    print("[grade] done: %d graded, %d PASS, %.0fs on %d workers -> %s"
          % (len(jobs), stats["pass"], time.time() - t0, workers or os.cpu_count(), out_path), flush=True)


if __name__ == "__main__":
    main()
