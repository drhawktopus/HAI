"""Find the route to the CAMERA's own config through the NVR — READ ONLY.

`system.listService` on this box lists **PassThrough**, **VirtualHostProxy**,
**TransparentTransfer**, **RemoteDeviceManager** and **RemoteDevVideoIn**. The pass
concluded there was "no Dahua Virtual Host" from three CONFIG KEYS returning 400 — but a config key
is not a service. That is the lab's own documented trap (an absent VALUE never proves an absent
FEATURE) applied to the very device the trap was first written about.

Two questions here, both read-only:
  1. What methods do those services expose? A pass-through would let us configure the camera at
     10.1.1.128:35000 directly, which is where 2K30 actually lives.
  2. `encode.getConfigCaps` exists (it answers "Request invalid param!", NOT "Method not found") —
     find the parameter shape it wants, because it enumerates the VALID encoder modes and would say
     whether 2560x1440@30 is refused by policy or absent by capability.
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nvr_rpc2 import RPC2

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpc2_passthrough_dump.json")

SERVICES = ["PassThrough", "VirtualHostProxy", "TransparentTransfer", "VDPPassThrough",
            "RemoteDeviceManager", "RemoteDevVideoIn", "RemoteMagicBox", "RemoteDevicePreview",
            "IntervideoManager", "IntervideoClient", "DeviceConfigManager", "EncodeProfile",
            "devVideoEncode", "TransCode"]

# getConfigCaps param shapes seen across Dahua firmwares. The method exists; only the shape is wrong.
CAPS_SHAPES = [
    None,
    {"channel": 0},
    {"Channel": 0},
    {"channel": 0, "type": "MainFormat"},
    {"channel": 0, "index": 0},
    {"channels": [0]},
    {"name": "Encode"},
    {"channel": 0, "stream": 0},
]


def show(label, res, dump_key=None, dump=None):
    if res.get("error"):
        print("  %-46s ERR %s" % (label, json.dumps(res["error"])[:110]))
        return False
    p = res.get("params")
    if dump is not None and dump_key:
        dump[dump_key] = p
    ms = (p or {}).get("method") if isinstance(p, dict) else None
    if ms:
        print("  %-46s OK  %d methods" % (label, len(ms)))
        print("      " + ", ".join(ms)[:700])
    else:
        print("  %-46s OK  %s" % (label, json.dumps(p)[:220] if p else "(empty payload)"))
    return True


def main():
    r = RPC2()
    r.login()
    dump = {}

    print("=== service method listings ===")
    for s in SERVICES:
        try:
            res = r.call("%s.listMethod" % s)
        except Exception as e:
            print("  %-46s EXC %s" % (s, str(e)[:70]))
            continue
        show(s + ".listMethod", res, s, dump)

    print("\n=== encode.getConfigCaps -- hunting the param shape ===")
    for shape in CAPS_SHAPES:
        try:
            res = r.call("encode.getConfigCaps", shape)
        except Exception as e:
            print("  %-46s EXC %s" % (json.dumps(shape), str(e)[:70]))
            continue
        label = "getConfigCaps %s" % json.dumps(shape)
        if show(label, res, "getConfigCaps_%s" % json.dumps(shape), dump):
            break            # first shape that answers is the one we want

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(dump, f, indent=1)
    print("\nwrote %s" % OUT)
    try:
        r.logout()
    except Exception:
        pass


if __name__ == "__main__":
    main()
