r"""Write the speed probe's rewrite_12k prompt as plain text, for an imatrix routing count of the probe's own tokens
(the r62 hot-store known-positive check: does r60's A1 hot set overlap the probe's?).

    python probe_text.py <speed_rewrite_probe.py> <out.txt>
"""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("probe", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
text = mod.TASKS["rewrite_12k"]["prompt"]
with open(sys.argv[2], "w", encoding="utf-8", newline="\n") as f:
    f.write(text)
print("wrote %d chars (~%d tokens at 3.3 chars/token) -> %s" % (len(text), len(text) / 3.3, sys.argv[2]))
