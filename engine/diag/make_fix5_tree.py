"""Make the PROOF tree llama.cpp-620d222-r4-fix5: the lab engine's r4 source + lab patch #5 and nothing else.

It exists to prove the patch with the history probe before a lab revision is cut (`submit_fix5.py`). The revision
itself is made the documented way: the patch script on a fresh copy of the base tree, as `llama.cpp-620d222-r5`.

    python make_fix5_tree.py"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "llama.cpp-620d222-r4")
DST = os.path.join(HERE, "llama.cpp-620d222-r4-fix5")
PATCH = os.path.join(os.path.dirname(HERE), "patches", "vk_fa_dead_cells_patch.py")


def skip(d, names):
    return [n for n in names if n == ".git" or (n.startswith("build") and os.path.isdir(os.path.join(d, n)))]


if os.path.isdir(DST):
    shutil.rmtree(DST)
shutil.copytree(SRC, DST, ignore=skip)
r = subprocess.run([sys.executable, PATCH, DST], capture_output=True, text=True)
print(r.stdout.strip(), r.stderr.strip()[-400:])
raise SystemExit(r.returncode)
