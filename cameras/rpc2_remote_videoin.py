"""Ask the CAMERA (not the recorder) what it can do, through the NVR's RemoteDevVideoIn — READ ONLY.

THE OPENING: `system.listService` shows the NVR runs a **RemoteDevVideoIn** service whose methods
include `getCaps`, `getVideoFormat`, `getMaxResolutionId`, `setVideoFormat`, `setMaxResolutionId`
and `setCompression`. Those act on the REMOTE DEVICE. The pass concluded 2K30 was
impossible because the RECORDER's own Encode table refuses 2560x1440 -- but that table is the
recorder's policy, not the camera's capability, and this service talks to the camera.

Also probed: `RemoteMagicBox.getProductDefinition` (the CAMERA's definition -- the E841CA spec sheet
lists 5MP@30, so the camera should say so itself) and `VirtualHostProxy.getHttpProxyPort`, which on
Dahua NVRs hands out a proxied HTTP port onto each camera's own web UI. The 08-10 note "no Virtual
Host" came from three CONFIG KEYS returning 400; the SERVICE is present.

 NOTHING HERE WRITES. Every set* method is listed for the record and deliberately not called --
changing a camera's encoder is the owner's hardware and their call, and this device is already documented
as accepting values it will never emit, so any write needs a stream-level verification plan first.
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nvr_rpc2 import RPC2

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpc2_camera_caps.json")


def main():
    r = RPC2()
    r.login()
    dump = {}

    def call(method, params=None, obj=None, label=None):
        lbl = label or method
        try:
            res = r.call(method, params, object_id=obj)
        except Exception as e:
            print("  %-44s EXC %s" % (lbl, str(e)[:80]))
            return None
        if res.get("error"):
            print("  %-44s ERR %s" % (lbl, json.dumps(res["error"])[:110]))
            return None
        p = res.get("params")
        dump[lbl] = {"params": p, "result": res.get("result")}
        print("  %-44s OK  %s" % (lbl, (json.dumps(p)[:200] if p else "(empty)")))
        return res

    print("=== the CAMERA's own product definition (via the NVR) ===")
    for ch in (0, 1):
        call("RemoteMagicBox.getProductDefinition", {"channel": ch},
             label="RemoteMagicBox.getProductDefinition ch%d" % ch)
        call("RemoteMagicBox.getSoftwareVersion", {"channel": ch},
             label="RemoteMagicBox.getSoftwareVersion ch%d" % ch)

    print("\n=== RemoteDevVideoIn -- instance then query ===")
    for ch in (0, 1):
        inst = call("RemoteDevVideoIn.factory.instance", {"channel": ch},
                    label="RemoteDevVideoIn.factory.instance ch%d" % ch)
        obj = inst.get("result") if inst else None
        # Some firmwares take the channel on every call instead of an instance; try both.
        for method in ("getCaps", "getVideoFormat", "getMaxResolutionId", "getCompression"):
            m = "RemoteDevVideoIn." + method
            if obj:
                call(m, {"channel": ch}, obj=obj, label="%s ch%d (obj)" % (m, ch))
            else:
                call(m, {"channel": ch}, label="%s ch%d" % (m, ch))

    print("\n=== a proxied HTTP port onto the camera's own web UI? ===")
    call("VirtualHostProxy.getHttpProxyPort", None)
    for ch in (0, 1):
        call("VirtualHostProxy.getHttpProxyPort", {"channel": ch},
             label="VirtualHostProxy.getHttpProxyPort ch%d" % ch)

    print("\n=== remote device info / caps ===")
    call("RemoteDeviceManager.getCaps", None)
    call("RemoteDeviceManager.getDeviceInfoAll", None)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(dump, f, indent=1)
    print("\nwrote %s" % OUT)
    try:
        r.logout()
    except Exception:
        pass


if __name__ == "__main__":
    main()
