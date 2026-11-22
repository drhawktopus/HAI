"""POSITIVE CONTROL for the yolo11 bench. Run BEFORE believing its "no cat" result.

The matrix found zero cats in eight cells. That is either a real finding about yolo11 on IR
footage, or my binary/model/decode is broken and every negative is void. Those look identical
from the results alone, which is exactly the failure this lab keeps paying for -- a matcher
that cannot match anything reports a confident NOT FOUND.

Three controls, cheapest first:
  control.jpg          the Fellowship's own live decoy, with a KNOWN expected label already on
                       the tablet in ~/det/control.expect -- if yolo misses this, the binary is
                       the problem, not the footage
  night_crop_0343_hq   the known IR cat crop that mobilenet scores on. If yolo misses THIS, the
                       model is the problem, not the frame size
  src_1080 at floor 0  every candidate, unfiltered, so "cat scored 0.03" and "cat never appeared"
                       stay distinguishable
"""
import sys

sys.path.insert(0, "/app")
import tablet_foundry as tf  # noqa: E402

HOST = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: positive_control.py <tablet>  (its roster name)")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

rc, out = tf._ssh(HOST, "cat ~/det/control.expect 2>/dev/null; ls ~/det/", timeout=30)
print("=== what the tablet already holds as ground truth ===")
print(out)

# Put the known IR cat crop next to the bench binary.
rc, _ = tf._ssh(HOST, "cp ~/det/night_crop_0343.jpg ~/bench/ctrl_cat.jpg 2>/dev/null; "
                      "cp ~/det/control.jpg ~/bench/ctrl_decoy.jpg 2>/dev/null; ls -la ~/bench/",
                timeout=30)

for label, img, floor in [
    ("DECOY (known-good, mobilenet passes it)", "ctrl_decoy.jpg", "0.05"),
    ("IR CAT CROP (known positive)", "ctrl_cat.jpg", "0.01"),
    ("full 1080 frame, floor 0.001", "src_1080.jpg", "0.001"),
]:
    rc, out = tf._ssh(
        HOST, "cd ~/bench && ./yolo11_bench yolo11n 640 %s %s 3 2>&1" % (img, floor),
        timeout=300)
    print("\n=== %s  (%s) rc=%s ===" % (label, img, rc))
    lines = [l.strip() for l in (out or "").splitlines() if l.strip()]
    # print everything -- a truncated control is not a control
    for line in lines[:40]:
        print("   " + line)
