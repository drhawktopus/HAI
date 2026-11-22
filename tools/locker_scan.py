"""Fail-closed check: no credential-locker value (plain or URL-encoded) in any file under a folder, tests included.

    python3 locker_scan.py <locker folder> <folder to scan> <folder holding publish_hai.py>

The locker's values come from the publish script's own reader (one derivation, never a copy). Prints file names and a
count, never a value. Exit 0 clean, 1 a value found, 2 the locker could not be read (an unread locker is not an empty one).
"""
import os
import sys
from pathlib import Path


def main(argv):
    locker, scan, publisher = argv[1], argv[2], argv[3]
    sys.path.insert(0, publisher)
    import publish_hai
    vals = publish_hai.locker_values(Path(locker))
    if not vals:
        print("locker scan: the locker at %s could not be read" % locker)
        return 2
    hits, scanned = [], 0
    for root, dirs, files in os.walk(scan):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            p = os.path.join(root, f)
            try:
                text = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            scanned += 1
            if any(v in text for v in vals):
                hits.append(os.path.relpath(p, scan))
    print("locker scan: %d file(s) read, %d carry a locker value%s" % (
        scanned, len(hits), (": " + ", ".join(hits[:10])) if hits else ""))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
