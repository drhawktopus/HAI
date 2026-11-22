"""map payload: echo — the fleet's LIVE DECOY and the template every map payload copies.

Contract (kind: "map"): invoked as `python map_echo.py items.txt out.json`. Read one item string
per line, emit a JSON LIST of row dicts, and EVERY row MUST carry "item": <the input line verbatim>
— coverage validation counts distinct `item` values, so a payload that forgets the echo fails
coverage instead of silently passing.
"""
import json
import platform
import sys
import time


def main(items_path, out_path):
    with open(items_path, encoding="utf-8") as f:
        items = [l.strip() for l in f if l.strip()]
    rows = []
    for it in items:
        rows.append({
            "item": it,
            "result": "echo:%s" % it,
            "node": platform.node(),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
