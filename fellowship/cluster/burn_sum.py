import glob
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # the fellowship folder
import roster  # noqa: E402

_T = roster.hosts()
NAMES = {_T["gandalf"]: "gandalf   X6C6F", _T["frodo"]: "frodo     X606F", _T["sam"]: "sam       X606F",
         _T["pippin"]: "pippin    X505F", _T["merry"]: "merry     X505F"}

SC = os.path.dirname(os.path.abspath(__file__))
tot = 0.0
cores = 0
rows = []
for f in sorted(glob.glob(os.path.join(SC, "burn_*.json"))):
    ip = os.path.basename(f)[5:-5]
    try:
        d = json.load(open(f))
    except Exception:
        rows.append((NAMES.get(ip, ip), 0, 0.0, 0.0, "NO RESULT"))
        continue
    tot += d["gflops"]
    cores += d["cores"]
    rows.append((NAMES.get(ip, ip), d["cores"], d["gflops"], d["per_core"], ""))

print("%-18s %6s %12s %11s" % ("node", "cores", "GFLOP/s", "per-core"))
for n, c, g, pc, err in rows:
    print("%-18s %6d %12.2f %11.2f %s" % (n, c, g, pc, err))
print("-" * 52)
print("%-18s %6d %12.2f" % ("CLUSTER TOTAL", cores, tot))
print("\nvs Wiglaf (1994 Beowulf, 16 nodes, 74 MFLOP/s): %.0fx" % (tot * 1000 / 74))
