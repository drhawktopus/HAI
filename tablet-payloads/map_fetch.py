"""map payload: fetch — item is a URL; row is status, title, and size. Stdlib only (tablets have
bare python). Timeouts are short and per-item: one dead site must not eat the batch.

Contract: `python map_fetch.py items.txt out.json`; every row carries "item" (the URL verbatim) —
see map_echo.py, the template. Errors are ROWS, not crashes: a fetch that failed is a measured
result ("this URL did not answer"), never a missing item, or coverage could not tell a dead link
from a skipped one.
"""
import json
import re
import sys
import urllib.request


def fetch_one(url):
    row = {"item": url, "status": None, "title": "", "bytes": 0, "error": ""}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tabfleet-map/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read(500_000)
            row["status"] = getattr(r, "status", None) or r.getcode()
            row["bytes"] = len(body)
            m = re.search(rb"<title[^>]*>(.*?)</title>", body, re.I | re.S)
            if m:
                row["title"] = m.group(1).decode("utf-8", "replace").strip()[:300]
    except Exception as e:
        row["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    return row


def main(items_path, out_path):
    with open(items_path, encoding="utf-8") as f:
        items = [l.strip() for l in f if l.strip()]
    rows = [fetch_one(u) for u in items]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
