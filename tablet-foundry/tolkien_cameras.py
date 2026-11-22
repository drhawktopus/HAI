"""Tolkien camera event store.

Subscribes to NVR motion events, maintains a durable append-only JSONL record, and prunes
data to prevent disk exhaustion. Used exclusively by the Tolkien head (`tablet-foundry`);
never imported by edge agents.

Storage Constraints & Invariants:
    - Format: Append-only JSONL (no SQLite) for consistency with lab persistence conventions.
    - Location: Local ext4 host bind mount only. Never CIFS or container layers, as deploy
      routines routinely recreate containers and discard image-internal state.
    - Retention Bounds (Independent):
        1. Metadata: Deleted after 365 days.
        2. Snapshots: Deleted after 90 days.
        3. Hard Ceiling: Snapshot directory capped at 12 GB total (oldest-first eviction),
           regardless of age, to protect against traffic spikes exceeding rate-based estimates.
    - Observability: Both age and size pruning log dropped items; silent truncation is avoided.

Directory Layout:
    events/<YYYY-MM-DD>.jsonl       Motion event records (day boundary in the lab's zone, NVR_TZ).
    rejects/<YYYY-MM-DD>.jsonl      Classified duplicate/rejected events.
    snapshots/                      960px JPEG frames.
    backups/                        Nightly copies (~14 days retention).
    subscriber.beat                 Liveness file, updated by the worker loop.

Scope Boundaries:
    - Motion detection: Handled by NVR hardware.
    - Video storage: Referenced via NVR's 2 TB local storage; clips are not copied here.
    - Analysis (tracking/zones): Delegation to later stages (Fellowship/Tolkien), not this module.
"""
import io
import json
import os
import shutil
import time
import zoneinfo
from datetime import datetime, timedelta

STORE_VERSION = 1

# /tabfoundry is the tablet-foundry container's LOCAL bind (host /opt/arrstack/tablet-foundry).
HOME = os.environ.get("TOLKIEN_CAMERAS_HOME", "/tabfoundry/cameras")
EVENT_DIR = os.environ.get("TOLKIEN_CAMERAS_EVENTS", os.path.join(HOME, "events"))
REJECT_DIR = os.environ.get("TOLKIEN_CAMERAS_REJECTS", os.path.join(HOME, "rejects"))
SNAP_DIR = os.environ.get("TOLKIEN_CAMERAS_SNAPS", os.path.join(HOME, "snapshots"))
BACKUP_DIR = os.environ.get("TOLKIEN_CAMERAS_BACKUPS", os.path.join(HOME, "backups"))
BEAT_PATH = os.environ.get("TOLKIEN_CAMERAS_BEAT", os.path.join(HOME, "subscriber.beat"))

METADATA_DAYS = float(os.environ.get("TOLKIEN_CAMERAS_METADATA_DAYS", 365))
SNAPSHOT_DAYS = float(os.environ.get("TOLKIEN_CAMERAS_SNAPSHOT_DAYS", 90))
SNAPSHOT_CEILING_BYTES = int(float(os.environ.get("TOLKIEN_CAMERAS_CEILING_GB", 12)) * (1 << 30))
BACKUPS_KEPT = int(os.environ.get("TOLKIEN_CAMERAS_BACKUPS_KEPT", 14))

# The NVR speaks LOCAL wall-clock; Sundae's clock is UTC. See `nvr_time_to_epoch`.
NVR_TZ = zoneinfo.ZoneInfo(os.environ.get("TOLKIEN_CAMERAS_TZ") or os.environ.get("TZ") or "UTC")
# Paths on a network filesystem, where the store must never live: anything under /mnt, plus the container's own
# share mounts (TOLKIEN_CAMERAS_NETWORK_MOUNTS, comma-separated prefixes; the compose file names them).
NETWORK_MOUNTS = ("/mnt/",) + tuple(m.strip() for m in os.environ.get("TOLKIEN_CAMERAS_NETWORK_MOUNTS", "").split(",") if m.strip())

# Map channel IDs to room names. NVR ChannelTitles are stale; use this for UI,
# and channel IDs (keys) for all NVR API calls.
CHANNEL_ROOM = {1: "dining_room", 2: "basement"}

# The field set. Kept as a constant so writer and reader cannot drift -- the producer/consumer
# disagreement is this lab's most-repeated bug (a `\t` that was never emitted dropped 267 rows
# while 185 unit tests stayed green).
FIELDS = ("camera", "channel", "started_at", "ended_at", "nvr_code", "seq",
          "source", "action", "colour", "snapshot", "note", "created_at")

NVR_HOST = os.environ.get("TOLKIEN_NVR_HOST", "")      # the NVR's address; unset = no cameras linked
SNAPSHOT_EDGE = int(os.environ.get("TOLKIEN_CAMERAS_SNAP_EDGE", 960))
SNAPSHOT_QUALITY = int(os.environ.get("TOLKIEN_CAMERAS_SNAP_QUALITY", 82))
BEAT_PERIOD = float(os.environ.get("TOLKIEN_CAMERAS_BEAT_PERIOD", 30))
# The NVR's own heartbeat is 5 s, so a socket silent for much longer than that is a dead pipe, not
# a quiet house. Generous multiple: we would rather reconnect late than churn on a healthy link.
READ_TIMEOUT = float(os.environ.get("TOLKIEN_CAMERAS_READ_TIMEOUT", 30))


# Degradations that still "work" and therefore report nothing on their own. A path that silently
# costs more than the happy path is the one that needs a counter -- it will not announce itself.
DEGRADED = {"downscale_failures": 0, "last_downscale_error": None}


def store_status():
    """Return store status for the tile and post-mortems.

    Read-only; does not probe external services. Returns DEGRADED separately from
    down to catch silent resource bloat (e.g., oversized snapshots) that would
    otherwise remain invisible until disk space is exhausted."""
    state, why = subscriber_verdict()
    snaps = _dir_files(SNAP_DIR) if os.path.isdir(SNAP_DIR) else []
    total = sum(f[2] for f in snaps)
    out = {
        "subscriber": state,
        "subscriber_detail": why,
        "snapshots": len(snaps),
        "snapshot_bytes": total,
        "snapshot_ceiling_bytes": SNAPSHOT_CEILING_BYTES,
        "snapshot_pct_of_ceiling": round(100.0 * total / SNAPSHOT_CEILING_BYTES, 1)
        if SNAPSHOT_CEILING_BYTES else None,
        "degraded": DEGRADED["downscale_failures"] > 0,
        "downscale_failures": DEGRADED["downscale_failures"],
        "last_downscale_error": DEGRADED["last_downscale_error"],
    }
    if out["degraded"]:
        out["degraded_detail"] = (
            "snapshots are being stored FULL SIZE (~2.3x budget) because downscaling failed %d "
            "time(s) - check Pillow is present in the image" % DEGRADED["downscale_failures"])
    return out


def _is_network_path(path):
    """True if `path` looks like CIFS/UNC. Network filesystems are where append-only guarantees and
    lock semantics quietly stop holding; the store must be local."""
    p = str(path).replace("\\", "/")
    if p.startswith("//"):
        return True
    return any(p.startswith(m) for m in NETWORK_MOUNTS)


# Wire format constraints (measured, not inferred):
#
# 1. No per-event sequence/ID field: `seq` is always NULL on this path.
#    This explains why `event_key` collapses None/'' to avoid inconsistent deduplication.
#    Simultaneous triggers are indistinguishable; rely on Start/Stop semantics and rejects log.
#
# 2. No timestamp in event payload: `started_at` uses receipt time (epoch, no DST ambiguity).
#    This differs from the `mediaFileFind` path which uses local wall-clock strings requiring
#    timezone/DST handling via `nvr_time_to_epoch`. The `source` field distinguishes these paths.
#
# Index is 0-based here; snapshot.cgi/ChannelTitle are 1-based. Thus channel = index + 1,
# and room name comes from CHANNEL_ROOM, never the device title.

EVENT_LINE = "Code=%s;action=%s;index=%d"


def parse_event_line(line):
    """'Code=VideoMotion;action=Start;index=0' -> dict, or None if this is not an event line.

    Deliberately tolerant of the fields we have NOT seen: the capture above is 15 minutes of one
    device, so it bounds what this NVR emits, not what it could ever emit. Unknown `key=value`
    pairs are preserved under `extra` instead of being dropped -- if a sequence field ever does
    appear, it shows up in the store rather than being silently discarded by a regex written on a
    quiet afternoon."""
    line = (line or "").strip()
    if not line.startswith("Code="):
        return None
    parts = [p for p in line.split(";") if p]
    out, extra = {}, {}
    for p in parts:
        k, _, v = p.partition("=")
        k = k.strip()
        if k in ("Code", "action", "index"):
            out[k] = v.strip()
        else:
            extra[k] = v.strip()
    if "Code" not in out:
        return None
    try:
        idx = int(out.get("index", 0))
    except ValueError:
        idx = 0
    channel = idx + 1                      # 0-based on the wire, 1-based everywhere else
    return {"nvr_code": out["Code"],
            "action": out.get("action", ""),
            "channel": channel,
            "camera": CHANNEL_ROOM.get(channel, "channel%d" % channel),
            "extra": extra or None}


# NVR timestamps are local wall-clock strings with no offset.
# Conversion is explicit to avoid reliance on process timezone,
# which differs between host (UTC) and containers (the compose's TZ).
# Tests for this skew are skipped because both environments currently
# share the same TZ, making discrimination impossible here.

def nvr_time_to_epoch(s, tz=None):
    """'YYYY-MM-DD HH:MM:SS' in NVR-local time -> (epoch_seconds, note_or_None).

    DST POLICY, decided here at ingest rather than left to chance (ruling sharpening 2):
      * AMBIGUOUS (the hour that repeats when clocks go back): take fold=0, the FIRST occurrence --
        deterministic, and it keeps event order monotonic across the repeat.
      * NONEXISTENT (the hour skipped when clocks go forward): the wall-clock never happened;
        take fold=0 and FLAG it.
    Either way the row carries a NOTE. A quietly-resolved ambiguity is a silent 1-hour error in a
    log whose entire purpose is answering "when"; a noted one is a fact somebody can check."""
    tz = tz or NVR_TZ
    naive = datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S")
    e0 = naive.replace(tzinfo=tz, fold=0).timestamp()
    e1 = naive.replace(tzinfo=tz, fold=1).timestamp()
    if e0 == e1:
        return e0, None
    if e1 > e0:
        return e0, "DST-ambiguous local time %s; took fold=0 (first occurrence)" % s
    return e0, "DST-nonexistent local time %s (skipped hour); took fold=0" % s


# All human-facing timestamps are in NVR_TZ: TOLKIEN_CAMERAS_TZ, else the container's TZ, else UTC.
# Use `fmt_local()` exclusively; it explicitly passes the zone
# to avoid relying on process TZ environment variables.

def fmt_local(epoch, fmt="%Y-%m-%d %H:%M:%S", tz=None):
    """THE display formatter. Central time. Display only -- never store this."""
    return datetime.fromtimestamp(epoch, tz or NVR_TZ).strftime(fmt)


def epoch_to_local(epoch, tz=None):
    """Back-compat alias for `fmt_local`. One derivation, two names."""
    return fmt_local(epoch, tz=tz)


def day_key(epoch, tz=None):
    """Return the date key for the file an event belongs to.

    Uses the lab's local time (NVR_TZ). Do not change to UTC:
    doing so splits evening events across midnight, causing queries
    for 'last night' to miss early hours."""
    return fmt_local(epoch, "%Y-%m-%d", tz=tz)


# ── STORE (append-only JSONL) ────────────────────────────────────────────────────────────────────

def event_path(epoch=None, day=None):
    return os.path.join(EVENT_DIR, "%s.jsonl" % (day or day_key(epoch if epoch else time.time())))


def reject_path(epoch=None, day=None):
    return os.path.join(REJECT_DIR, "%s.jsonl" % (day or day_key(epoch if epoch else time.time())))


def init_store():
    """Create the store directories. Idempotent.

    Refuses a network path outright rather than trusting a comment to be read."""
    for d in (EVENT_DIR, REJECT_DIR, SNAP_DIR, BACKUP_DIR):
        if _is_network_path(d):
            raise RuntimeError(
                "refusing to place the event store on a network path (%s). It must live on a local "
                "bind: the container layer is destroyed by every --force-recreate, and network "
                "filesystems are where append semantics quietly stop holding." % d)
        os.makedirs(d, exist_ok=True)
    return HOME


def event_key(rec):
    """Return the natural identity tuple for an observation.

    Includes `action` to distinguish Start/Stop events occurring in the same second.
    Normalizes `seq` to '' if None to ensure consistent deduplication regardless of
    data source (NVR may omit sequence numbers)."""
    return (rec.get("camera"), float(rec.get("started_at") or 0),
            rec.get("nvr_code"), rec.get("action") or "Start", rec.get("seq") or "")


class EventWriter(object):
    """Subscriber singleton for deduplicating event records.

    Uses an in-memory set for O(1) session deduplication. This prevents reconnect replays
    from inflating counts or corrupting Stage 1 grading when the attach socket re-sends data
    after a drop.

    Keys are seeded from disk lazily, per day, to survive process restarts. Seeding only
    today's file at construction fails for backfills, mid-night bursts, or late stops,
    as records belonging to other days would appear new. The record's day-file is loaded
    on first touch, ensuring correctness with a cost of one file read per day."""

    def __init__(self):
        init_store()
        self.seen = set()
        self._seeded = set()          # which day-files have been read into `seen`
        self.counts = {"appended": 0, "rejected": 0}

    def _seed_day(self, day):
        """Load one day-file's keys. Idempotent, and each file is read at most once per session."""
        if day in self._seeded:
            return
        self._seeded.add(day)
        for rec in read_day(day):
            self.seen.add(event_key(rec))

    def append(self, camera, channel, started_at, nvr_code, source,
               seq=None, ended_at=None, colour=None, snapshot=None, note=None, action="Start"):
        """Append one observation. Returns (written: bool, reason: str|None).

        `action` must be part of the dedupe key because NVR Start/Stop events lack
        independent timestamps. Without it, a Stop arriving in the same second as its
        Start would be incorrectly discarded as a duplicate."""
        rec = {"camera": camera, "channel": channel, "started_at": float(started_at),
               "ended_at": ended_at, "nvr_code": nvr_code, "seq": seq, "source": source,
               "action": action, "colour": colour, "snapshot": snapshot, "note": note,
               "created_at": time.time()}
        self._seed_day(day_key(rec["started_at"]))
        key = event_key(rec)
        if key in self.seen:
            self.counts["rejected"] += 1
            self._write(reject_path(rec["started_at"]),
                        dict(rec, reason="duplicate-key", seen_at=time.time()))
            return False, "duplicate-key"
        self.seen.add(key)
        self._write(event_path(rec["started_at"]), rec)
        self.counts["appended"] += 1
        return True, None

    @staticmethod
    def _write(path, rec):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Append mode, one object per line, flushed. Line-oriented append is the property the whole
        # format rests on: a torn write costs one line, never the file.
        with io.open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()


# ── READ SIDE — read-only by construction ────────────────────────────────────────────────────────

def _read_jsonl(path):
    """Yield records from a JSONL file.

    Opened read-only ('r').
    Malformed lines are skipped and counted (never fatal, never silent).
    This ensures a torn tail line doesn't render the whole file unreadable."""
    if not os.path.exists(path):
        return
    with io.open(path, "r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                yield {"_malformed": True, "_path": path, "_line": n}


def read_day(day):
    """All well-formed records for one local day, in file order."""
    return [r for r in _read_jsonl(event_path(day=day)) if not r.get("_malformed")]


def read_events(start_epoch, end_epoch, camera=None, dedupe=True):
    """Events in [start, end), newest-last, across however many day-files that spans.

    ON-READ DEDUPE (sharpening 1) is deliberately belt-and-braces with the writer's in-memory set:
    the set covers a live session, this covers everything else -- two subscribers overlapping during
    a deploy, a restored backup replayed over a live file, a manual re-ingest. The store is
    append-only, so the reader is the only place a duplicate can still be neutralised."""
    out, seen, dupes = [], set(), 0
    day = datetime.fromtimestamp(start_epoch, NVR_TZ).date()
    last = datetime.fromtimestamp(end_epoch, NVR_TZ).date()
    while day <= last:
        for rec in read_day(day.strftime("%Y-%m-%d")):
            ts = float(rec.get("started_at") or 0)
            if not (start_epoch <= ts < end_epoch):
                continue
            if camera and rec.get("camera") != camera:
                continue
            if dedupe:
                k = event_key(rec)
                if k in seen:
                    dupes += 1
                    continue
                seen.add(k)
            out.append(rec)
        day += timedelta(days=1)
    out.sort(key=lambda r: float(r.get("started_at") or 0))
    return out, {"duplicates_dropped": dupes}


def read_rejects(day):
    return [r for r in _read_jsonl(reject_path(day=day)) if not r.get("_malformed")]


def malformed_lines(day):
    """Torn/garbled lines for a day. Exposed so 'the file has a bad line' is an ANSWERABLE question
    rather than a silent omission from every count built on it."""
    return [r for r in _read_jsonl(event_path(day=day)) if r.get("_malformed")]


# ── RETENTION ────────────────────────────────────────────────────────────────────────────────────

def _dir_files(path):
    out = []
    for root, _dirs, names in os.walk(path):
        for n in names:
            fp = os.path.join(root, n)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            out.append((fp, st.st_mtime, st.st_size))
    return out


def prune(now=None):
    """Enforce retention bounds by age and total size.

    Returns a report of what was dropped. Both bounds are necessary:
      * Age catches normal churn per policy.
      * Size catches regime changes (e.g., new camera, sensitivity spikes)
        that age-based pruning misses, preventing disk exhaustion."""
    now = now or time.time()
    report = {"event_days": [], "reject_days": [], "snapshots_by_age": 0,
              "snapshots_by_ceiling": 0, "bytes_freed": 0, "ceiling_hit": False}

    # -- metadata: delete whole day-files past the tier.
    cutoff_day = fmt_local(now - METADATA_DAYS * 86400, "%Y-%m-%d")
    for d, bucket in ((EVENT_DIR, "event_days"), (REJECT_DIR, "reject_days")):
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".jsonl"):
                continue
            if name[:-len(".jsonl")] < cutoff_day:        # ISO dates sort lexicographically
                fp = os.path.join(d, name)
                try:
                    report["bytes_freed"] += os.path.getsize(fp)
                    os.remove(fp)
                    report[bucket].append(name)
                except OSError:
                    pass

    # -- snapshots by AGE
    cutoff = now - SNAPSHOT_DAYS * 86400
    for fp, mtime, size in (_dir_files(SNAP_DIR) if os.path.isdir(SNAP_DIR) else []):
        if mtime < cutoff:
            try:
                os.remove(fp)
                report["snapshots_by_age"] += 1
                report["bytes_freed"] += size
            except OSError:
                pass

    # -- snapshots by CEILING, oldest first, regardless of age
    files = _dir_files(SNAP_DIR) if os.path.isdir(SNAP_DIR) else []
    total = sum(f[2] for f in files)
    if total > SNAPSHOT_CEILING_BYTES:
        report["ceiling_hit"] = True
        for fp, _mtime, size in sorted(files, key=lambda f: f[1]):
            if total <= SNAPSHOT_CEILING_BYTES:
                break
            try:
                os.remove(fp)
                total -= size
                report["snapshots_by_ceiling"] += 1
                report["bytes_freed"] += size
            except OSError:
                pass
    return report


def backup(now=None):
    """Copy yesterday's closed day-file and today's current one to a second local path; keep ~14.

    Yesterday's file is closed, so its copy is exact. Today's file is live,
    so the copy is a point-in-time prefix. This is safe because JSONL is
    line-oriented: a truncated tail costs at most the last line, not the
    whole file."""
    now = now or time.time()
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = fmt_local(now, "%Y%m%d-%H%M%S")
    copied = []
    for offset in (1, 0):                       # yesterday (closed) first, then today (live)
        day = day_key(now - offset * 86400)
        src = event_path(day=day)
        if not os.path.exists(src):
            continue
        dest = os.path.join(BACKUP_DIR, "%s.%s.jsonl" % (day, stamp))
        shutil.copy2(src, dest)
        copied.append(os.path.basename(dest))

    existing = sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith(".jsonl"))
    dropped = []
    if len(existing) > BACKUPS_KEPT:
        for f in existing[:-BACKUPS_KEPT]:
            try:
                os.remove(os.path.join(BACKUP_DIR, f))
                dropped.append(f)
            except OSError:
                pass
    kept = sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith(".jsonl"))
    return {"copied": copied, "kept": len(kept), "dropped": dropped}


# ── HEARTBEAT ────────────────────────────────────────────────────────────────────────────────────

def write_beat(state, period, detail="", extra=None):
    """Write liveness state. Call from the worker loop itself, never a separate timer.

    This ensures staleness reflects actual work progress, not just process uptime.
    The `period` defines the expected cadence so readers can calculate staleness dynamically."""
    beat = {"ts": time.time(), "state": state, "period": period, "detail": detail}
    if extra:
        beat.update(extra)
    os.makedirs(os.path.dirname(BEAT_PATH), exist_ok=True)
    tmp = BEAT_PATH + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(beat, fh)
    os.replace(tmp, BEAT_PATH)      # atomic: a reader never sees a half-written beat
    return beat


def read_beat():
    try:
        with io.open(BEAT_PATH, "r", encoding="utf-8") as fh:
            b = json.load(fh)
        b["age"] = max(0.0, time.time() - float(b.get("ts") or 0))
        return b
    except Exception:
        return None


def pair_events(records):
    """Pair raw Start/Stop observations into events with a duration.

        The NVR emits Start and Stop as separate append-only lines. Since the log
    cannot be updated in place, pairing happens here on read to preserve the raw
    observation history.

        A Start with no matching Stop leaves `ended_at` as None (in-progress or
    missed Stop). Treat None as indeterminate duration; never render it as 0."""
    out, open_by_cam = [], {}
    for rec in sorted(records, key=lambda r: float(r.get("started_at") or 0)):
        action = (rec.get("action") or "Start").lower()
        cam = rec.get("camera")
        if action == "stop":
            started = open_by_cam.pop(cam, None)
            if started is not None:
                started["ended_at"] = float(rec.get("started_at") or 0)
            # A Stop with no matching Start is KEPT as its own row rather than dropped: it is
            # evidence the subscriber came up mid-event, which is a fact worth being able to see.
            else:
                out.append(dict(rec, ended_at=float(rec.get("started_at") or 0),
                                note=(rec.get("note") or "") + " [stop with no observed start]"))
            continue
        ev = dict(rec)
        ev.setdefault("ended_at", None)
        open_by_cam[cam] = ev
        out.append(ev)
    return out


def duration(ev):
    """Seconds, or None when the Stop was never observed. Never 0 for an unknown."""
    if ev.get("ended_at") is None:
        return None
    return max(0.0, float(ev["ended_at"]) - float(ev.get("started_at") or 0))


# ── SNAPSHOT + COLOUR ────────────────────────────────────────────────────────────────────────────

def snapshot_capture(channel, epoch, opener=None, fetch=None):
    """Grab a still for one channel, downscale, store.

    Returns (relpath, colour, note).
        `colour` is 1 / 0 / None. None means UNKNOWN (not IR). This flag is mandatory:
        IR only supports Lucy, Binks, Zeke; colour supports all five. A failed measurement
        defaulting to 0 would silently claim IR and corrupt identifications.

        Downscaling avoids filling the disk with full 2688x1520 frames (~51 GB/yr).
        960px is sufficient for cat identification."""
    note = None
    try:
        raw = (fetch or _fetch_snapshot)(channel, opener)
    except Exception as e:
        return None, None, "snapshot fetch failed: %s: %s" % (type(e).__name__, str(e)[:120])

    day = day_key(epoch)
    rel = os.path.join(day, "ch%d-%s.jpg" % (channel, fmt_local(epoch, "%H%M%S")))
    dest = os.path.join(SNAP_DIR, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    colour = None
    try:
        from PIL import Image                      # imported late: the store must work without it
        im = Image.open(io.BytesIO(raw))
        im.load()
        colour = _measure_colour(im)
        im.thumbnail((SNAPSHOT_EDGE, SNAPSHOT_EDGE), Image.LANCZOS)
        im.convert("RGB").save(dest, "JPEG", quality=SNAPSHOT_QUALITY, optimize=True)
    except Exception as e:
        # Store raw bytes on downscale failure to prevent missing snapshots.
        # Fallback is ~2x larger; `DEGRADED` metrics and `store_status()`
        # surface this "degraded" state so the cost is visible, not silent.
        DEGRADED["downscale_failures"] += 1
        DEGRADED["last_downscale_error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
        note = "downscale failed (%s: %s); stored full size" % (type(e).__name__, str(e)[:80])
        with io.open(dest, "wb") as fh:
            fh.write(raw)
    return rel.replace("\\", "/"), colour, note


def _fetch_snapshot(channel, opener=None):
    import urllib.request
    url = "http://%s/cgi-bin/snapshot.cgi?channel=%d" % (NVR_HOST, channel)
    op = opener or urllib.request.build_opener()
    with op.open(url, timeout=20) as r:
        return r.read()


SAT_CUTOFF = float(os.environ.get("TOLKIEN_CAMERAS_SAT_CUTOFF", 8.0))


def _measure_colour(im, sample=64, cutoff=None):
    """1 = colour, 0 = IR, None = could not tell.

    Measured on a downsampled copy: the answer does not need four megapixels, and a cheap
    measurement is one that actually gets taken on every event."""
    try:
        from PIL import ImageStat
        small = im.convert("RGB").copy()
        small.thumbnail((sample, sample))
        if not small.size[0] or not small.size[1]:
            return None
        mean_sat = ImageStat.Stat(small.convert("HSV")).mean[1]
        return 1 if mean_sat > (SAT_CUTOFF if cutoff is None else cutoff) else 0
    except Exception:
        return None      # UNKNOWN. Never guess IR.


# ── SUBSCRIBER ───────────────────────────────────────────────────────────────────────────────────

def subscribe(stop_flag=None, opener=None, url=None, max_events=None, writer=None,
              capture=True, on_event=None, on_tick=None):
    """Subscribe to `eventManager.cgi?action=attach` and append observations. Runs forever.

    Mechanics:
      * Heartbeat is written inside this loop; a live pusher is not a live worker.
      * Socket uses read timeouts with bounded backoff reconnects to detect half-open connections.
      * `max_events` allows finite runs (e.g., for testing) by exiting after the limit.
      * `on_tick` is called within the read loop alongside heartbeat updates, ensuring maintenance
        tasks (retention/backup) execute periodically rather than relying on function return,
        which never occurs in this infinite loop.
      * Clean socket closures (e.g., NVR-initiated) are handled without escalating backoff;
        only actual failures trigger exponential backoff."""
    import urllib.request
    url = url or ("http://%s/cgi-bin/eventManager.cgi?action=attach&codes=%%5BAll%%5D&heartbeat=5"
                  % NVR_HOST)
    w = writer or EventWriter()
    op = opener or urllib.request.build_opener()
    backoff, seen = 5.0, 0
    counts = {"events": 0, "heartbeats": 0, "reconnects": 0, "clean_ends": 0, "errors": 0}

    while not (stop_flag and stop_flag()):
        stream = None
        try:
            write_beat("connecting", BEAT_PERIOD, "opening attach stream", extra=counts)
            stream = op.open(url, timeout=READ_TIMEOUT)
            write_beat("watching", BEAT_PERIOD, "attach stream open", extra=counts)
            backoff = 5.0                      # a good connection resets the failure ladder
            last_beat = time.time()
            while not (stop_flag and stop_flag()):
                raw = stream.readline()
                if not raw:
                    counts["clean_ends"] += 1
                    break                       # ordinary close, NOT a failure
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    pass
                elif "heartbeat" in line.lower():
                    counts["heartbeats"] += 1
                else:
                    parsed = parse_event_line(line)
                    if parsed:
                        counts["events"] += 1
                        seen += 1
                        # `op` carries the digest auth `snapshot.cgi` requires; a bare opener inside
                        # `_ingest` fails with HTTP 401.
                        _ingest(w, parsed, capture=capture, on_event=on_event, opener=op)
                # The beat is written from inside the read loop, so if this loop stops for ANY
                # reason -- wedge, exception, deadlock -- the beat goes stale and says so.
                if time.time() - last_beat >= BEAT_PERIOD:
                    write_beat("watching", BEAT_PERIOD,
                               "%d events, %d heartbeats" % (counts["events"], counts["heartbeats"]),
                               extra=counts)
                    last_beat = time.time()
                    if on_tick:
                        # on_tick failures must not crash the subscriber.
                        try:
                            on_tick(counts)
                        except Exception as e:                       # noqa: BLE001
                            write_beat("watching", BEAT_PERIOD,
                                       "maintenance error: %s: %s" % (type(e).__name__,
                                                                      str(e)[:100]),
                                       extra=counts)
                if max_events and seen >= max_events:
                    write_beat("watching", BEAT_PERIOD, "max_events reached", extra=counts)
                    return counts
        except Exception as e:
            counts["errors"] += 1
            write_beat("reconnecting", BEAT_PERIOD,
                       "%s: %s" % (type(e).__name__, str(e)[:120]), extra=counts)
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
        if stop_flag and stop_flag():
            break
        counts["reconnects"] += 1
        time.sleep(backoff if counts["errors"] else 1.0)
        if counts["errors"]:
            backoff = min(backoff * 2, 300.0)
    write_beat("stopped", BEAT_PERIOD, "stop requested", extra=counts)
    return counts


def _ingest(writer, parsed, capture=True, on_event=None, opener=None):
    """Ingest parsed camera events. Maps one line to one observation (plus snapshot on Start).

    NOTE: `started_at` uses the local receipt clock (`now`), as attach events lack timestamps.
    This differs from `mediaFileFind` rows, so the `source` field is required to distinguish
    time origins and ensure comparable data."""
    now = time.time()
    is_start = (parsed.get("action") or "").lower() == "start"
    snap = colour = note = None
    if is_start and capture:
        snap, colour, note = snapshot_capture(parsed["channel"], now, opener=opener)
    written, reason = writer.append(
        parsed["camera"], parsed["channel"], now, parsed["nvr_code"], "eventManager",
        action=parsed.get("action") or "Start", colour=colour, snapshot=snap, note=note,
        seq=(parsed.get("extra") or {}).get("seq"))
    if on_event:
        on_event(parsed, written, reason)
    return written, reason


# Scheduling is internal: the lab scheduler treats the Fellowship as a single target.
# Use existing `fellow_health()` for node selection (returns `alive: False` for unreachable,
# not omission). Do not introduce a second liveness mechanism to avoid drift.

BURST_STORE = os.environ.get("TOLKIEN_CAMERAS_ANALYSES", os.path.join(HOME, "analyses"))


def analysis_path(epoch=None, day=None):
    return os.path.join(BURST_STORE, "%s.jsonl" % (day or day_key(epoch or time.time())))


def pick_burst_node(health, prefer=None):
    """Choose a fellow for a burst.


    Requires both a live agent and a ready detector; an alive agent alone does not
    guarantee the resident model is loaded."""
    live = [(h, r) for h, r in sorted((health or {}).items())
            if r.get("alive") and (r.get("detector") or {}).get("ready")]
    if not live:
        asked = len(health or {})
        return None, ("no fellow has BOTH a live agent and a ready detector (asked %d)" % asked)
    for want in (prefer or []):
        for h, _r in live:
            if h == want:
                return h, "preferred node %s is healthy" % h
    return live[0][0], "first healthy node with a ready detector (%d candidates)" % len(live)


def burst_analyse(caller, host, channel, start_local, end_local, user, pw,
                  frames=150, stride=1, scale="704x480", nvr=None, timeout=300, codec=None,
                  substream=False):
    """Ask one fellow to burst-analyse a recorded window. Returns the fellow's result dict.

    `caller` is injected (normally `tablet_foundry.fellow_call`) for testing without a tablet.

    Returns the result dict AS-IS on failure, not None, to distinguish error conditions."""
    req = {"task": "burst", "channel": int(channel), "start": start_local, "end": end_local,
           "user": user, "pw": pw, "frames": int(frames), "stride": int(stride),
           "scale": scale, "timeout": int(timeout)}
    nvr = nvr or NVR_HOST             # the hub's setting travels with the order: a tablet holds no NVR address
    if nvr:
        req["nvr"] = nvr
    if codec:
        req["codec"] = codec
    if substream:
        req["substream"] = substream
    ok, obj = caller(host, "/work", req, timeout=timeout + 30)
    if not isinstance(obj, dict):
        return {"ok": False, "error": "fellow returned a non-dict: %r" % (obj,), "node": host}

    # obj['ok'] is transport status; inner['ok'] is task result.
    # A successful transport can contain a failed task (e.g., zero frames).
    # Inner ok takes precedence for the final verdict.
    inner = obj.get("result")
    if isinstance(inner, dict):
        out = dict(inner)
        out["transport_ok"] = bool(ok and obj.get("ok"))
        out["secs"] = obj.get("secs")
        out.setdefault("ok", False)
    else:
        # No `result` at all: the agent rejected the task (unknown task, busy, bad token). That is
        # a transport-level failure and the envelope IS the whole story.
        out = dict(obj)
        out["ok"] = bool(ok and obj.get("ok"))
    out["node"] = host
    return out


def record_analysis(result, event=None):
    """Append one burst result to the analyses stream, including failures.

    Failures and "found nothing" results must be stored separately from missing runs
    to distinguish attempted analyses from skipped ones."""
    rec = {
        "ts": time.time(),
        "node": result.get("node"),
        "ok": bool(result.get("ok")),
        "channel": result.get("channel"),
        "start": result.get("start"),
        "end": result.get("end"),
        "decoded": result.get("decoded"),
        "analysed": result.get("analysed"),
        "frames_with_detections": result.get("frames_with_detections"),
        "decode_s": result.get("decode_s"),
        "total_s": result.get("total_s"),
        "error": result.get("error"),
        "event_started_at": (event or {}).get("started_at"),
        "detections": result.get("detections") or [],
    }
    path = analysis_path(rec["ts"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
    return rec


def detections_at_floor(result, floor, labels=("cat", "dog")):
    """Return frames where the best prediction for `labels` exceeds `floor`.

        Returns:
            tuple: (count of matching frames, highest confidence score found).

        Warning:
            The `floor` threshold must be explicitly passed and applied consistently
    to both positive detections and negative controls. Using a constant or omitting
    the floor risks tuning the threshold until positives pass, allowing false
    positives (which cluster near 0.57) to slip through.

        Note:
            `labels` defaults to ``('cat', 'dog')`` because subjects often flicker
    between these classes; filtering for only one class causes significant
    detection loss."""
    want = {str(x).lower() for x in labels}
    hits, best = 0, 0.0
    for fr in (result.get("detections") or []):
        top = 0.0
        for p in fr.get("predictions") or []:
            if str(p.get("label", "")).lower() in want:
                top = max(top, float(p.get("confidence") or 0.0))
        best = max(best, top)
        if top >= floor:
            hits += 1
    return hits, best


def subscriber_verdict(beat=None, now=None):
    """LIVE / SILENT / DOWN / UNKNOWN -- and UNKNOWN is a real answer, not a fallback to the scary one.

    Derived from the cadence the reporter declared, not a constant here. "I could not tell" must
    never render as a definite state in either direction."""
    beat = beat if beat is not None else read_beat()
    if not beat or not beat.get("ts"):
        return "UNKNOWN", "no heartbeat file - cannot tell running from never-started"
    period = float(beat.get("period") or 60)
    age = float(beat.get("age", (now or time.time()) - float(beat["ts"])))
    if age <= max(90.0, period * 2):
        return "LIVE", "beat %.0fs old (cadence %.0fs)" % (age, period)
    if age <= max(90.0, period * 3):
        return "SILENT", "beat %.0fs old, past 2x cadence %.0fs - not yet DOWN" % (age, period)
    return "DOWN", "beat %.0fs old, past 3x cadence %.0fs" % (age, period)
