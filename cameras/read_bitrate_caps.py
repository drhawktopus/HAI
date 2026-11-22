"""Read the LIVE bitrate ceilings and current values for every enabled encoder slot. Read-only.

WHY LIVE: `resolution_types.json` is a capture taken when ch1 was 4K@15 and ch2 was
a different format again -- its per-slot caps carry those geometries (it still shows FPSMax 15 for
ch1). Caps on this device are reported per current configuration, so a stored capture is a claim
about a configuration that no longer exists. Ask the device.

 The DECLARED cap is not the real one in at least one known case: ExtraFormat[0] declares a 1280
ceiling and dining has been running 1536 for days. So treat declared as a hint, and let a measured
stream settle the argument (`measure_bitrate.py`).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from nvr_rpc2 import RPC2

NAMES = {0: "dining (ch1)", 1: "basement (ch2)"}


def main():
    r = RPC2()
    ok, msg = r.login()
    if not ok:
        print("login failed: %s" % msg)
        return 2

    cfg = r.call("configManager.getConfig", {"name": "Encode"})
    table = (cfg.get("params") or {}).get("table")
    if not table:
        print("could not read Encode table")
        return 2

    for idx in (0, 1):
        print("=== %s ===" % NAMES[idx])
        caps = r.call("encode.getConfigCaps",
                      {"channel": idx, "config": table, "stream": "All"})
        cp = caps.get("params") or {}
        caprows = cp.get("caps") or cp.get("table") or []
        if isinstance(caprows, dict):
            caprows = [caprows]
        caprow = caprows[idx] if len(caprows) > idx else (caprows[0] if caprows else {})

        for kind in ("MainFormat", "ExtraFormat"):
            cur_slots = table[idx].get(kind) or []
            cap_slots = (caprow or {}).get(kind) or []
            for i, slot in enumerate(cur_slots):
                v = (slot or {}).get("Video") or {}
                if not v or not (slot or {}).get("VideoEnable"):
                    continue
                cv = ((cap_slots[i] if len(cap_slots) > i else {}) or {}).get("Video") or {}
                opts = cv.get("BitRateOptions")
                ceiling = opts[1] if isinstance(opts, list) and len(opts) > 1 else "?"
                cur = v.get("BitRate")
                flag = "" if cur == ceiling else "   <-- BELOW CEILING"
                print("  %-12s[%d]  %sx%s @%s %-6s  bitrate %s / cap %s%s"
                      % (kind, i, v.get("Width"), v.get("Height"), v.get("FPS"),
                         v.get("Compression"), cur, ceiling, flag))
        print("  (row BitRateRange: %s)" % json.dumps(caprow.get("BitRateRange")
                                                      if isinstance(caprow, dict) else None))
        print()

    try:
        r.logout()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
