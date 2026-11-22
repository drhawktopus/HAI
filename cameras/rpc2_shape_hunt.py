"""Find the parameter shape for the services that reach the CAMERA — READ ONLY.

The error codes are informative and must be kept apart:
    268894210 "Method not found!" -> the verb does not exist. Stop.
    268894211 "Request invalid param!" -> verb EXISTS, wrong argument shape. Keep hunting.
    268894209 "Invalid Request!" -> verb exists, wants an object instance / different context.
Treating these as one "it failed" is how the pass concluded there was no route at all.

Targets, in order of what they would buy:
  PassThrough.call -- invoke an arbitrary RPC2 method ON the camera (the jackpot)
  DeviceConfigManager.getConfig -- read the camera's own config tables
  RemoteDevVideoIn.factory.instance -- the handle the video-format setters need
  VirtualHostProxy.getHttpProxyPort -- a proxied port onto the camera's web UI

The camera's identifiers, from LogicDeviceManager.getCameraAll / RemoteDevice:
  channel 0 -> 10.1.1.128, uuid:System_CONFIG_NETCAMERA_INFO_0, port 35000
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nvr_rpc2 import RPC2

UUID0 = "uuid:System_CONFIG_NETCAMERA_INFO_0"

TRIALS = [
    # ---- PassThrough.call : the generic camera RPC bridge
    ("PassThrough.call", {"channel": 0, "method": "magicBox.getProductDefinition", "params": None}),
    ("PassThrough.call", {"channel": 0, "data": {"method": "magicBox.getProductDefinition"}}),
    ("PassThrough.call", {"deviceID": UUID0, "method": "magicBox.getProductDefinition"}),
    ("PassThrough.call", {"channel": 0, "cmd": "magicBox.getProductDefinition"}),
    ("PassThrough.call", {"uniqueChannel": 0, "method": "magicBox.getProductDefinition"}),
    # ---- DeviceConfigManager : the camera's own config tables
    ("DeviceConfigManager.getConfig", {"channel": 0, "name": "Encode"}),
    ("DeviceConfigManager.getConfig", {"channel": 0, "name": ["Encode"]}),
    ("DeviceConfigManager.getConfig", {"deviceID": UUID0, "name": "Encode"}),
    ("DeviceConfigManager.getConfig", {"name": "Encode", "uniqueChannel": 0}),
    ("DeviceConfigManager.getConfig", {"channels": [0], "name": "Encode"}),
    # ---- the video-format handle
    ("RemoteDevVideoIn.factory.instance", {"Channel": 0}),
    ("RemoteDevVideoIn.factory.instance", {"channel": 0, "deviceID": UUID0}),
    ("RemoteDevVideoIn.factory.instance", {"uniqueChannel": 0}),
    ("RemoteDevVideoIn.factory.instance", {"deviceID": UUID0}),
    ("RemoteDevVideoIn.factory.instance", {"channel": 0, "stream": 0}),
    # ---- a proxied HTTP port onto the camera UI
    ("VirtualHostProxy.getHttpProxyPort", {"deviceID": UUID0}),
    ("VirtualHostProxy.getHttpProxyPort", {"uniqueChannel": 0}),
    ("VirtualHostProxy.getHttpProxyPort", {"channel": 0, "type": "http"}),
    ("VirtualHostProxy.getHttpProxyPort", {"channels": [0]}),
    # ---- transparent transfer (raw bytes to the device)
    ("TransparentTransfer.transfer", {"channel": 0, "data": ""}),
    # ---- remote device manager, with an id
    ("RemoteDeviceManager.getDeviceInfo", {"deviceID": UUID0}),
    ("RemoteDeviceManager.getDeviceNames", None),
    ("RemoteDeviceManager.getDevices", None),
]


def main():
    r = RPC2()
    r.login()
    hits = {}
    for method, params in TRIALS:
        label = "%s %s" % (method, json.dumps(params)[:70] if params else "")
        try:
            res = r.call(method, params)
        except Exception as e:
            print("  %-78s EXC %s" % (label, str(e)[:60]))
            continue
        err = res.get("error")
        if err:
            code = err.get("code")
            tag = {268894210: "NO-SUCH-METHOD", 268894211: "BAD-PARAMS",
                   268894209: "NEEDS-CONTEXT"}.get(code, "ERR %s" % code)
            print("  %-78s %s" % (label, tag))
        else:
            p = res.get("params")
            hits[label] = {"params": p, "result": res.get("result")}
            print("  %-78s ✅ OK  %s" % (label, (json.dumps(p)[:160] if p else "(empty)")))

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpc2_shape_hits.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(hits, f, indent=1)
    print("\n%d shape(s) answered -> %s" % (len(hits), out))
    try:
        r.logout()
    except Exception:
        pass


if __name__ == "__main__":
    main()
