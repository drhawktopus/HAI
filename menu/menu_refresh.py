r"""#34 Phase 7, SELF-SUSTAINING: the lab regenerates Spock's placement table when the menu's inputs change - no Claude.

    python menu_refresh.py # rebuild only if an input changed since the last good refresh
    python menu_refresh.py --force # rebuild now

Run by the scheduled task "HAI P34 menu refresh" on Softserve (menu_refresh.cmd; AtStartup + 15-min repetition,
IgnoreNew, RestartCount 3, no time limit - the lab's standard for on-box machinery, as "HAI P34 menu fill" was).

The chain is the documented manual one: hard_bench_fleet_tv.py reduce -> build_menu.py (re-grades only NEW answers - grade_cache.jsonl) ->
menu_standings.py -> menu_policy.py (atomic write; Spock re-reads policy.json on its mtime). The fingerprint covers what
those three read: every delivered result file under campaigns\ and outbox\ (progress markers and partial files are
work in flight, not deliveries), the payloads (a scorer change re-keys the grades), the hold and void files, the role
summaries and chat1 standings. A failed step never advances the fingerprint, so the next run retries it; the log says
which step failed and its last lines. State: Datasets\Project34-Menu\refresh-state.json. Log: menu_refresh.log.

AFTER the chain a derived product is kept current: menu_matrix.py writes model-matrix.csv + model-matrix.md
(every measured config, ranked on the three lenses with menu_policy's own rules). It is NOT a chain step: a matrix fault
must not hold the placement table's fingerprint and re-run the whole chain every 15 minutes. Instead it runs whenever its
output is missing or older than policy.json or its own code, so it retries each cycle until it succeeds, and a failure is
logged loudly and makes the run exit 1."""
import glob
import hashlib
import json
import os
import subprocess
import sys
import time

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                                       # noqa: BLE001
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import menu_paths as MPATH  # noqa: E402  every folder and mirror the menu reads
MENU = MPATH.MENU
STATE = os.path.join(MENU, "refresh-state.json")
LOG = os.path.join(HERE, "menu_refresh.log")
TREES = (MPATH.CAMPAIGNS, MPATH.OUTBOX, MPATH.PAYLOADS)
FILES = (os.path.join(HERE, "menu_fill_hold.json"), os.path.join(HERE, "menu_fill_auto_holds.json"),
         os.path.join(HERE, "menu_policy.py"), os.path.join(HERE, "menu_fill.py"),
         os.path.join(MPATH.ORCH_MIRROR, "model_fit.py"), os.path.join(MPATH.FOUNDRY_MIRROR, "gpu_spill.py"),
         os.path.join(HERE, "menu_void.json"))
# chat1_standings.csv left the FILES tuple the chain now WRITES it (reduce_chat1.py below), and an output in
# the fingerprint re-triggers the next run forever; its inputs (the CHAT1 deliveries under campaigns\ and outbox\) are
# fingerprinted already.
GLOBS = (os.path.join(MPATH.FOUNDRY_MIRROR, "workspace", "a1", "summary", "*-phase7-roles-*.json"),)
# (script, args). The tool/vision reducer runs FIRST: build_menu reads tool and vision cells only through the
# fleet_tv_rows.csv it writes, so the reducer must be in this chain or new tool deliveries sit unread.
CHAIN = (("hard_bench_fleet_tv.py", ("reduce",)), ("reduce_chat1.py", ()), ("build_menu.py", ()),
         ("menu_standings.py", ()), ("menu_policy.py", ()))
# reduce_chat1.py is in the chain for the same reason: menu_policy reads chat1_standings.csv, which only this
# reducer writes.


def step_names():
    return [s for s, _a in CHAIN]


def in_flight(path):
    p = path.replace("/", "\\").lower()
    return ".progress\\" in p or p.endswith(".partial.jsonl") or p.endswith(".tmp")


def fingerprint():
    """(sha, n_files): what the chain reads, by path + size + mtime. A file the walk cannot stat is skipped."""
    h, n = hashlib.sha256(), 0
    entries = []
    for root in TREES:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                p = os.path.join(dirpath, f)
                if in_flight(p) or not (f.endswith(".json") or f.endswith(".jsonl") or f.endswith(".py")):
                    continue
                entries.append(p)
    entries += [p for p in FILES if os.path.exists(p)]
    for g in GLOBS:
        entries += glob.glob(g)
    for p in sorted(entries):
        try:
            st = os.stat(p)
        except OSError:
            continue
        h.update(("%s|%d|%d\n" % (p, st.st_size, int(st.st_mtime))).encode("utf-8", "replace"))
        n += 1
    assert n, "zero input files fingerprinted - the trees are unreachable, not empty"
    return h.hexdigest(), n


def log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def run_chain(runner=subprocess.run):
    """-> (ok, step, seconds per step, tail of the failing step's output)."""
    took = {}
    for step, args in CHAIN:
        t0 = time.time()
        r = runner([sys.executable, os.path.join(HERE, step)] + list(args), cwd=HERE, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
        took[step] = round(time.time() - t0, 1)
        if r.returncode != 0:
            tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-8:]
            return False, step, took, tail
        log("  %s ok in %.1f s" % (step, took[step]))
    return True, None, took, []


POLICY = os.path.join(MENU, "policy.json")
MATRIX_OUT = os.path.join(MENU, "model-matrix.csv")
MATRIX_STEP = "menu_matrix.py"


def matrix_stale():
    """True when the matrix must be (re)built: output missing, or older than the policy it derives from or its own code.
    No policy.json yet = nothing to derive = not stale (the chain has not produced one)."""
    try:
        newest_input = max(os.path.getmtime(POLICY), os.path.getmtime(os.path.join(HERE, MATRIX_STEP)))
    except OSError:
        return False
    try:
        return os.path.getmtime(MATRIX_OUT) < newest_input
    except OSError:
        return True


def run_matrix(runner=subprocess.run):
    """-> True when the matrix is current afterwards. A failure is logged with the step's last lines, never swallowed."""
    t0 = time.time()
    r = runner([sys.executable, os.path.join(HERE, MATRIX_STEP)], cwd=HERE, capture_output=True, text=True,
               encoding="utf-8", errors="replace")
    if r.returncode != 0:
        log("FAILED at %s (the placement table is unaffected; retried next cycle):" % MATRIX_STEP)
        for t in ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-8:]:
            log("    | " + t)
        return False
    log("  %s ok in %.1f s" % (MATRIX_STEP, time.time() - t0))
    return True


def main(argv, runner=subprocess.run):
    rc = refresh(argv, runner)
    if rc == 0 and matrix_stale():
        rc = 0 if run_matrix(runner) else 1
    return rc


def refresh(argv, runner=subprocess.run):
    fp, n = fingerprint()
    prev = read_state()
    if prev.get("fingerprint") == fp and "--force" not in argv:
        print("no change since %s (%d inputs)" % (prev.get("refreshed"), n))
        return 0
    log("refresh: %d inputs, fingerprint %s (was %s)" % (n, fp[:12], (prev.get("fingerprint") or "none")[:12]))
    ok, step, took, tail = run_chain(runner)
    if not ok:
        log("FAILED at %s after %s - fingerprint NOT advanced, the next run retries:" % (step, took))
        for t in tail:
            log("    | " + t)
        return 1
    state = {"fingerprint": fp, "inputs": n, "refreshed": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": took}
    with open(STATE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    os.replace(STATE + ".tmp", STATE)
    log("refreshed: policy.json regenerated (%s)" % ", ".join("%s %.0f s" % kv for kv in took.items()))
    try:
        with open(os.path.join(MENU, "build-manifest.json"), encoding="utf-8") as f:
            unread = json.load(f).get("unread_deliveries") or []
    except (OSError, ValueError):
        unread = None
    if unread is None:
        log("UNREAD DELIVERIES: could not read build-manifest.json - unchecked")
    elif unread:
        log("UNREAD DELIVERIES: %d - the desk says done, the menu found no out.json: %s" % (len(unread), ", ".join(unread)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
