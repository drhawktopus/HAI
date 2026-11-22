"""Ask the NVR, via RPC2, what encoder modes it considers VALID — and list every service it has.

`encode.getConfigCaps` returned HTTP 400 over CGI which is what closed the 2K30
question. But `encode.listMethod` over RPC2 lists getConfigCaps as a real method. **A verb absent
from CGI is not absent from the device** — the CGI write path on this unit is already known to be a
lying success, so its refusals carry little information about RPC2.

If getConfigCaps enumerates the resolution list, we learn directly whether 2560x1440@30 is
refused by policy (a list we might change) or by capability (it is not there at all).

READ ONLY.
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nvr_rpc2 import RPC2

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpc2_caps_dump.json")


def main():
    r = RPC2()
    r.login()
    dump = {}

    # 1. every service the box admits to
    res = r.call("system.listService")
    svcs = (res.get("params") or {}).get("service") or []
    dump["services"] = svcs
    print("SERVICES: %d" % len(svcs))
    # the ones that could plausibly reach a remote camera or change encoder policy
    interesting = [s for s in svcs if any(k in s.lower() for k in
                   ("remote", "logic", "encode", "video", "proxy", "pass", "trans", "device",
                    "config", "net", "ptz"))]
    print("  candidates:", ", ".join(interesting))
    print()

    # 2. the encoder capability enumeration -- the actual question
    for ch in (0, 1):
        for method in ("encode.getConfigCaps", "encode.getCaps", "encode.getEncodeType"):
            try:
                res = r.call(method, {"channel": ch})
            except Exception as e:
                print("ch%d %-26s EXC %s" % (ch, method, e))
                continue
            if res.get("error"):
                print("ch%d %-26s ERR %s" % (ch, method, json.dumps(res["error"])[:120]))
            else:
                dump["%s_ch%d" % (method, ch)] = res.get("params")
                print("ch%d %-26s OK  (captured)" % (ch, method))

    # 3. what methods does the remote-device manager expose?
    for method in ("LogicDeviceManager.listMethod", "configManager.listMethod",
                   "devVideoInput.listMethod", "system.getCaps"):
        try:
            res = r.call(method)
        except Exception as e:
            print("%-34s EXC %s" % (method, e))
            continue
        if res.get("error"):
            print("%-34s ERR %s" % (method, json.dumps(res["error"])[:110]))
        else:
            dump[method] = res.get("params")
            ms = (res.get("params") or {}).get("method") or []
            print("%-34s OK  %d methods" % (method, len(ms)))
            if ms:
                print("     ", ", ".join(ms)[:600])

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(dump, f, indent=1)
    print("\nwrote %s" % OUT)
    try:
        r.logout()
    except Exception:
        pass


if __name__ == "__main__":
    main()
