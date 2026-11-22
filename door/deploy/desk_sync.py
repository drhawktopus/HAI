#!/usr/bin/env python3
"""Carry GPT's desk into the lab, from outside Uhura's container, so the internet-facing half never writes to the library.

- Everything on the desk is copied to ChatGPT Home.
- Today's and past journals go to Journal/YYYY-MM-DD-chatgpt.md.
- Career notes go to Reference/Career/from-chatgpt, handoffs to Notes/GPT-Handoffs. Both unindexed, so a cloud
  model's note cannot become something Doc treats as policy. New files only; an existing file is never replaced.

Exit is non-zero if anything failed, so the failure shows in the unit's status.
"""
import json
import os
import shutil
import sys
import time

DESK = os.environ.get("UHURA_DESK", "/opt/uhura/desk")
HOME = os.environ.get("CHATGPT_HOME", "/srv/chatgpt-home")
LIBRARY = os.environ.get("UHURA_LIBRARY", "/srv/library")
STATUS = os.environ.get("UHURA_DESK_STATUS", "/opt/uhura/desk-status.json")
MARK = "Written by ChatGPT"


def copy_if_changed(src, dst):
    """Atomic copy when the content differs. Returns True if it wrote."""
    with open(src, "rb") as f:
        data = f.read()
    if os.path.exists(dst):
        with open(dst, "rb") as f:
            if f.read() == data:
                return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp-desk"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dst)
    return True


def main():
    for required in (LIBRARY + "/Journal", os.path.dirname(HOME)):
        if not os.path.isdir(required):
            raise SystemExit(f"not mounted: {required}")
    counts = {"home": 0, "journal": 0, "career": 0, "handoffs": 0, "refused": 0}
    for dirpath, _dirs, names in os.walk(DESK):
        for name in names:
            if name.startswith("."):
                continue
            src = os.path.join(dirpath, name)
            if os.path.islink(src):
                continue
            rel = os.path.relpath(src, DESK)
            counts["home"] += copy_if_changed(src, os.path.join(HOME, rel))
    journal_dir = os.path.join(DESK, "journal")
    if os.path.isdir(journal_dir):
        for name in os.listdir(journal_dir):
            if not name.endswith("-chatgpt.md"):
                continue
            dst = os.path.join(LIBRARY, "Journal", name)
            if os.path.exists(dst) and MARK not in open(dst, encoding="utf-8", errors="replace").read(400):
                counts["refused"] += 1  # a file GPT did not write is never replaced
                continue
            counts["journal"] += copy_if_changed(os.path.join(journal_dir, name), dst)
    for folder, target in (("career", "Reference/Career/from-chatgpt"), ("handoffs", "Notes/GPT-Handoffs")):
        source = os.path.join(DESK, folder)
        if not os.path.isdir(source):
            continue
        for name in os.listdir(source):
            dst = os.path.join(LIBRARY, target, name)
            if name.endswith(".md") and not os.path.exists(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(os.path.join(source, name), dst + ".tmp-desk")
                os.replace(dst + ".tmp-desk", dst)
                counts[folder] += 1
    summary = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **counts}
    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump(summary, f)
    print(json.dumps(summary))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("desk_sync FAILED:", type(exc).__name__, str(exc)[:300], flush=True)
        sys.exit(1)
