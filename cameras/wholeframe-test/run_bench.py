"""Run the bigger-input model matrix on ONE tablet. Executes INSIDE the tablet-foundry container.

 with a ceiling of
3-4 cameras. So every cell reports BOTH ms and detections, and the summary converts ms into the
only unit that decides anything here -- how many cameras the Fellowship could carry.

 CONFOUND, STATED UP FRONT: Frigate is serving live detections on these same tablets (~1.1/s per
node at ~250 ms, roughly a 28% duty cycle), so the bench contends with real work. That makes these
numbers PESSIMISTIC. min is the closer estimate of uncontended speed, median the closer estimate of
what we would actually get in service. Neither is quoted alone.

One tablet only: this is a measurement, not a deployment, and taking the whole Fellowship out of
rotation to time a model would be the cure being worse than the disease.
"""
import sys

sys.path.insert(0, "/app")      # /tabfoundry is the DATA dir; the module lives in /app
import tablet_foundry as tf  # noqa: E402

HOST = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: run_bench.py <tablet>  (its roster name)")
STAGE = "/ssdshared/tabfoundry-bench"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FILES = ["yolo11_bench", "yolo11n.ncnn.param", "yolo11n.ncnn.bin",
         "yolo11s.ncnn.param", "yolo11s.ncnn.bin",
         "src_d1.jpg", "src_1080.jpg", "src_4k.jpg", "src_eventsnap.jpg"]

print("=== staging to %s ===" % HOST)
rc, _ = tf._ssh(HOST, "mkdir -p ~/bench", timeout=30)
if rc != 0:
    print("FAIL: cannot mkdir ~/bench on %s" % HOST)
    sys.exit(1)

import os  # noqa: E402
for name in FILES:
    local = os.path.join(STAGE, name)
    want = os.path.getsize(local)
    rc, have = tf._ssh(HOST, "stat -c%%s ~/bench/%s 2>/dev/null || echo 0" % name, timeout=30)
    if rc == 0 and (have or "").strip().isdigit() and int(have.strip()) == want:
        print("  %-22s already current (%d bytes)" % (name, want))
        continue
    # write beside then rename: the binary may be executing from a previous cell
    rc, err = tf._scp(HOST, local, "bench/%s.new" % name)
    if rc != 0:
        print("  %-22s SCP FAILED: %s" % (name, err))
        sys.exit(1)
    tf._ssh(HOST, "mv -f ~/bench/%s.new ~/bench/%s" % (name, name), timeout=60)
    print("  %-22s pushed %d bytes" % (name, want))
tf._ssh(HOST, "chmod +x ~/bench/yolo11_bench", timeout=30)

print()
print("=== matrix: {yolo11n, yolo11s} x {src_d1, src_1080, src_4k, src_eventsnap} @ imgsz 640 ===")
print()
REPS = 12
for model in ("yolo11n", "yolo11s"):
    for img in ("src_d1", "src_1080", "src_4k", "src_eventsnap"):
        cmd = ("cd ~/bench && ./yolo11_bench %s 640 %s.jpg 0.05 %d 2>&1"
               % (model, img, REPS))
        rc, out = tf._ssh(HOST, cmd, timeout=600)
        print("--- %s / %s (rc=%s) ---" % (model, img, rc))
        for line in (out or "").splitlines():
            line = line.strip()
            if line.startswith(("SPEED", "DET", "MODEL", "WARNING")) or "fail" in line.lower():
                print("   " + line)
        print()
