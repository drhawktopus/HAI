r"""Camera questions for HAI — event queries, live snapshots, and retrospective footage scans.

The event-backed answers have no source until Tolkien's event store lands (EVENT_BACKEND is None); a
question then returns one honest "capability is mid-move" sentence rather than a backend error. See
`Reference\Lorex-NVR-Operations.md`. `scan_footage()` needs no event backend — it mines recorded NVR
frames as a tablet-foundry order, so retrospective questions are answerable today; the Lorex NVR owns
motion detection, 24/7 recording and snapshots throughout.

Channel names are not room names: ch1 = dining_room, ch2 = basement (that camera was relocated from
the front door). Use the camera names, not the NVR channel numbers.

Sam's detection label flickers between `cat` and `dog`, so any "cat" question must accept BOTH labels
or it silently loses ~half the sightings. `ANIMAL_LABELS` exists so no caller has to remember that.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zoneinfo
from datetime import datetime

import config

# Human-facing timestamps are in the lab's zone (config.LAB_ZONE, from the container's TZ). Pass the zone explicitly; never read the ambient
# process timezone (time.localtime()) — that is correct only because the container runs
# that TZ, and is wrong in any host-level script or cron.
CAMERA_TZ = config.LAB_ZONE


def fmt_local(epoch, fmt="%Y-%m-%d %H:%M:%S"):
    """Central time, always. Display only."""
    return datetime.fromtimestamp(float(epoch or 0), CAMERA_TZ).strftime(fmt)

FRIGATE = getattr(config, "FRIGATE_URL", "http://frigate:5000")
TIMEOUT = 12

# Deliberately None until Tolkien's event store lands; set EVENT_BACKEND = "tolkien" to re-enable.
EVENT_BACKEND = None

_NO_BACKEND = (
    "I can't answer that yet — the camera event store is mid-move.\n"
    "  Frigate was deliberately retired on 2026-08-12 and its job (object detection, tracking, "
    "zones and the event log) is being rebuilt inside Tolkien. Until that lands there is no event "
    "history to query, so this is a known gap and not a fault to chase.\n"
    "  Still working right now: the Lorex NVR is untouched — it records 24/7, does motion detection "
    "in hardware and serves live snapshots. Retrospective questions about recorded footage can be "
    "answered by submitting a scan order (`scan_footage`), which never depended on Frigate."
)

# Both labels deliberate — Sam's label flickers cat/dog (see module docstring); there is no dog here.
ANIMAL_LABELS = ("cat", "dog")

# Camera/channel name -> room label, for phrasing answers back to the user.
ROOMS = {"dining_room": "the dining room", "basement": "the basement"}


def _get(path, params=None):
    """GET an event-backend API path. Returns (ok, parsed_or_error_string). Never raises.

    Fail-soft, not fail-silent: an unavailable backend returns an error sentence, not an empty
    result that the model reads as 'nothing happened'."""
    if not EVENT_BACKEND:
        return False, _NO_BACKEND
    url = FRIGATE.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            return True, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, "The event store returned HTTP %s for %s" % (e.code, path)
    except Exception as e:
        return False, "Could not reach the event store (%s: %s)" % (type(e).__name__, str(e)[:120])


def _score(ev):
    """Top score, tolerating the 0.17 schema move of the field into `data`.

    Reading the old top-level `top_score` on 0.17 yields 0.00 for every event; read `data.top_score`."""
    d = ev.get("data") or {}
    return float(d.get("top_score") or ev.get("top_score") or 0.0)


def _when(ts, now=None):
    """Human 'how long ago', because a unix stamp is useless in a chat answer."""
    secs = int((now or time.time()) - ts)
    if secs < 90:
        return "%d seconds ago" % secs
    if secs < 5400:
        return "%d minutes ago" % (secs // 60)
    if secs < 172800:
        return "%.1f hours ago" % (secs / 3600.0)
    return "%.1f days ago" % (secs / 86400.0)


def events(camera=None, label=None, zone=None, hours=24, limit=15):
    """Recent detections, newest first, as a readable summary.

    label='cat' automatically includes 'dog' -- see ANIMAL_LABELS."""
    try:
        hours = max(0.1, float(hours))
        limit = max(1, min(int(limit), 50))
    except Exception:
        hours, limit = 24.0, 15

    params = {"limit": 200, "after": time.time() - hours * 3600}
    if camera:
        params["cameras"] = camera
    ok, data = _get("/api/events", params)
    if not ok:
        return data
    if not isinstance(data, list):
        return "The event store returned an unexpected response for events."

    wanted = set(ANIMAL_LABELS) if (label or "").lower() in ANIMAL_LABELS else (
        {label.lower()} if label else None)
    rows = []
    for e in data:
        if wanted and (e.get("label") or "").lower() not in wanted:
            continue
        if zone and zone not in (e.get("zones") or []):
            continue
        rows.append(e)
    rows.sort(key=lambda e: e.get("start_time") or 0, reverse=True)

    if not rows:
        scope = []
        if label:
            scope.append("%s%s" % (label, " (or dog)" if label.lower() in ANIMAL_LABELS else ""))
        if zone:
            scope.append("in the %s zone" % zone)
        if camera:
            scope.append("on %s" % ROOMS.get(camera, camera))
        return ("No %s detections in the last %g hours%s. (The event store answered — this "
                "is a real absence, not a failure.)"
                % (" ".join(scope) or "camera", hours, ""))

    now = time.time()
    out = ["%d detection(s) in the last %g hours:" % (len(rows), hours)]
    for e in rows[:limit]:
        z = ",".join(e.get("zones") or []) or "-"
        out.append("  %s  %-11s %-6s %d%%  zone=%s  (%s)" % (
            fmt_local(e.get("start_time") or 0, "%H:%M:%S"),
            e.get("camera"), e.get("label"), round(_score(e) * 100), z,
            _when(e.get("start_time") or now, now)))
    if len(rows) > limit:
        out.append("  ... and %d more" % (len(rows) - limit))
    return "\n".join(out)


def last_seen(label="cat", camera=None, zone=None, days=7):
    """When was X last detected? The 'when did Sam last use the litter box' question."""
    ok, data = _get("/api/events", {"limit": 500, "after": time.time() - float(days) * 86400,
                                    **({"cameras": camera} if camera else {})})
    if not ok:
        return data
    wanted = set(ANIMAL_LABELS) if (label or "").lower() in ANIMAL_LABELS else {(label or "").lower()}
    rows = [e for e in data
            if (e.get("label") or "").lower() in wanted
            and (not zone or zone in (e.get("zones") or []))]
    if not rows:
        return ("No %s seen%s in the last %g days." % (
            label, (" in the %s zone" % zone) if zone else "", float(days)))
    e = max(rows, key=lambda x: x.get("start_time") or 0)
    return ("Last %s: %s (%s) on %s%s, %d%% confidence." % (
        label, fmt_local(e["start_time"], "%a %H:%M:%S"),
        _when(e["start_time"]), ROOMS.get(e.get("camera"), e.get("camera")),
        (" in the %s zone" % ",".join(e.get("zones"))) if e.get("zones") else "",
        round(_score(e) * 100)))


def status():
    """Are the cameras actually working? Detection health, not just container uptime.

    Checks `detection_fps`, not `camera_fps`: the decode metric can look healthy while detection is
    off entirely."""
    ok, data = _get("/api/stats")
    if not ok:
        return data
    cams = (data or {}).get("cameras") or {}
    if not cams:
        return "The event store answered but reports no cameras."
    lines = []
    for name, v in sorted(cams.items()):
        lines.append("  %-12s decode %.1f fps · detection %.1f fps" % (
            name, v.get("camera_fps") or 0, v.get("detection_fps") or 0))
    det = {k: round(v.get("inference_speed", 0), 1)
           for k, v in ((data or {}).get("detectors") or {}).items()}
    lines.append("  detector: %s (ms per inference)" % (det or "none"))
    lines.append("  note: detection fps is 0 when nothing is moving — that is normal, not a fault.")
    return "Camera status:\n" + "\n".join(lines)


def scan_footage(camera, start, end, note=""):
    """Submit an order to scan recorded NVR footage for a window the event store did not index.

    Works with no event backend: reads recorded frames from the NVR and hands them to the tablet
    foundry, so it never depended on the event store. Non-blocking — playback is paced 1x per
    session, so even ~10 parallel windows take minutes; the foundry owns the run (resumable,
    checkpointed) and results land in the outbox.

    Returns the order name so the answer can be collected later."""
    import os
    import re
    if camera not in ROOMS:
        return "Unknown camera %r. Known: %s" % (camera, ", ".join(sorted(ROOMS)))
    if not re.match(r"^\d{2}:\d{2}$", start or "") or not re.match(r"^\d{2}:\d{2}$", end or ""):
        return "start and end must be HH:MM (24h), e.g. start='02:00' end='04:00'."

    name = "cam-scan.%s.%s" % (camera, fmt_local(time.time(), "%Y%m%d-%H%M%S"))
    inbox = config.TABLET_INBOX_HOST or "the tablet foundry's inbox"
    import tablet_orders                       # the payload's path as the tablet foundry sees the share
    spec = {
        "kind": "map",
        "name": name,
        "payload": tablet_orders.FOUNDRY_SHARE + "/payloads/map_motion.py",
        "items_list": "",
        "notes": ("Retrospective footage scan requested via HAI. camera=%s window=%s-%s. %s"
                  % (camera, start, end, note)),
        "_pending_frames": {"camera": camera, "start": start, "end": end},
    }
    try:
        os.makedirs(inbox, exist_ok=True)
        with open(os.path.join(inbox, name + ".tjob.json"), "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=1)
    except Exception as e:
        return "Could not submit the scan order (%s: %s)" % (type(e).__name__, str(e)[:120])
    return ("Submitted footage scan order '%s' for %s, %s-%s. Frames must still be mined from the "
            "NVR before it can run — this is queued work, not an instant answer. Results land in "
            "the tablet foundry outbox." % (name, ROOMS.get(camera, camera), start, end))
