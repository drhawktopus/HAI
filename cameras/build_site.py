"""Generate the household event timeline served at the cameras site (CAMERAS_SITE_URL).

NOT a cat gallery.

So this is a searchable record of what happened in the house. Cats are the most frequent subject,
people are another, and the litter-box and burglary questions are the SAME query with different
region and time filters -- which is why filtering is the structure and nothing is themed.

First cut: static HTML + thumbnails, generated from the corpus already on disk. No database, no
framework. Descriptions and cat identity get added later as columns in the same layout, so this
is not throwaway.

 Images are 4K now. Full frames are shipped as-is for click-through but the grid uses generated
thumbnails -- a wall of 8MP JPEGs would be unusable on a phone, which is where the owner will read it.
"""
import glob
import json
import os
import re
import shutil
import sys
import time

import numpy as np
from PIL import Image
from cam_settings import S

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "site")
FRIGATE = S.setting("FRIGATE_URL", "")   # authenticated; see frigate_open
NVR_URL = S.setting("NVR_SITE_URL", "") or "http://%s/" % S.setting("NVR_HOST", "")   # the NVR's own pages
MAX_EVENTS = int(os.environ.get("SITE_MAX_EVENTS", "400"))
# Frigate camera name -> the channel number the page has always filtered on.
CH_OF = {"dining_room": "1", "basement": "2"}
THUMBS = os.path.join(SITE, "thumbs")
FULL = os.path.join(SITE, "full")
THUMB_W = 480
SAT_CUTOFF = 8.0

CAMS = {"1": "Family / dining room", "2": "Bedroom (front door view)"}


# ── FRIGATE SESSION ──────────────────────────────────────────────────────────────────────────────
# Frigate is authenticated (Caddy ->:8971). The unauthenticated:5000 is deliberately
# internal to Sundae, so a LAN-side generator must log in rather than reach around the auth.
_OPENER = None


def _credential():
    """(user, password) from the environment, else the gated credentials file. Never inline."""
    import os
    import re
    u = os.environ.get("FRIGATE_USER") or "admin"
    p = os.environ.get("FRIGATE_PASS")
    if p:
        return u, p
    path = os.environ.get("FRIGATE_CRED_FILE") or S.setting("CAMERA_CRED_DOC", "")
    # `open`, not `io.open` — this module never imports `io`. It did read `io.open` briefly, and
    # the NameError was swallowed by the bare `except` below, which then reported "no password
    # found" for a credential sitting exactly where expected. Fail-soft turning into fail-SILENT.
    try:
        with open(path, encoding="utf-8") as fh:
            txt = fh.read()
    except OSError as exc:
        print("  credential file unreadable (%s) — set FRIGATE_PASS instead" % exc)
        return u, None
    try:
        block = txt.split("Frigate (Sundae)", 1)[1]
        m = re.search(r"\*\*Password\*\*\s*\|\s*`([^`]+)`", block)
        if m:
            return u, m.group(1)
        print("  credential file read but no Frigate password row matched")
    except IndexError:
        print("  credential file has no 'Frigate (Sundae)' block")
    return u, None


def frigate_open(url, timeout=25):
    """urlopen against Frigate with a logged-in session. Raises if login is impossible."""
    global _OPENER
    if not FRIGATE:
        raise RuntimeError("set FRIGATE_URL (Frigate's address)")
    import http.cookiejar
    import urllib.request
    if _OPENER is None:
        user, pw = _credential()
        if not pw:
            raise RuntimeError(
                "No Frigate password. Set FRIGATE_PASS, or check the 'Frigate (Sundae)' block in "
                "the camera credentials document (CAMERA_CRED_DOC)")
        jar = http.cookiejar.CookieJar()
        _OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        body = json.dumps({"user": user, "password": pw}).encode("utf-8")
        req = urllib.request.Request(FRIGATE.rstrip("/") + "/api/login", data=body,
                                     headers={"Content-Type": "application/json"})
        with _OPENER.open(req, timeout=20) as r:
            if r.status != 200:
                raise RuntimeError("Frigate login failed: HTTP %s" % r.status)
    return _OPENER.open(url, timeout=timeout)


def colour_of(im):
    return float(np.asarray(im.convert("HSV"))[:, :, 1].mean()) > SAT_CUTOFF


def load_boxes():
    """Motion regions as DATA, from the Fellowship's results (site/boxes.json).

     Boxes used to be burned into the JPEGs by a local script. That was backwards: the FELLOWSHIP
    computes them now, so they belong in the page as coordinates drawn over the image, not baked
    into pixels. Keeping them as data means re-running the detector never re-encodes a single
    frame, and the same numbers can feed the litter-box ROI test later.

    Absent file = no boxes drawn. That is NOT "no motion" -- it means the order has not run yet,
    and the page says so rather than implying an empty result.
    """
    p = os.path.join(SITE, "boxes.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def collect():
    """Events from FRIGATE, which is the detector now.

     Replaced a glob over `corpus_recorded/` + `boxes.json`. That pipeline was background
    subtraction, built before Frigate existed and superseded by it — it could only ever say
    "something changed in this region", never "cat, 82%". The page was rendering its output from
    18:14 while Frigate held 131 real detections.

    Frigate's snapshot is fetched WITH `bbox=1`, so the box is burned in by the detector that drew
    it rather than re-derived here. One source of truth for where the object was.
    """
    out = []
    url = FRIGATE.rstrip("/") + "/api/events?limit=%d" % MAX_EVENTS
    with frigate_open(url, timeout=25) as r:
        raw = json.loads(r.read().decode("utf-8"))

    for e in raw:
        ts = e.get("start_time") or 0
        lt = time.localtime(ts)
        cam = e.get("camera") or "?"
        # 0.17 moved the score into `data`. Reading the old top-level field yields 0.00 for
        # every event and silently renders "0%" beside a perfectly good detection.
        d = e.get("data") or {}
        score = float(d.get("top_score") or e.get("top_score") or 0.0)
        out.append({
            "file": "%s_%s_%s.jpg" % (time.strftime("%Y%m%d-%H%M%S", lt), cam, e.get("id", "x")[-6:]),
            "eid": e.get("id"),
            "date": time.strftime("%Y-%m-%d", lt),
            "time": time.strftime("%H:%M:%S", lt),
            "ch": CH_OF.get(cam, "?"),
            "cam": cam,
            "label": e.get("label") or "?",
            "score": score,
            "zones": e.get("zones") or [],
            "has_clip": bool(e.get("has_clip")),
        })
    out.sort(key=lambda x: (x["date"], x["time"]), reverse=True)
    return out


def fetch_snapshot(e, dest):
    """Frigate's own snapshot, box burned in. Returns True if written."""
    url = "%s/api/events/%s/snapshot.jpg?bbox=1" % (FRIGATE.rstrip("/"), e["eid"])
    try:
        with frigate_open(url, timeout=25) as r:
            data = r.read()
        if len(data) < 2000:          # Frigate returns a tiny body while an event is still open
            return False
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def main():
    for d in (THUMBS, FULL):
        os.makedirs(d, exist_ok=True)
    events = collect()
    print("events from Frigate: %d" % len(events))

    kept = []
    for e in events:
        full_path = os.path.join(FULL, e["file"])
        if not os.path.exists(full_path) and not fetch_snapshot(e, full_path):
            # Skipped, not faked. An event whose snapshot is not ready yet (still in progress)
            # gets dropped from THIS build and picked up by the next one -- rendering a card with a
            # broken image would look like a camera fault.
            continue
        im = Image.open(full_path).convert("RGB")
        e["colour"] = colour_of(im)
        e["w"], e["h"] = im.size
        e["boxes"] = []          # Frigate burns the box into the snapshot; no overlay needed
        e["boxed"] = bool(e["zones"])
        h = int(im.height * THUMB_W / im.width)
        im.resize((THUMB_W, h)).save(os.path.join(THUMBS, e["file"]), quality=82)
        kept.append(e)
    skipped = len(events) - len(kept)
    events = kept
    if skipped:
        print("  %d event(s) skipped: snapshot not ready yet (will appear next build)" % skipped)

    boxed = sum(1 for e in events if e["boxed"])
    from collections import Counter
    print("  by label: %s | in a zone: %d | colour: %d | IR: %d"
          % (dict(Counter(e["label"] for e in events)), boxed,
             sum(1 for e in events if e["colour"]),
             sum(1 for e in events if not e["colour"])))

    with open(os.path.join(SITE, "events.json"), "w", encoding="utf-8") as f:
        json.dump(events, f, indent=1)

    cards = []
    for e in events:
        overlays = "".join(
            '<b style="left:{l:.3%};top:{t:.3%};width:{w:.3%};height:{h:.3%}"></b>'.format(
                l=b[0], t=b[1], w=max(b[2] - b[0], 0.004), h=max(b[3] - b[1], 0.004))
            for b in e["boxes"])
        cards.append(
            '<a class="ev" href="full/{f}" target="_blank" data-ch="{ch}" '
            'data-date="{d}" data-mode="{mode}" data-boxed="{b}" data-label="{lab}">'
            '<div class="shot"><img loading="lazy" src="thumbs/{f}" alt="">{ov}</div>'
            '<div class="meta"><span class="t">{d} {tm}</span>'
            '<span class="c">{cam}</span>'
            '<span class="tags">{tags}</span></div></a>'.format(
                f=e["file"], ch=e["ch"], d=e["date"], tm=e["time"], ov=overlays,
                cam=CAMS.get(e["ch"], e.get("cam", "cam %s" % e["ch"])),
                mode="colour" if e["colour"] else "ir",
                b="1" if e["boxed"] else "0",
                lab=e["label"],
                tags=("".join(
                    ['<i class="tag box">%s %d%%</i>' % (e["label"], round(e["score"] * 100)),
                     '<i class="tag box">%s</i>' % ",".join(e["zones"]) if e["zones"] else "",
                     '<i class="tag ir">IR</i>' if not e["colour"] else ""]))))

    html = TEMPLATE.replace("{{CARDS}}", "\n".join(cards)) \
                   .replace("{{COUNT}}", str(len(events))) \
                   .replace("{{BOXED}}", str(boxed)) \
                   .replace("{{NVR_URL}}", NVR_URL)
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print("-> %s" % os.path.join(SITE, "index.html"))


TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cameras — household events</title>
<style>
 :root{--bg:#14161a;--card:#1c1f25;--line:#2b2f37;--txt:#e6e8ec;--dim:#9aa3af;--acc:#ff4d4d}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--txt);
      font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
 header{padding:16px 18px;border-bottom:1px solid var(--line);position:sticky;top:0;
        background:var(--bg);z-index:5}
 h1{margin:0 0 2px;font-size:19px}
 .sub{color:var(--dim);font-size:13px}
 .bar{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
 button{background:var(--card);color:var(--txt);border:1px solid var(--line);
        border-radius:999px;padding:7px 13px;font-size:13px;cursor:pointer}
 button.on{background:#2f6fed;border-color:#2f6fed;color:#fff}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));
       gap:10px;padding:14px}
 .ev{background:var(--card);border:1px solid var(--line);border-radius:10px;
     overflow:hidden;text-decoration:none;color:inherit;display:block}
 /* MUST come after .ev: `display:block` on a class beats the UA stylesheet's [hidden] rule,
    so setting el.hidden=true had no visual effect and every filter appeared to do nothing. */
 .ev[hidden]{display:none}
 .shot{position:relative;line-height:0}
 .ev img{width:100%;display:block;aspect-ratio:16/9;object-fit:cover;background:#000}
 /* motion regions: DATA from the Fellowship, drawn as overlays. Percent units so the same
    numbers keep working when frames go 4K -- nothing is baked into the pixels. */
 .shot b{position:absolute;border:2px solid var(--acc);border-radius:2px;
         box-shadow:0 0 0 1px rgba(0,0,0,.45)}
 .meta{padding:8px 10px;display:flex;flex-direction:column;gap:2px}
 .t{font-variant-numeric:tabular-nums;font-size:13px}
 .c{color:var(--dim);font-size:12px}
 .tags{margin-top:4px;display:flex;gap:5px;flex-wrap:wrap}
 .tag{font-style:normal;font-size:11px;padding:2px 7px;border-radius:999px;
      border:1px solid var(--line);color:var(--dim)}
 .tag.box{border-color:var(--acc);color:var(--acc)}
 .empty{padding:40px;text-align:center;color:var(--dim)}
 footer{padding:16px 18px;color:var(--dim);font-size:12px;border-top:1px solid var(--line)}
 @media(prefers-color-scheme:light){
   :root{--bg:#f6f7f9;--card:#fff;--line:#e2e5ea;--txt:#14161a;--dim:#5b636e}}
</style></head><body>
<header>
  <h1>Cameras</h1>
  <div class="sub">{{COUNT}} events &middot; {{BOXED}} with a detected motion region &middot;
     <a href="{{NVR_URL}}" style="color:#6ea8ff">live view &amp; playback &rarr;</a></div>
  <div class="bar" id="bar">
    <button data-f="all" class="on">All</button>
    <button data-f="ch1">Dining room</button>
    <button data-f="ch2">Basement</button>
    <button data-f="person">People</button>
    <button data-f="cat">Cats</button>
    <button data-f="boxed">In a zone</button>
    <button data-f="ir">IR / night</button>
    <button data-f="colour">Colour</button>
  </div>
</header>
<div class="grid" id="grid">
{{CARDS}}
</div>
<div class="empty" id="empty" hidden>No events match that filter.</div>
<footer>
  Snapshots pulled from the NVR at each motion event. Red boxes mark where the scene changed
  against a median background &mdash; static clutter (soot, furniture) is invisible to it by
  construction. Descriptions and per-animal identity are not built yet.
</footer>
<script>
 const grid=document.getElementById('grid'),empty=document.getElementById('empty');
 document.getElementById('bar').addEventListener('click',e=>{
   const b=e.target.closest('button'); if(!b) return;
   [...e.currentTarget.children].forEach(x=>x.classList.toggle('on',x===b));
   const f=b.dataset.f; let shown=0;
   [...grid.children].forEach(card=>{
     const ok = f==='all'
       || (f==='ch1'&&card.dataset.ch==='1')
       || (f==='ch2'&&card.dataset.ch==='2')
       || (f==='boxed'&&card.dataset.boxed==='1')
       || (f==='person'&&card.dataset.label==='person')
       /* cat AND dog: the detector labels the same cat either way (measured: cat 70% then
          dog 60% on the same animal 20s apart), so a cat filter that omits dog hides half of them */
       || (f==='cat'&&(card.dataset.label==='cat'||card.dataset.label==='dog'))
       || (f==='ir'&&card.dataset.mode==='ir')
       || (f==='colour'&&card.dataset.mode==='colour');
     card.hidden=!ok; if(ok) shown++;
   });
   empty.hidden = shown>0;
 });
</script>
</body></html>
"""

if __name__ == "__main__":
    main()
