"""Submit the LAB ENGINE build order (#34 B11b stage 2, engine parity) to Mothership through the desk.

    python submit_labengine.py [engine tag, default 620d222]      e.g. 620d222-r3 = a lab revision's own source tree

Mothership is the build box (VS 2022 Build Tools, CUDA 13.0, Vulkan SDK 1.4.363). The order builds the NVIDIA and AMD
engine sets from the patched lab-engine\\llama.cpp-<tag> tree on the share and delivers them to
campaigns\\labengine-<date>\\<order>\\; it installs nothing (payload: workspace\\labengine\\build_lab_engine.py). The tag
is the source tree's own name, and fleet_engine.tag_of reads it back from the build's engine.json (one name everywhere)."""
import datetime
import json
import os
import re
import sys
import urllib.request
import engine_settings as E   # the head's URL and the share, from the lab's settings

KIRK = E.KIRK
USER = "ClaudeSupervisor"
DAY = datetime.datetime.now().strftime("%Y%m%d")
TAG = sys.argv[1] if len(sys.argv) > 1 else "620d222"
assert re.fullmatch(r"[0-9a-f]{7,}(?:-r\d+)?", TAG), "an engine tag is a short commit, optionally -r<N>: %r" % TAG
assert os.path.isdir(os.path.join(os.path.join(E.HERE), "llama.cpp-" + TAG)), "no source tree llama.cpp-%s" % TAG
SRC = E.engine_unc("llama.cpp-" + TAG)
UNC_DIR = E.campaigns("labengine-%s" % DAY)


def post(path, body):
    req = urllib.request.Request(KIRK + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


if __name__ == "__main__":
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    # "-compat": built with ollama's llama/compat layer (apply_ollama_compat.py); the 07:34 build had none
    spec = {"name": "labengine-%s-compat.%s" % (TAG, stamp), "mode": "compute", "load": "cpu", "initiator": USER,
            "payload": "{FOUNDRY_ROOT}\\workspace\\labengine\\build_lab_engine.py",
            "args": [SRC, "{ORDER_OUT}"], "deliver_to": UNC_DIR, "notify": "failures"}
    # rush: someone is waiting on this build - the lab's own way to skip the cheap-power window (without it the order
    # waits for power under the price ceiling)
    body = {"spec": spec, "requires": ["order.compute"], "only": ["mothership"], "user": USER, "rush": True}
    print(json.dumps(post("/p20/order", body), indent=1))
