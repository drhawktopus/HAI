r"""Cut a LAB REVISION's source tree: a fresh copy of the previous revision's tree + one lab patch script.

    python make_revision_tree.py <from tag> <to tag> <patch script> "<what this revision is, in one or two sentences>"
    e.g. python make_revision_tree.py 620d222-r5 620d222-r6 patches\some_patch.py "r5 + lab patch #6: ..."

Writes the new tree's own record, LAB-REVISION-<to>/README.txt: what it is, and the patch script with its sha256 at
the moment it was applied, so the tree still says what was built after the script changes on disk.

The rule (the runbook's "A LAB REVISION is its own engine tag"): the tree is made by running the patch script on a
fresh copy, never by editing a tree a live build came from. Refuses when the new tree exists. Then:
`python submit_labengine.py <to tag>`, `fleet_deploy.py --engine <delivered dir>`, the resume proof."""
import os
import shutil
import subprocess
import sys

import datetime
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
if len(sys.argv) != 5 or not sys.argv[4].strip():
    raise SystemExit(__doc__)
src, dst, patch = (os.path.join(HERE, "llama.cpp-" + sys.argv[1]), os.path.join(HERE, "llama.cpp-" + sys.argv[2]), sys.argv[3])
what = sys.argv[4].strip()
assert os.path.isdir(src), "no source tree %s" % src
assert not os.path.exists(dst), "%s exists already: a revision's tree is made once" % dst
assert os.path.isfile(patch), "no patch script %s" % patch


def skip(d, names):
    return [n for n in names if n == ".git" or (n.startswith("build") and os.path.isdir(os.path.join(d, n)))]


shutil.copytree(src, dst, ignore=skip)
r = subprocess.run([sys.executable, patch, dst], capture_output=True, text=True)
print(r.stdout.strip(), r.stderr.strip()[-400:])
if r.returncode != 0 or "patched" not in r.stdout or "already patched" in r.stdout:
    shutil.rmtree(dst, ignore_errors=True)
    raise SystemExit("the patch did not apply freshly; %s removed" % dst)
with open(patch, "rb") as f:
    patch_sha = hashlib.sha256(f.read()).hexdigest()
rec = os.path.join(dst, "LAB-REVISION-" + sys.argv[2].split("-")[-1])
os.makedirs(rec, exist_ok=True)
with open(os.path.join(rec, "README.txt"), "w", encoding="utf-8") as f:
    f.write("llama.cpp-%s = llama.cpp-%s + %s\n\n%s\n\nPatch script: %s\nsha256 when applied: %s (%s)\n"
            "The tree is the record of what was built: if the script changes later, reproduce from this tree.\n"
            % (sys.argv[2], sys.argv[1], os.path.basename(patch), what, patch, patch_sha,
               datetime.datetime.now().isoformat(timespec="seconds")))
print("made", dst, "| record", os.path.join(rec, "README.txt"))
