"""Ask the CAMERA for its own product definition, through the NVR — READ ONLY.

THE KEY THAT UNLOCKED IT: the NVR's own web UI JavaScript (`/app/jsCore/RPC.js`, served without
auth) shows the instance parameter is **{uuid: <deviceUUID>}**, not {channel: N}:

    RemoteMagicBox.getProductDefinition = function(a,c){ return d.instance({uuid:a}).then(...) }
    RemoteDeviceManager.getCaps = function(){ return this.instance(null,{subClassID:""}) }

23 guessed shapes failed before reading this. -- and the DEVICE'S OWN CLIENT is the
best manual of all, because it cannot be out of date with the firmware it ships with.

WHY IT MATTERS: the E841CA spec sheet lists 5MP@30 (Lorex market 5MP as "2K"), and the RECORDER's
Encode table refuses 2560x1440. Those are different claims about different devices. This asks the
camera directly.
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nvr_rpc2 import RPC2

UUIDS = ["uuid:System_CONFIG_NETCAMERA_INFO_0", "uuid:System_CONFIG_NETCAMERA_INFO_1"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera_definition.json")


def main():
    r = RPC2()
    r.login()
    dump = {}

    for uu in UUIDS:
        print("=== %s ===" % uu)
        inst = r.call("RemoteMagicBox.factory.instance", {"uuid": uu})
        if inst.get("error"):
            print("  factory.instance ERR %s" % json.dumps(inst["error"])[:120])
            continue
        obj = inst.get("result")
        print("  instance -> object %s" % obj)

        for method in ("RemoteMagicBox.getProductDefinition",
                       "RemoteMagicBox.getSoftwareVersion",
                       "RemoteMagicBox.getState"):
            res = r.call(method, None, object_id=obj)
            if res.get("error"):
                print("  %-40s ERR %s" % (method, json.dumps(res["error"])[:100]))
                continue
            p = res.get("params")
            dump["%s|%s" % (uu, method)] = p
            print("  %-40s OK" % method)
            # surface anything resolution-shaped without dumping the whole blob
            blob = json.dumps(p)
            for key in ("Resolution", "MaxResolution", "VideoInResolution", "5M", "2592",
                        "2560", "1440", "1944", "MaxWidth", "MaxHeight", "MaxFps", "FrameRate"):
                idx = blob.find(key)
                if idx > -1:
                    print("       ...%s" % blob[max(0, idx - 70):idx + 130].replace("\n", " "))
                    break
        print()

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(dump, f, indent=1)
    print("wrote %s" % OUT)
    try:
        r.logout()
    except Exception:
        pass


if __name__ == "__main__":
    main()
