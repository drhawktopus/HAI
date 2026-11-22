"""Snapshot the Lorex N844A8's full config, and diff it against a previous snapshot.

Purpose: when a setting is changed through the NVR UI or the Lorex app, this
names the EXACT config keys that moved. That is how you learn a schema you were
never going to guess -- let the vendor's own UI write it, then read what it wrote.

Usage (run from anywhere; paths are absolute):
    python nvr_config_diff.py                 # take a snapshot, diff vs newest existing
    python nvr_config_diff.py --snap-only     # just capture, no diff
    python nvr_config_diff.py --against FILE  # diff vs a specific snapshot

Read-only against the NVR: a single getConfig&name=All. Never writes to the device.
Credentials come from the gated store, never hardcoded.
"""
import argparse
import glob
import os
import sys
import time
import urllib.request

from nvr_settings import HOST, login
BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config-backups")


def opener():
    user, pw = login()
    mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    mgr.add_password(None, "http://%s/" % HOST, user, pw)
    return urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(mgr))


def snapshot():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    data = opener().open(
        "http://%s/cgi-bin/configManager.cgi?action=getConfig&name=All" % HOST,
        timeout=30).read()
    path = os.path.join(BACKUP_DIR, "all-%s.txt" % time.strftime("%Y%m%d-%H%M%S"))
    with open(path, "wb") as f:
        f.write(data)
    return path, data.decode("utf-8", "replace")


def load(path):
    out = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if "=" in line:
                k, _, v = line.partition("=")
                out[k] = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap-only", action="store_true")
    ap.add_argument("--against")
    a = ap.parse_args()

    prior = sorted(glob.glob(os.path.join(BACKUP_DIR, "all-*.txt")))
    baseline = a.against or (prior[-1] if prior else None)

    path, _ = snapshot()
    print("snapshot -> %s" % path)
    if a.snap_only:
        return
    if not baseline:
        print("no earlier snapshot to diff against; this one is the baseline.")
        return

    print("baseline -> %s" % baseline)
    old, new = load(baseline), load(path)
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(old) & set(new) if old[k] != new[k])

    if not (added or removed or changed):
        print("\nNO DIFFERENCES. The config is byte-identical on the keys compared.")
        print("If a setting was just changed in the UI/app and nothing shows here, the "
              "change did NOT land in the NVR's config -- it is app-side or cloud-side.")
        return

    print("\n%d added, %d removed, %d changed" % (len(added), len(removed), len(changed)))
    for title, keys in (("ADDED", added), ("REMOVED", removed), ("CHANGED", changed)):
        if not keys:
            continue
        print("\n--- %s" % title)
        for k in keys[:400]:
            short = k.replace("table.All.", "")
            if title == "CHANGED":
                print("  %s : %s -> %s" % (short, old[k], new[k]))
            elif title == "ADDED":
                print("  %s = %s" % (short, new[k]))
            else:
                print("  %s (was %s)" % (short, old[k]))
        if len(keys) > 400:
            print("  ... and %d more (truncated -- raise the cap if this matters)"
                  % (len(keys) - 400))


if __name__ == "__main__":
    sys.exit(main())
