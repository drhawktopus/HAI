"""Get the VALID resolution list per channel — the call that settles 2K30. READ ONLY.

The full chain, all of it lifted from the NVR's own web UI rather than guessed:

  /nvr/data/MenuConfig.js        -> the encode page is `js.camera.encode.videoStream.VideoStream`
  .../videoStream/VideoStream.js -> `rpc.Encode.getConfigChnlCaps(channel, c.Encode)`
                                    where c.Encode is the WHOLE Encode config TABLE
  /app/jsCore/RPC.js             -> getConfigChnlCaps = send("encode.getConfigCaps",
                                        {channel, config, stream: "All"})
  and the UI's own test for 2K:      Ext.Array.contains(caps.MainFormat[n].Video.ResolutionTypes,
                                                        "2560x1440")

So `config` is not a name or a mode -- it is the current Encode table, handed back to the device so
it can answer "given this, what is valid". Eight guessed values failed before reading the caller.

If "2560x1440" appears in ResolutionTypes for a MAIN format, the recorder CAN be asked for 2K and
the earlier refusal was a policy/parameter problem. If it does not appear anywhere, the recorder
genuinely cannot, and the remaining route is the remote-device write path.
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nvr_rpc2 import RPC2

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resolution_types.json")
WANT = ("2560x1440", "2592x1944", "2688x1520", "3840x2160", "1920x1080")


def main():
    r = RPC2()
    r.login()

    cfg = r.call("configManager.getConfig", {"name": "Encode"})
    table = (cfg.get("params") or {}).get("table")
    if not table:
        print("could not read the Encode table")
        return
    print("Encode table read: %d channels\n" % len(table))

    dump = {}
    for ch in (0, 1):
        res = r.call("encode.getConfigCaps",
                     {"channel": ch, "config": table, "stream": "All"})
        if res.get("error"):
            print("ch%d ERR %s" % (ch, json.dumps(res["error"])[:110]))
            continue
        caps = (res.get("params") or {}).get("caps")
        dump["ch%d" % ch] = caps
        if not caps:
            print("ch%d OK but empty caps" % ch)
            continue
        entry = caps[0] if isinstance(caps, list) else caps
        print("=== channel %d ===" % ch)
        for kind in ("MainFormat", "ExtraFormat"):
            for i, fmt in enumerate((entry or {}).get(kind) or []):
                v = (fmt or {}).get("Video") or {}
                rts = v.get("ResolutionTypes")
                if rts is None:
                    continue
                hits = [w for w in WANT if w in rts]
                print("  %s[%d]  %d modes   %s" % (kind, i, len(rts), ", ".join(hits)))
                if i == 0 and kind == "MainFormat":
                    print("      full list: %s" % ", ".join(rts))
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
