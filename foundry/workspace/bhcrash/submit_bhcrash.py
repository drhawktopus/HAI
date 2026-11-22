"""Submit BigHonker 3070 time-to-failure runs (bh_crash_ttf.py) to the desk, pinned to BigHonker. One compute order per run.
Usage: submit_bhcrash.py <arm> <runs> [--budget=900] [--clock=unlocked|MHz] [--fan=PCT] [--load=gemm|llm] [--pl=W]
                         [--graphs=off] [--restore=unlocked|MHz] [--dry]
Payload flags (--fan --load --pl --graphs --restore) pass through. Arms define the single variable under test:
  llm-base  --load=llm                    dense-LLM load (baseline failure mode)
  llm-pl    --load=llm --pl=<W>           power-limited (isolates transient impact)
  llm-nogr  --load=llm --graphs=off       CUDA graphs disabled (isolates graph overhead)"""
import datetime
import json
import os
import sys
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))        # the foundry mirror root
sys.path.insert(0, _ROOT)
import foundry_paths as _P             # this workspace's ONE settings reader

KIRK = _P.head_url()                    # '' = no head linked: there is nowhere to submit to
UNC = _P.setting("SSD_SHARED_UNC", "") + r"\mothership-build\workspace\bhcrash"
if not KIRK or not _P.setting("SSD_SHARED_UNC", ""):
    sys.exit("no head linked (HAI_URL) or no shared staging (SSD_SHARED_UNC): nothing to submit")
args = [a for a in sys.argv[1:] if not a.startswith("--")]
arm, runs = args[0], int(args[1])
budget = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--budget=")), "900")
clock = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--clock=")), "unlocked")
# BigHonker enforces an 180 W power limit (section 9j). To run a stock baseline, pass --pl=280 explicitly.
# The payload restores 180 W afterwards (override with --restore-pl).
PASS = ("--fan=", "--load=", "--pl=", "--graphs=", "--restore=", "--restore-pl=")
passthrough = [a for a in sys.argv if a.startswith(PASS)]
stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
for r in range(1, runs + 1):
    name = "bhcrash-%s-r%d.%s" % (arm, r, stamp)
    body = {"spec": {"name": name, "mode": "compute", "load": "gpu", "initiator": "ClaudeSupervisor",
                     "payload": "{FOUNDRY_ROOT}\\workspace\\bhcrash\\bh_crash_ttf.py",
                     "args": ["{ORDER_OUT}", arm, budget, clock] + passthrough,
                     "deliver_to": UNC + "\\delivered",
                     # the order owns the card's clock while it runs: the health daemon stands its re-lock down
                     # for exactly this order (p20_health_daemon.clock_owner) and resumes when the run ends
                     "gpu_clock": clock,
                     "notify": "failures", "progress_total": int(budget)},
            "requires": ["order.compute"], "only": ["bighonker"], "user": "ClaudeSupervisor"}
    if "--dry" in sys.argv:
        print("DRY", name, budget, clock)
        continue
    req = urllib.request.Request(KIRK + "/p20/order", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        reply = json.loads(resp.read().decode("utf-8", "replace"))
    print("%-44s -> %s %s" % (name, "ok" if reply.get("ok") else "FAILED", reply.get("error") or ""))
