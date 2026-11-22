"""Add the early-stop rule (a1_stop) to QUEUED A1 orders' plan files before they run.
A plan is read when its order starts, so amending it before then changes the order before fulfilment. Each plan is
backed up beside itself and gets an `amended` note; a plan already carrying a rule is left alone.

    python amend_stop_rule.py <baseline order> <plan name> [<plan name>...] [--go]"""
import json
import os
import shutil
import sys

import a1_stop
import reduce_a1

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the menu folder
import menu_paths as MP  # noqa: E402
sys.path.insert(0, MP.FOUNDRY_MIRROR)
import foundry_paths as _P  # noqa: E402   the foundry's one settings reader
LOCAL = os.path.join(MP.FOUNDRY_MIRROR, "workspace", "a1")
UNC = _P.setting("SSD_SHARED_UNC", "") + "\\%s\\workspace\\a1" % os.path.basename(MP.FOUNDRY_MIRROR)
if not _P.setting("SSD_SHARED_UNC", ""):
    sys.exit("no shared staging (SSD_SHARED_UNC): no stop rule to write")
PLANS = os.path.join(LOCAL, "plans")

args = [a for a in sys.argv[1:] if not a.startswith("--")]
go = "--go" in sys.argv
baseline, names = args[0], args[1:]
assert names, "name at least one plan"
rule = a1_stop.make_rule(baseline, UNC, reduce_a1.NON_DISCRIMINATING)
for n in names:
    p = os.path.join(PLANS, n + ".json")
    plan = json.load(open(p, encoding="utf-8"))
    if plan.get("behaviour"):
        print("SKIP %s: a behaviour arm is not judged on solves" % n)
        continue
    if plan.get("stop_rule"):
        print("SKIP %s: already has a stop rule" % n)
        continue
    print("%s %s  <- stop vs %s" % ("AMEND" if go else "WOULD AMEND", n, baseline))
    if go:
        shutil.copy2(p, p + ".bak-20261008-stop")
        plan["stop_rule"] = rule
        plan["amended"] = "2026-10-08: stop_rule added before the order ran (a1_stop; the owner 'Yes option 1')"
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=1)
        os.replace(tmp, p)
