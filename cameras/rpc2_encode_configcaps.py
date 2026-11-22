"""Enumerate the VALID encoder modes per channel — the call that settles 2K30. READ ONLY.

The exact shape, lifted from the NVR's own web UI (`/app/jsCore/RPC.js`):

    send("encode.getConfigCaps", {channel: N, config: C, stream: "All"})

I had been calling it with {channel: N} alone, which returns "Request invalid param!" -- and the
pass called the CGI form, which 400s. Neither says anything about what the device
actually supports. This does.

What we are looking for: whether 2560x1440 (and 2592x1944, the 5MP the E841CA spec sheet lists)
appear in the capability list. If they do, the refusal is POLICY and there is a lever. If they do
not appear for the main stream at all, then the recorder genuinely cannot ask for it and the answer
is the remote-device write path (`configManager.setConfig` with {target: channel}) instead.
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nvr_rpc2 import RPC2

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "encode_configcaps.json")


def main():
    r = RPC2()
    r.login()
    dump = {}

    # `config` is passed straight through by the UI; try the plausible values rather than one guess.
    for cfg in (None, "Encode", "MainFormat", 0):
        for ch in (0, 1):
            params = {"channel": ch, "config": cfg, "stream": "All"}
            res = r.call("encode.getConfigCaps", params)
            label = "ch%d config=%s" % (ch, cfg)
            if res.get("error"):
                print("  %-28s ERR %s" % (label, json.dumps(res["error"])[:90]))
                continue
            caps = (res.get("params") or {}).get("caps")
            dump[label] = caps
            print("  %-28s OK  captured" % label)
            if not caps:
                continue
            blob = json.dumps(caps)
            # Report the resolution vocabulary directly -- that is the whole question.
            for token in ("2560", "1440", "2592", "1944", "3840", "2160", "1920", "5M", "4M", "3M"):
                if token in blob:
                    print("       contains %s" % token)
        if any(k.startswith("ch0") and dump.get(k) for k in dump):
            break     # first config value that answers is the right one

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(dump, f, indent=1)
    print("\nwrote %s" % OUT)
    try:
        r.logout()
    except Exception:
        pass


if __name__ == "__main__":
    main()
