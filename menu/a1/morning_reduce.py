"""One command for the morning every table the night's arms can produce, each only if its inputs are
delivered (a missing delivery is SAID, never faked). Read-only on the factories; writes under the summary dirs.

    python morning_reduce.py

- the A1 ladder on the spot set: solo · gate · gate+review (misses named per cell), plus the team arms and the window arm
- the delegation corpus over every delivered team arm
- S10b select (the lead's checks on the tablets' candidates) with the S10a+b residue from the MoE's own f2 rows
- the directed S10c rigby arm beside the GPU arms"""
import glob
import os
import pathlib
import subprocess
import sys

for _s in ("stdout", "stderr"):                      # cp1252 console: the reducers print non-ASCII glyphs
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                                    # noqa: BLE001
        pass
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import order_paths as op  # noqa: E402

import menu_paths as MP  # noqa: E402  the tablet desk: a setting
OUTBOX = pathlib.Path(MP.need("OUTBOX"))
ARMS = {
    "solo": "a1v22-py-agentic-hai-q38-best-thon-spot.20261006-0823",
    "team": "a1v22-py-agentic-hai-q38-best-thon-spot-team-bi.20261006-0823",
    "dir": "a1v22-py-agentic-hai-q38-best-thon-spot-team-bi-dir.20261006-1102",
    "must": "a1v22-py-agentic-hai-q38-best-thon-spot-team-bi-must.20261006-1450",
    "gate": "a1v22-py-agentic-hai-q38-best-thon-spot-gate.20261006-1735",
    "gate-rev": "a1v22-py-agentic-hai-q38-best-thon-spot-gate-rev-bi.20261006-1735",
    "must-win": "a1v22-py-agentic-hai-q38-best-thon-spot-team-bi-must-win.20261006-1900",
    # replicates (the ladder was n=1), the reviewer thinking ON, the behaviour arms, the v2.2 leads
    "solo-r2": "a1v22-py-agentic-hai-q38-best-thon-spot.20261007-0757",
    "gate-r2": "a1v22-py-agentic-hai-q38-best-thon-spot-gate.20261007-0757",
    "gate-rev-rthon": "a1v22-py-agentic-hai-q38-best-thon-spot-gate-rev-bi-rthon.20261007-0811",
    "b-recover": "a1v22-py-agentic-hai-q38-best-thon-spot-brecover.20261007-0836",
    "b-correct": "a1v22-py-agentic-hai-q38-best-thon-spot-bcorrect.20261007-0836",
    "b-plan": "a1v22-py-agentic-hai-q38-best-thon-spot-bplan.20261007-0836",
    "thoff": "a1v22-py-agentic-hai-q38-best-thoff-spot.20261007-0752",
    "mintools": "a1v22-py-agentic-q38-best-thon-spot.20261007-0752",
    "ornith": "a1v22-py-agentic-hai-ornith-best-spot.20261007-0757",
    "nemotron30": "a1v22-py-agentic-hai-nemotron30-best-spot.20261007-0752",
    "team-ri-dir": "a1v22-py-agentic-hai-q38-best-thon-spot-team-ri-dir.20261007-0859",   # rigby as the 3.8's helper
}
RIGBY = {"s10c-dir": "a1v22-rigby-moe-hai-off-spot-team-fe-dir.20261006-1934",
         "s10b": "s10b-rigby-moe-f2tests-s1.20261006-1804",
         "rigby-solo-spot": "a1v22-rigby-moe-hai-off-spot.20261006-1919",
         "rigby-solo-ngram": "a1v22-rigby-moe-hai-off-spot-spec-ngram-simple.20261006-2237",
         "rigby-gate": "a1v22-rigby-moe-hai-off-spot-team-fe-dir-gate.20261006-1919",
         "rigby-gate-rev": "a1v22-rigby-moe-hai-off-spot-team-fe-dir-gate-rev-fe.20261006-1919"}
S10B_RESIDUE = "hardbench-rigby-m-qwen3.6-35b-a3b-ud-q3-k-xl-f2-t06-off-s1.20260930-2254"


def gpu_delivered(name):
    recs = op.desk_records(name)
    return bool(recs and op.delivered(recs[0]))


def rigby_delivered(name):
    return bool(glob.glob(str(OUTBOX / ("%s.*.results.json" % name))))


def run(label, argv):
    print("\n=== %s: %s" % (label, " ".join(argv)), flush=True)
    r = subprocess.run([sys.executable] + argv, cwd=str(HERE), capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    print(r.stdout[-6000:])
    if r.returncode:
        print("!! rc %d\n%s" % (r.returncode, r.stderr[-2000:]))


def main():
    have = {k: v for k, v in ARMS.items() if gpu_delivered(v)}
    missing = sorted(set(ARMS) - set(have))
    print("GPU arms delivered: %s | not yet: %s" % (sorted(have), missing))
    rig = {k: v for k, v in RIGBY.items() if rigby_delivered(v)}
    print("rigby delivered: %s | not yet: %s" % (sorted(rig), sorted(set(RIGBY) - set(rig))))
    ladder = [have[k] for k in ("solo", "solo-r2", "gate", "gate-r2", "gate-rev", "gate-rev-rthon") if k in have]
    behaviour = [have[k] for k in ("solo-r2", "b-recover", "b-correct", "b-plan") if k in have]
    if any(k in have for k in ("b-recover", "b-correct", "b-plan")):
        run("behaviour arms (+ the solo replicate as their control)",
            ["reduce_a1.py"] + sum([["--gpu", n] for n in behaviour], []) + ["--label", "behaviour"])
    leads = [have[k] for k in ("solo", "thoff", "mintools", "ornith", "nemotron30") if k in have]
    if len(leads) > 1:
        run("leads (v2.2, spot set)", ["reduce_a1.py"] + sum([["--gpu", n] for n in leads], []) + ["--label", "leads"])
    if ladder:
        run("ladder (solo / gate / gate+review)", ["reduce_a1.py"] + sum([["--gpu", n] for n in ladder], []) + ["--label", "ladder"])
    teams = [have[k] for k in ("team", "dir", "team-ri-dir", "must", "must-win") if k in have]
    argv = ["reduce_a1.py"] + sum([["--gpu", n] for n in teams], [])
    rigby_arms = [rig[k] for k in ("rigby-solo-spot", "rigby-solo-ngram", "s10c-dir", "rigby-gate", "rigby-gate-rev") if k in rig]
    argv += sum([["--rigby", n] for n in rigby_arms], [])
    if teams or rigby_arms:
        run("team arms (+ the rigby ladder: solo spot / directed S10c / gate / gate+review)", argv + ["--label", "teams"])
    corpus = [have[k] for k in ("must", "must-win") if k in have]
    if corpus:
        run("delegation corpus", ["delegation_corpus.py"] + sum([["--gpu", n] for n in corpus], []))
    if "s10b" in rig:
        run("S10b select", [str(HERE.parent / "s10b_select.py"), "--lead", rig["s10b"], "--residue", S10B_RESIDUE])
    else:
        print("\nS10b: the lead order has not delivered - no select yet")


if __name__ == "__main__":
    main()
