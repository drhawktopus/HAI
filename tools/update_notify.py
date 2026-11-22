"""    nightly diff of lab box updates (READ-ONLY on boxes; update_inventory.py performs the read).

Categories (priority order):
  INSTALLED: changes present since last read
  WAITING: newly offered updates (excludes Defender signatures to reduce noise)
  UPSTREAM: new releases of supported engines (ollama, ComfyUI, llama-swap, ai-toolkit)

Unreachable boxes are marked as such and diffed against last known state on next run; never treated as clean.

Outputs:
  <DOCS_STATE_DIR>\\lab-updates\\report.txt: first line `asked <iso>`, last line `END`
  <DOCS_STATE_DIR>\\lab-updates\\report-<stamp>.json: full data"""
import datetime
import glob
import json
import os
import re
import sys
import urllib.request
import lab_settings as S  # noqa: E402  the lab's settings reader

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_inventory as inv  # noqa: E402

STATE = os.path.join(S.setting("DOCS_STATE_DIR", ""), "lab-updates")
FIELD_MAX = 1000                # Discord's embed field value limit is 1024
# repo -> what the lab runs of it, the words the notification shows: the UPSTREAM_WATCH and UPSTREAM_COMMITS_WATCH
# settings (JSON tables), since what runs where is this lab's own fact
UPSTREAM = S.setting_json("UPSTREAM_WATCH", {}) or {}
UPSTREAM_COMMITS = S.setting_json("UPSTREAM_COMMITS_WATCH", {}) or {}
NOISE = re.compile(r"Security Intelligence Update for Microsoft Defender", re.I)


def last_good(box, before_stamp):
    """The newest earlier reading of `box` that is a real answer (not COULD NOT ASK), or None."""
    for p in sorted(glob.glob(os.path.join(inv.OUT_DIR, "%s-*.json" % box)), reverse=True):
        if os.path.basename(p)[len(box) + 1:-5] >= before_stamp:
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if "could_not_ask" not in d:
            return d
    return None


def winget_table(text):
    """`winget upgrade` text -> {id: (installed, available)}, sliced by the header's own column positions."""
    lines = (text or "").replace("\r", "\n").splitlines()
    # winget's progress spinner is printed on the same line before the header ("  - \ | Name  Id ..."), so the header
    # is found wherever it sits and the columns are measured from its "Name"; the data rows start at column 0
    hdr = next((i for i, ln in enumerate(lines) if re.search(r"Name\s+Id\s+Version\s+Available", ln)), None)
    if hdr is None:
        return {}
    h = lines[hdr][lines[hdr].index("Name"):]
    cols = [h.index(c) for c in ("Id", "Version", "Available", "Source")]
    out = {}
    for ln in lines[hdr + 1:]:
        if (not ln.strip() or set(ln.strip()) <= {"-"} or re.match(r"^\d+ upgrades? available", ln)
                or len(ln) < cols[3]):
            continue
        pid = ln[cols[0]:cols[1]].strip()
        if pid:
            out[pid] = (ln[cols[1]:cols[2]].strip(), ln[cols[2]:cols[3]].strip())
    return out


def _by(rows, key, val):
    return {key(r): val(r) for r in rows or []}


def diff_windows(prev, cur):
    installed, waiting = [], []
    po, co = prev.get("os") or {}, cur.get("os") or {}
    if (po.get("build"), po.get("ubr")) != (co.get("build"), co.get("ubr")):
        installed.append("Windows %s.%s -> %s.%s" % (po.get("build"), po.get("ubr"), co.get("build"), co.get("ubr")))
    k = lambda d: "%s | %s" % (d.get("device"), d.get("provider"))  # noqa: E731
    pd, cd = _by(prev.get("drivers"), k, lambda d: d.get("version")), _by(cur.get("drivers"), k, lambda d: d.get("version"))
    for dev in sorted(cd):
        if dev in pd and pd[dev] != cd[dev]:
            installed.append("driver %s: %s -> %s" % (dev.split(" | ")[0], pd[dev], cd[dev]))
        elif dev not in pd:
            installed.append("new driver %s %s" % (dev.split(" | ")[0], cd[dev]))
    pp = _by(prev.get("programs"), lambda p: p.get("name"), lambda p: p.get("version"))
    cp = _by(cur.get("programs"), lambda p: p.get("name"), lambda p: p.get("version"))
    for name in sorted(cp):
        if name in pp and pp[name] != cp[name]:
            installed.append("%s %s -> %s" % (name, pp[name], cp[name]))
        elif name not in pp:
            installed.append("installed %s %s" % (name, cp[name]))
    for name in sorted(set(pp) - set(cp)):
        installed.append("removed %s" % name)
    pe = _by(prev.get("engines"), lambda e: e.get("root"), lambda e: ((e.get("git") or {}).get("commit"), e.get("packages")))
    for e in cur.get("engines") or []:
        old = pe.get(e.get("root"))
        new = ((e.get("git") or {}).get("commit"), e.get("packages"))
        if old is not None and old != new:
            what = []
            if old[0] != new[0]:
                what.append("git %s -> %s" % ((old[0] or "-")[:10], (new[0] or "-")[:10]))
            for pk in sorted(set(old[1] or {}) | set(new[1] or {})):
                if (old[1] or {}).get(pk) != (new[1] or {}).get(pk):
                    what.append("%s %s -> %s" % (pk, (old[1] or {}).get(pk), (new[1] or {}).get(pk)))
            installed.append("ENGINE %s: %s" % (e.get("root"), "; ".join(what)))
    for t in sorted(set(cur.get("ollama_trees") or []) - set(prev.get("ollama_trees") or [])):
        installed.append("new ollama tree %s" % t)
    if cur.get("reboot_pending") and not prev.get("reboot_pending"):
        installed.append("a reboot is now pending")
    # detect vendor updater tasks that appear or are re-enabled after being disabled
    pt = {t.get("path", "") + t.get("name", ""): t.get("state") for t in prev.get("update_tasks") or []}
    for t in cur.get("update_tasks") or []:
        key = t.get("path", "") + t.get("name", "")
        if (t.get("name") or "").startswith("HAI "):     # the lab's own tasks (this one included) are not vendor news
            continue
        if key not in pt:
            installed.append("new updater task %s (%s)" % (t.get("name"), t.get("state")))
        elif pt[key] != t.get("state"):
            installed.append("updater task %s: %s -> %s" % (t.get("name"), pt[key], t.get("state")))
    pw = {u.get("title") for u in prev.get("wu_pending") or []}
    for u in cur.get("wu_pending") or []:
        if u.get("title") not in pw and not NOISE.search(u.get("title") or ""):
            waiting.append("Windows Update: %s" % u.get("title"))
    pg, cg = winget_table((prev.get("winget_upgrade") or {}).get("text")), winget_table((cur.get("winget_upgrade") or {}).get("text"))
    for pid in sorted(cg):
        if pg.get(pid) != cg[pid]:
            waiting.append("%s %s -> %s" % (pid, cg[pid][0], cg[pid][1]))
    return installed, waiting


def diff_linux(prev, cur):
    installed, waiting = [], []
    if prev.get("kernel") != cur.get("kernel"):
        installed.append("kernel %s -> %s" % (prev.get("kernel"), cur.get("kernel")))
    img = lambda rows: {r.split(" ")[0]: r.split(" ")[1] for r in rows or [] if " " in r}  # noqa: E731
    pi, ci = img(prev.get("images")), img(cur.get("images"))
    for name in sorted(ci):
        if name in pi and pi[name] != ci[name]:
            installed.append("image %s rebuilt/pulled" % name)
    pe, ce = set(prev.get("engine_git") or []), set(cur.get("engine_git") or [])
    for e in sorted(ce - pe):
        installed.append("engine %s" % e)
    if cur.get("reboot_required") and not prev.get("reboot_required"):
        installed.append("a reboot is now required")
    pkg = lambda rows: {r.split("/")[0] for r in rows or []}  # noqa: E731
    new = sorted(pkg(cur.get("upgradable")) - pkg(prev.get("upgradable")))
    if new:
        waiting.append("apt: %d new (%s)" % (len(new), ", ".join(new[:6]) + (" ..." if len(new) > 6 else "")))
    return installed, waiting


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "hai-lab-update-notify", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


LAB_ENGINE_PIN = "b11669"     # the 10-22 build (llama.cpp 620d2225c); engine_pipeline's state file overrides this once it has run


def lab_engine_pin(state_dir=None):
    """The llama.cpp build the fleet serves, from the engine pipeline's own state (live.build), else the 10-22 constant."""
    try:
        live = json.load(open(os.path.join(state_dir or STATE, "engine.json"), encoding="utf-8")).get("live") or {}
        return live.get("build") or LAB_ENGINE_PIN, live
    except (OSError, ValueError):
        return LAB_ENGINE_PIN, {}


def ollama_pin_line(tag, fetch=None, state_dir=None):
    """A new ollama release's pinned llama.cpp build vs the lab engine's. The lab engine is a LAB BUILD at ollama's pinned
    commit plus lab patches (#0/#2/#3) and ollama's compat layer - a new pin means the LAB rebuilds it: engine_pipeline.py
    (the 02:00 task) takes releases 7+ days old through prepare -> build order -> canary -> fleet -> verify on its own,
    and stops for a person only when a patch no longer applies. This line says where that stands."""
    pin_ours, live = lab_engine_pin(state_dir)
    try:
        if fetch is None:
            req = urllib.request.Request("https://raw.githubusercontent.com/ollama/ollama/%s/LLAMA_CPP_VERSION" % tag,
                                         headers={"User-Agent": "hai-lab-update-notify"})
            with urllib.request.urlopen(req, timeout=20) as r:
                pin = r.read().decode("utf-8").strip()
        else:
            pin = fetch(tag)
    except Exception as e:                                                      # noqa: BLE001
        return "ollama %s: its llama.cpp pin could not be read (%s)" % (tag, type(e).__name__)
    if pin == pin_ours:
        return "ollama %s pins llama.cpp %s = the lab engine's; no rebuild needed" % (tag, pin)
    return ("ollama %s pins llama.cpp %s; the lab engine is on %s - the engine pipeline rebuilds once the release is "
            "%d days old (patches #0/#2/#3 + compat, build order, canary, fleet); a patch that no longer applies is the "
            "one thing it asks a person for (Lab-Update-Inventory-38 § Lab engine)" % (tag, pin, pin_ours, 7))


def upstream(seen):
    """New upstream releases since the previous night -> (news lines, updated seen-map). A failed ask is said, not hidden."""
    news, now = [], dict(seen)
    for repo, ours in UPSTREAM.items():
        try:
            tag = _get_json("https://api.github.com/repos/%s/releases/latest" % repo).get("tag_name")
        except Exception as e:                                                  # noqa: BLE001
            news.append("upstream %s: could not ask (%s)" % (repo, type(e).__name__))
            continue
        if tag and seen.get(repo) and seen[repo] != tag:
            news.append("upstream %s released %s (we run: %s)" % (repo, tag, ours))
            if repo == "ollama/ollama":
                news.append(ollama_pin_line(tag))
        if tag:
            now[repo] = tag
    for repo, ours in UPSTREAM_COMMITS.items():
        try:
            c = _get_json("https://api.github.com/repos/%s/commits?per_page=1" % repo)[0]
            sha, day = c["sha"][:10], c["commit"]["committer"]["date"][:10]
        except Exception as e:                                                  # noqa: BLE001
            news.append("upstream %s: could not ask (%s)" % (repo, type(e).__name__))
            continue
        if seen.get(repo) and seen[repo].split(" ")[0] != sha:
            news.append("upstream %s has new commits (latest %s, %s; we run: %s)" % (repo, sha, day, ours))
        now[repo] = "%s %s" % (sha, day)
    return news, now


def field_text(asked, per_box, ups, first=None):
    """The Discord field: `first` (what the lab's stager did), then INSTALLED, WAITING, UPSTREAM; the per-box totals
    line always last."""
    lines = list(first or [])
    for box, d in per_box.items():
        if d.get("could_not_ask"):
            lines.append("%s: could not ask" % box)
            continue
        for x in d["installed"]:
            lines.append("⚠ %s: %s" % (box, x))
    for box, d in per_box.items():
        for x in d.get("waiting") or []:
            lines.append("%s: %s" % (box, x))
    lines += ups
    if not any(d.get("installed") or d.get("waiting") for d in per_box.values()) and not ups:
        lines.append("Nothing new since the last reading.")
    tail = "Waiting: " + "; ".join("%s %s" % (b, d["counts"]) for b, d in per_box.items() if d.get("counts"))
    room = FIELD_MAX - len(tail) - 70            # the totals line is always kept; the news gets what is left
    body, used, shown = [], 0, 0
    for ln in lines:
        if used + len(ln) + 1 > room:
            break
        body.append(ln)
        used += len(ln) + 1
        shown += 1
    if shown < len(lines):
        body.append("+%d more in State/lab-updates/report-%s.json" % (len(lines) - shown, asked))
    body.append(tail)
    return "\n".join(body)[:FIELD_MAX]


def counts(box, d):
    if box in inv.WINDOWS:
        wu = [u for u in d.get("wu_pending") or [] if not NOISE.search(u.get("title") or "")]
        return "WU %d, winget %d" % (len(wu), len(winget_table((d.get("winget_upgrade") or {}).get("text"))))
    return "apt %d" % len(d.get("upgradable") or [])


def main(argv):
    dry = "--dry" in argv
    boxes = [a for a in argv if not a.startswith("--")] or list(inv.WINDOWS + inv.LINUX)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    got = inv.collect(boxes, stamp)
    per_box = {}
    for box in boxes:
        cur = got[box]
        if "could_not_ask" in cur:
            per_box[box] = {"could_not_ask": cur["could_not_ask"][-200:]}
            continue
        prev = last_good(box, stamp)
        if prev is None:
            per_box[box] = {"installed": [], "waiting": [], "first_reading": True, "counts": counts(box, cur)}
            continue
        i, w = (diff_windows if box in inv.WINDOWS else diff_linux)(prev, cur)
        per_box[box] = {"installed": i, "waiting": w, "counts": counts(box, cur)}
    seen_path = os.path.join(STATE, "upstream.json")
    try:
        seen = json.load(open(seen_path, encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    ups, seen_now = upstream(seen)
    asked = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    text = field_text(stamp, per_box, ups)
    report = "asked %s\n%s\nEND\n" % (asked, text)
    if dry:
        print(report)
        print(json.dumps(per_box, indent=1)[:4000])
        return 0
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "report-%s.json" % stamp), "w", encoding="utf-8") as f:
        json.dump({"asked": asked, "boxes": per_box, "upstream": ups, "upstream_seen": seen_now}, f, indent=1)
    for path, body in ((seen_path, json.dumps(seen_now, indent=1)), (os.path.join(STATE, "report.txt"), report)):
        with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        os.replace(path + ".tmp", path)
    print(report)
    return 0


if __name__ == "__main__":
    # runs headless (pythonw, nightly task): every run leaves a line, and a crash leaves its traceback
    try:
        rc = main(sys.argv[1:])
        msg = "ok"
    except Exception:                                                           # noqa: BLE001
        import traceback
        rc, msg = 1, traceback.format_exc()
    try:
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, "notify.log"), "a", encoding="utf-8") as f:
            f.write("%s rc=%s %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), rc, msg))
    except OSError:
        pass
    sys.exit(rc)
