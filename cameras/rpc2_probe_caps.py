"""Probe the NVR's RPC2 surface for a route to the CAMERA's own encoder — READ ONLY.

WHY: 2K30 is a stated goal and the recorder refuses it through the CGI API (`encode.cgi
getConfigCaps` 400, VirtualHost 400, `configManager.cgi&channel=N` 400). RPC2 is a different
surface, and the CGI WRITE path returns "OK" while changing nothing (a config diff shows it), so
CGI's refusals say little about what RPC2 can do.

The interesting family on a Dahua NVR is LogicDeviceManager, the service through which the unit
administers its remote IPC cameras. If the NVR can push encoder config down to a camera, 2K30 can be set on the
CAMERA even though the recorder will not offer it in its own table.

THREE OUTCOMES, KEPT DISTINCT (an empty answer is not an absent feature):
    OK+data -> method exists and answered
    OK+empty -> method exists, nothing configured
    ERROR -> read the code/message; "no such method" is different from "bad params"
"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from nvr_rpc2 import RPC2

# Read-only candidates. Nothing here mutates; the set_config path is deliberately untouched.
CANDIDATES = [
    # what does the box say it IS and can do
    ("magicBox.getProductDefinition", None),
    ("magicBox.getSoftwareVersion", None),
    ("magicBox.getCaps", None),
    ("system.listMethod", None),               # the jackpot if it answers
    ("system.listService", None),
    # remote (IPC) device administration -- the NVR's own camera manager
    ("LogicDeviceManager.getCameraAll", None),
    ("LogicDeviceManager.secGetCameraAll", None),
    ("LogicDeviceManager.getCameraState", {"uniqueChannels": [-1]}),
    ("LogicDeviceManager.getCameraStatus", None),
    # encoder capability enumeration
    ("encode.getCaps", {"channel": 0}),
    ("Encode.getCaps", {"channel": 0}),
    ("devVideoInput.getCaps", {"channel": 0}),
    ("encode.listMethod", None),
    # config tables that might carry the remote encoder
    ("configManager.getConfig", {"name": "Encode"}),
    ("configManager.getConfig", {"name": "RemoteDevice"}),
    ("configManager.getConfig", {"name": "VideoInOptions"}),
    ("configManager.getDefault", {"name": "Encode"}),
    ("configManager.getCaps", {"name": "Encode"}),
]


def brief(v, n=340):
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= n else s[:n] + " …"


def main():
    r = RPC2()
    r.login()
    print("logged in\n")
    for method, params in CANDIDATES:
        label = "%s %s" % (method, json.dumps(params) if params else "")
        try:
            res = r.call(method, params)
        except Exception as e:
            print("%-52s EXC   %s: %s" % (label, type(e).__name__, str(e)[:90]))
            continue
        ok = res.get("result")
        if res.get("error"):
            print("%-52s ERR   %s" % (label, brief(res["error"], 140)))
        elif ok and len(res) > 2:
            payload = {k: v for k, v in res.items() if k not in ("result", "id", "session")}
            print("%-52s OK    %s" % (label, brief(payload)))
        else:
            # OK with no payload is NOT the same as an error -- say so explicitly
            print("%-52s OK    (empty payload -- method exists, nothing returned)" % label)
    try:
        r.logout()
    except Exception:
        pass


if __name__ == "__main__":
    main()
