"""#38 Lab update system, step 1: what every lab box has installed and which updates are waiting. READ-ONLY.

    python update_inventory.py                  # every box below, in parallel
    python update_inventory.py mothership rigby # just these

Windows boxes run update_inventory.ps1 (Windows Update's own pending search + history, non-Microsoft drivers, installed
programs from the registry, vendor updater services/tasks, Defender, ollama) plus `winget upgrade` read-only. Linux
boxes run update_inventory.sh (apt's cached lists, reboot-required, the auto-upgrade units, NVIDIA driver, snaps).
Each box's full answer is written to update-inventory\\<box>-<stamp>.json (one name, one generation); the summary is
printed. A box that cannot be asked is reported as COULD NOT ASK, never as up to date.
"""
import base64
import datetime
import json
import os
import subprocess
import sys
import threading

import lab_settings as _S  # noqa: E402

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "update-inventory")
WINDOWS = tuple(_S.fleet_hosts())   # the Windows factories: the fleet table (FLEET_HOSTS)
# the Linux boxes: UPDATE_INVENTORY_LINUX (comma list), else the head and the tablet head
LINUX = tuple(x.strip() for x in (_S.setting("UPDATE_INVENTORY_LINUX", "") or ",".join(
    n for n in (_S.setting("HEAD_NAME", ""), _S.setting("TABLET_HEAD_NAME", "")) if n)).split(",") if x.strip())

REMOTE = r'''
import base64, os, subprocess, json, tempfile
ps = base64.b64decode("__PS__").decode("utf-8")
p = os.path.join(tempfile.gettempdir(), "update_inventory_%d.ps1" % os.getpid())
open(p, "w", encoding="utf-8-sig").write(ps)
try:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", p],
                       capture_output=True, timeout=900)
    out = r.stdout.decode("utf-8", "replace").strip().splitlines()
    inv = json.loads(out[-1]) if out else {"error": "no output: " + r.stderr.decode("utf-8", "replace")[-300:]}
finally:
    os.remove(p)
try:
    w = subprocess.run(["winget", "upgrade", "--disable-interactivity"], capture_output=True, timeout=180)
    # the WHOLE text: a tail cap cut Mothership's header off (38 rows > 6,000 chars) and the parser then read 0 rows
    inv["winget_upgrade"] = {"rc": w.returncode, "text": w.stdout.decode("utf-8", "replace")}
except Exception as e:
    inv["winget_upgrade"] = {"rc": None, "text": "%s: %s" % (type(e).__name__, e)}
print("@@" + json.dumps(inv))
'''


def ask_windows(box):
    ps = open(os.path.join(HERE, "update_inventory.ps1"), "rb").read()
    src = REMOTE.replace("__PS__", base64.b64encode(ps).decode("ascii"))
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", box, "python -"], input=src.encode("utf-8"),
                       capture_output=True, timeout=1200)
    hit = [x for x in r.stdout.decode("utf-8", "replace").splitlines() if x.startswith("@@")]
    if not hit:
        return {"could_not_ask": (r.stdout + r.stderr).decode("utf-8", "replace")[-400:]}
    return json.loads(hit[-1][2:])


def ask_linux(box):
    # the engine git trees the script inventories on this box (TABLET_HEAD_ENGINE_GIT_TREES, comma-separated)
    trees = " ".join(t.strip() for t in _S.setting("TABLET_HEAD_ENGINE_GIT_TREES", "").split(",") if t.strip())
    sh = ("LAB_ENGINE_TREES='%s'\n" % trees).encode("utf-8") + open(os.path.join(HERE, "update_inventory.sh"), "rb").read()
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", box, "bash -s"], input=sh,
                       capture_output=True, timeout=300)
    out = r.stdout.decode("utf-8", "replace")
    if "done\tyes" not in out:
        return {"could_not_ask": (out + r.stderr.decode("utf-8", "replace"))[-400:]}
    lists = {"upgradable": "upgradable", "reboot_pkg": "reboot_pkgs", "unit": "units", "snap_refresh": "snap_refresh",
             "image": "images", "engine_git": "engine_git", "engine_mount": "engine_mounts"}
    inv = {v: [] for v in lists.values()}
    for line in out.splitlines():
        k, _sep, v = line.partition("\t")
        if k in lists:
            inv[lists[k]].append(v)
        elif k:
            inv[k] = v
    return inv


def summary(box, inv):
    if "could_not_ask" in inv:
        return ["%s: COULD NOT ASK - %s" % (box, inv["could_not_ask"].strip().splitlines()[-1:] or "")]
    out = []
    if box in WINDOWS:
        os_ = inv.get("os") or {}
        pend = inv.get("wu_pending") or []
        out.append("%s: %s %s.%s (%s) | boot %s | reboot pending: %s" % (
            box, os_.get("caption"), os_.get("build"), os_.get("ubr"), os_.get("display"), os_.get("last_boot"),
            inv.get("reboot_pending")))
        out.append("  Windows Update: %s" % ("%d pending" % len(pend) if inv.get("wu_ok") else
                                            "COULD NOT ASK (%s)" % inv.get("wu_error")))
        for u in pend:
            out.append("    - %s [%s]%s" % (u.get("title"), u.get("categories"), " (downloaded)" if u.get("downloaded") else ""))
        out.append("  policy: %s" % json.dumps(inv.get("update_policy")))
        gpus = [d for d in inv.get("drivers") or [] if d.get("class") == "DISPLAY"]
        for d in gpus:
            out.append("  display driver: %s %s (%s, %s)" % (d.get("device"), d.get("version"), d.get("provider"), d.get("date")))
        out.append("  non-Microsoft drivers: %d | programs: %d | Defender platform %s, signatures %s (%s)" % (
            len(inv.get("drivers") or []), len(inv.get("programs") or []), (inv.get("defender") or {}).get("platform"),
            (inv.get("defender") or {}).get("signatures"), (inv.get("defender") or {}).get("signatures_updated")))
        out.append("  ollama: %s | api %s" % ([o.get("path") for o in inv.get("ollama") or []], inv.get("ollama_api_version")))
        for s in inv.get("vendor_services") or []:
            out.append("  service: %-28s %-8s %-8s %s" % (s.get("name"), s.get("state"), s.get("start"), s.get("display")))
        for t in inv.get("update_tasks") or []:
            out.append("  task: %s%s  %s  next %s  -> %s" % (t.get("path"), t.get("name"), t.get("state"), t.get("next"),
                                                          (t.get("action") or "")[:90]))
        w = inv.get("winget_upgrade") or {}
        out.append("  winget upgrade: rc %s, %d lines" % (w.get("rc"), len((w.get("text") or "").splitlines())))
        for e in inv.get("engines") or []:
            g = e.get("git") or {}
            pk = e.get("packages") or {}
            out.append("  engine %s: git %s %s | venv %s | %s%s" % (
                e.get("root"), (g.get("commit") or "-")[:10], g.get("ref") or "", e.get("venv") or "-",
                ", ".join("%s %s" % kv for kv in pk.items()) or "-",
                (" | %d custom nodes" % len(e["custom_nodes"]) if e.get("custom_nodes") else "") +
                (" | UPDATER: %s" % ", ".join(e["updaters"]) if e.get("updaters") else "")))
        if inv.get("foundry_env"):
            out.append("  foundry env: %s" % json.dumps(inv["foundry_env"]))
        out.append("  ollama trees: %s | llama-swap: %s | Flash-Next trees: %s" % (
            inv.get("ollama_trees"), inv.get("llama_swap"), inv.get("flashnext_trees")))
    else:
        out.append("%s: %s | kernel %s | boot %s | apt lists %s h old | %d upgradable | reboot required: %s" % (
            box, inv.get("os"), inv.get("kernel"), inv.get("boot"),
            round(int(inv["apt_lists_age_s"]) / 3600, 1) if inv.get("apt_lists_age_s") else "?",
            len(inv["upgradable"]), inv.get("reboot_required", "no")))
        for u in inv["units"]:
            out.append("  unit: " + u)
        for k in ("nvidia_driver", "docker"):
            if inv.get(k):
                out.append("  %s: %s" % (k, inv[k]))
        if inv["snap_refresh"]:
            out.append("  snap refresh: %d" % len(inv["snap_refresh"]))
        for x in inv["engine_git"] + inv["engine_mounts"]:
            out.append("  engine: " + x)
        local = [i for i in inv["images"] if i.split(":")[0].startswith(("arrstack-", "tolkien", "coding-runner", "tablet"))]
        out.append("  docker images: %d (%d built here: %s)" % (len(inv["images"]), len(local), "; ".join(local)[:400]))
    return out


def collect(boxes, stamp):
    """Ask every box in parallel, write each answer to OUT_DIR\\<box>-<stamp>.json -> {box: inventory}."""
    os.makedirs(OUT_DIR, exist_ok=True)
    got = {}

    def one(box):
        try:
            got[box] = ask_windows(box) if box in WINDOWS else ask_linux(box)
        except Exception as e:                                   # noqa: BLE001
            got[box] = {"could_not_ask": "%s: %s" % (type(e).__name__, e)}

    threads = [threading.Thread(target=one, args=(b,)) for b in boxes]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(got) == len(boxes), "asked %d boxes, %d answers" % (len(boxes), len(got))
    for box in boxes:
        with open(os.path.join(OUT_DIR, "%s-%s.json" % (box, stamp)), "w", encoding="utf-8") as f:
            json.dump(got[box], f, indent=1)
    return got


def main(argv):
    boxes = argv or list(WINDOWS + LINUX)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    got = collect(boxes, stamp)
    for box in boxes:
        print("\n".join(summary(box, got[box])))
        print("  -> %s" % os.path.join(OUT_DIR, "%s-%s.json" % (box, stamp)))
    asked = [b for b in boxes if "could_not_ask" not in got[b]]
    print("\nasked %d of %d boxes; could not ask: %s" % (len(asked), len(boxes),
                                                       [b for b in boxes if b not in asked] or "none"))


if __name__ == "__main__":
    main(sys.argv[1:])
