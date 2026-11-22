"""Read the CAMERA's own config tables via configManager + {target: N} — READ ONLY.

Shape lifted from the NVR's web UI (`/app/jsCore/RPC.js`):

    getConfigTarget = (name, ch, x, target) =>
        send("configManager.getConfig", {name, channel}, {target: target})
    setConfigTarget = (...) =>
        send("configManager.setConfig", payload, {target: target})

`target` is the extra that redirects a config operation at a REMOTE device instead of the recorder.
That is the write path to the camera, and it is what the"there is NO way round it"
conclusion missed -- that pass only ever tried CGI, whose write path on this unit is a documented
lying success.

 READ ONLY. If the camera's own Encode table turns out to offer 2592x1944 or 2560x1440 where the
recorder's does not, the WRITE is still the owner's call, and it needs a stream-level verification plan
because this device is already on record accepting values it never emits.
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nvr_rpc2 import RPC2

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera_own_config.json")
NAMES = ["Encode", "VideoInMode", "EncodeCaps", "VideoStandard"]


def resolutions(blob):
    """Pull every WidthxHeight pair mentioned, so the vocabulary is visible at a glance."""
    import re
    out = set()
    for m in re.finditer(r'"Width"\s*:\s*(\d+)\s*,\s*"Height"\s*:\s*(\d+)', blob):
        out.add("%sx%s" % (m.group(1), m.group(2)))
    for m in re.finditer(r'"CustomResolutionName"\s*:\s*"([^"]+)"', blob):
        out.add("name=" + m.group(1))
    return sorted(out)


def main():
    r = RPC2()
    r.login()
    dump = {}

    print("=== recorder's OWN Encode table (baseline) ===")
    base = r.call("configManager.getConfig", {"name": "Encode"})
    if not base.get("error"):
        blob = json.dumps(base.get("params"))
        dump["recorder_Encode"] = base.get("params")
        print("  resolutions seen:", ", ".join(resolutions(blob)) or "(none parsed)")

    for target in (0, 1):
        print("\n=== target=%d  (the CAMERA on that channel) ===" % target)
        for name in NAMES:
            res = r.call("configManager.getConfig", {"name": name},
                         extra={"target": target})
            label = "target%d.%s" % (target, name)
            if res.get("error"):
                print("  %-28s ERR %s" % (name, json.dumps(res["error"])[:95]))
                continue
            p = res.get("params")
            dump[label] = p
            blob = json.dumps(p)
            rs = resolutions(blob)
            print("  %-28s OK  %d bytes  %s" % (name, len(blob),
                                                (", ".join(rs) if rs else "")))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(dump, f, indent=1)
    print("\nwrote %s" % OUT)
    try:
        r.logout()
    except Exception:
        pass


if __name__ == "__main__":
    main()
