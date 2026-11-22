"""Orchestrator for consent-gated identity render jobs.

Workflow:
1. User requests render via chat; `tools.py` checks consent (`person_record.check_use`) and enqueues.
2. Worker (`p20_render_worker.py`) polls for jobs when GPU is free, renders, and POSTs PNG.
3. `app.py` saves image to user's library via `comfy_client.save_image`.

Critical constraints:
- Storage: File-per-job JSON in `QUEUE_DIR` (host-mounted) for persistence across restarts.
- Dispatch: Energy-gated server-side (`energy.is_runnable_now`). Fail-closed during peak pricing.
  Admin jobs set `rush=True` to bypass gate.
- Consent: Checked at enqueue time and recorded in job payload for audit trail. Fail-closed.
- Limits: One pending job per user max (prevents queue pile-up/misuse)."""
import copy
import datetime
import json
import os
import threading
import time
import uuid

import config

QUEUE_DIR = os.getenv("P20_QUEUE_DIR", os.path.join(config.STATE_DIR, "p20-render-queue"))

TERMINAL = ("done", "failed", "cancelled")


def _path(job_id):
    return os.path.join(QUEUE_DIR, job_id + ".json")


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


_WRITES = [0]     # how many records this process has written: an ask that waited for another's scan reuses it only if this did not move


def _wrote():
    _WRITES[0] += 1


def _save(job):
    os.makedirs(QUEUE_DIR, exist_ok=True)
    with open(_path(job["id"]), "w", encoding="utf-8") as f:
        json.dump(job, f, indent=1)
    _wrote()


# Called with every NEW order, on the thread that created it (#37 step 2: an order a chat turn starts appears in that
# conversation). The store stays ignorant of chat: app registers the hook. A hook that raises never fails the order.
ENQUEUE_HOOKS = []


def _announce(job):
    for hook in list(ENQUEUE_HOOKS):
        try:
            hook(job)
        except Exception:
            pass


def normalize_render_settings(settings):
    """Explicit comparison controls on the existing render order, without new execution machinery."""
    settings = {k: v for k, v in (settings or {}).items() if v is not None}
    if set(settings) - {'seed', 'width', 'height'}:
        raise ValueError('Unknown image comparison setting')
    if 'seed' in settings and (type(settings['seed']) is not int or not 0 <= settings['seed'] < 2**32):
        raise ValueError('Image seed must be an integer between 0 and 4294967295')
    if 'width' in settings or 'height' in settings:
        if not all(type(settings.get(k)) is int and 256 <= settings[k] <= 1536
                   and settings[k] % 16 == 0 for k in ('width', 'height')):
            raise ValueError('Set both image dimensions, in multiples of 16 between 256 and 1536')
        if settings['width'] * settings['height'] > 2 * 1024 * 1024:
            raise ValueError('The comparison canvas must be at most two megapixels')
    return settings


def enqueue(user, person_key, prompt, lora_path, trigger, *, use, consent_reason, rush=False, renderer=None,
            reference_id=None, reference_selection="manual", render_settings=None):
    """Create a pending job. Caller (render_person) has ALREADY passed check_use — we record the
    verdict for the audit trail rather than trusting it silently."""
    import render_options
    renderer_key, rendering = render_options.choose(user, renderer)
    settings = normalize_render_settings(render_settings)
    reference = None
    if rendering.get("reference_edit"):
        import krea_reference
        reference = krea_reference.resolve(user, reference_id)
        if reference_selection not in ("manual", "automatic"):
            raise ValueError('Unknown reference selection mode')
        reference['selection'] = reference_selection
        if lora_path or person_key != reference['person'] or use != reference['use']:
            raise ValueError('Reference editing uses the selected approved image without a trained identity adapter.')
    elif reference_id:
        raise ValueError('Reference images require Krea 2 Reference.')
    job = {
        "id": uuid.uuid4().hex[:12],
        "user": user,
        "person": person_key,
        "prompt": prompt,
        "lora_path": lora_path,
        "trigger": trigger,
        "use": use,
        "consent_reason": consent_reason,
        "requires": [rendering.get("requires") or "render.flux2"],
        "renderer": renderer_key,
        "render_model": rendering["model"],
        "host": rendering["host"],
        "rush": bool(rush),
        "status": "pending",
        "initiator": "hai",
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if rendering["model"] == "krea2":
        # Explicit end-to-end render strength; FLUX keeps its existing default.
        job["strength"] = 1.4 if (config.OWNER_USER and user == config.OWNER_USER) else 1.0
        if rendering.get("second_adapter"):
            job["second_adapter"] = rendering["second_adapter"]
            job["second_adapter_strength"] = rendering["second_adapter_strength"]
    if reference:
        job["reference"] = reference
        job["edit_adapter"] = "krea2_identity_edit_v2.safetensors"
        job["edit_strength"] = 1.0
        job["reference_boost"] = 2.0
        job["encoder_device"] = "default"
    job.update(settings)
    _save(job)
    _announce(job)
    return job


def enqueue_quick_preview(run, step, arch, lora_path, prompt_name, prompt,
                          *, strength=0.6, requires=None, caveat="", rush=True):
    """Enqueue an approximate training preview as a standard job in the shared render queue.

    This is a single-queue solution to avoid duplicate pipeline implementations. The preview
    is generated by the factory itself, so no user consent fields are required; the audit trail
    marks the initiator as 'foundry'. The output is an approximation of concept binding and
    pose steering on a smaller base model, not a final quality check for shipping.

    Args:
        rush (bool): Defaults to True. Previews expire quickly relative to training steps,
            so they should bypass energy-saving wait windows used for standard renders."""
    job = {
        "id": uuid.uuid4().hex[:12],
        "kind": "quick_preview",
        "run": run,
        "step": int(step),
        "arch": (arch or "").strip().lower(),
        "lora_path": lora_path,
        "prompt_name": prompt_name,
        "prompt": prompt,
        "strength": float(strength),
        "requires": list(requires or ["render.%s" % (arch or "").strip().lower()]),
        "approximate": True,
        "tag": "APPROX",
        "caveat": caveat or ("APPROXIMATE PREVIEW - rendered on a %s base, not the model being "
                            "trained. NOT valid evidence for electing a checkpoint." % arch),
        "rush": bool(rush),
        "status": "pending",
        "initiator": "foundry",
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    _save(job)
    _announce(job)
    return job


def enqueue_factory_order(spec, requires=None, rush=False, after_ids=None, step="", host="",
                          route=None, status="pending", why=""):
    """Enqueue a factory order for fleet-wide distribution.

    Unlike render jobs targeted to specific machines, this submits a complete spec to the shared queue,
    allowing any capable factory to pull it. It reuses the existing job store and downstream logic
    (capability assessment, priority, retirement), ensuring byte-identical handling as if the file were
    manually placed in a local queue.
    """
    name = (spec or {}).get("name") or "order"
    job = {
        "id": uuid.uuid4().hex[:12],
        "kind": "factory_order",
        "order_name": name,
        "spec": spec,
        "requires": list(requires or (spec or {}).get("requires") or []),
        "rush": bool(rush),
        # "after_ids": Job IDs that must reach 'done' before this one can be offered.
        # Empty list means ready immediately.
        "after_ids": [str(a) for a in (after_ids or [])],
        "step": str(step or ""),
        # Persisted host target. If empty (default), scheduling falls back to
        # capability-only matching (advisory placement policies are ignored).
        # Non-empty enforces specific host assignment, preventing split-brain
        # where scheduler and executor disagree on eligibility.
        "host": str(host or "").strip().lower(),
        # "held": every capable factory is unavailable (lost card, Plex using the failover box). No factory
        # pulls it (dispatchable() offers only "pending"); the desk re-places it when one is available.
        "status": status if status in ("pending", "held") else "pending",
        # what the desk needs to place it again: the pre-placement template, the kind and routing pins,
        # and the dealt slice. Without it a hand-back could only be refused.
        "route": route or {},
        "initiator": "fleet-desk",
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if job["status"] == "held":
        job["held_why"] = str(why or "")
    _save(job)
    _announce(job)
    return job


def held():
    """Factory orders the desk is holding because no capable factory is available."""
    return [j for j in all_jobs() if j.get("kind") == "factory_order" and j.get("status") == "held"]


def claim(job_id, host):
    """Mark an order as taken by `host`.

    Returns the job on success, None if the order was already claimed or not pending.
    Callers must treat None as 'not mine' and skip processing, never as an error."""
    j = _load(_path(job_id))
    if not j or j.get("status") != "pending":
        return None
    j["status"] = "claimed"
    j["claimed_by"] = host
    j["claimed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _save(j)
    return j


def buildable_by(jobs, caps):
    caps = set(caps or [])
    out = []
    for j in jobs:
        need = j.get("requires") or []
        # Pre-capability HAI renders are Flux.2 jobs too. A Klein-only worker must
        # not take them merely because their old record has no requires field.
        if (j.get("kind") or "") in ("", "render") and "user" in j and "prompt" in j:
            need = need or ["render.flux2"]
        if all(r in caps for r in need):
            out.append(j)
    return out


def placed_for(jobs, host):
    """Return jobs assigned to `host` or unassigned (available to any host).

    A job with a `host` field is exclusive to that host. A job without one
    is unplaced and available to all hosts. This placement check is distinct
    from capability checks (`buildable_by`); both must pass for execution."""
    h = (host or "").strip().lower()
    return [j for j in jobs if not (j.get("host") or "") or (j.get("host") or "").lower() == h]


_RAW = {}     # path -> ((mtime_ns, size), bytes): the record as last read, valid while its fingerprint holds
_PARSED = {}  # path -> ((mtime_ns, size), record): the record as last PARSED. SHARED objects - read, never mutate


def _shared_records(directory):
    """Shared cache of parsed records, keyed by directory.

    Records are read and parsed ONCE per file change (fingerprint: mtime_ns + size). The returned dicts are SHARED with the internal cache; callers must read them and hand out COPIES, never mutate the originals.

    Concurrent asks for the same directory share a single scan to avoid redundant I/O. If a record fails to parse (e.g. caught mid-write), it is not cached, ensuring the next request re-reads it."""
    done_before = _SCANNED.get(directory, (0, None, None))[0]
    with _SCAN_LOCK:
        n, writes_at_start, recs = _SCANNED.get(directory, (0, None, None))
        if n != done_before and writes_at_start == _WRITES[0]:
            return recs                               # another ask's scan finished while this one waited
        writes_at_start = _WRITES[0]
        recs = _scan_records(directory)
        _SCANNED[directory] = (n + 1, writes_at_start, recs)
        return recs


_SCAN_LOCK = threading.Lock()
_SCANNED = {}     # directory -> (scans completed, _WRITES when the last one began, its records)


def _scan_records(directory):
    if not os.path.isdir(directory):
        return []
    if not _SNAPSHOT["loaded"]:
        _load_snapshot()                # the last process's parsed records: this scan re-reads only what changed since
    seen, out = set(), []
    for e in os.scandir(directory):
        if not e.name.endswith(".json"):
            continue
        try:
            st = e.stat()
        except OSError:
            continue
        fp = (st.st_mtime_ns, st.st_size)
        seen.add(e.path)
        hit = _PARSED.get(e.path)
        if hit is None or hit[0] != fp:
            try:
                with open(e.path, "rb") as f:
                    rec = json.loads(f.read().decode("utf-8"))
            except (OSError, ValueError):
                _PARSED.pop(e.path, None)
                continue
            hit = (fp, rec)
            _PARSED[e.path] = hit
            _SNAPSHOT["reads"] += 1
        if hit[1]:
            out.append(hit[1])
    for p in [p for p in _PARSED if os.path.dirname(p) == os.path.dirname(os.path.join(directory, "x")) and p not in seen]:
        del _PARSED[p]
    _snapshot_if_due()
    return out


def all_jobs():
    """Every record, parsed fresh on each call. A record's BYTES are re-read only when its fingerprint (mtime_ns, size) changed.
    Parsing per call keeps callers free to mutate what they get."""
    if not os.path.isdir(QUEUE_DIR):
        return []
    if not _SNAPSHOT["loaded"]:
        _load_snapshot()
    seen, jobs = set(), []
    for e in os.scandir(QUEUE_DIR):
        if not e.name.endswith(".json"):
            continue
        try:
            st = e.stat()
        except OSError:
            continue
        fp = (st.st_mtime_ns, st.st_size)
        seen.add(e.path)
        hit = _RAW.get(e.path)
        if hit is None or hit[0] != fp:
            parsed = _PARSED.get(e.path)
            if parsed is not None and parsed[0] == fp:
                # already read by a factory's ask (or loaded from the saved cache): its bytes, without opening the file
                # again over the share - the first page poll after a restart read every record a second time
                hit = (fp, json.dumps(parsed[1]).encode("utf-8"))
            else:
                try:
                    with open(e.path, "rb") as f:
                        hit = (fp, f.read())
                except OSError:
                    _RAW.pop(e.path, None)
                    continue
            _RAW[e.path] = hit
        try:
            j = json.loads(hit[1].decode("utf-8"))
        except ValueError:
            continue
        if j:
            jobs.append(j)
    for p in [p for p in _RAW if p not in seen]:
        del _RAW[p]
    return sorted(jobs, key=lambda j: j.get("created", ""))


def pending(user=None):
    return [j for j in all_jobs() if j.get("status") == "pending"
            and (user is None or j.get("user") == user)]


def get(job_id):
    """The record by id: the queue's, else the archived one (archive_old), so a caller that asks by id finds an old
    order where it now lives."""
    return _load(_path(job_id)) or _load_archived(job_id)


# ARCHIVE: Finished orders/controls older than ARCHIVE_AFTER_DAYS move to <queue>/archive/<YYYY-MM>/ (never deleted).
# Records referenced by waiting orders stay; `get()` resolves archived ids.
# CACHE: Parsed records persist to <queue>/cache/parsed-records.json. The next process loads this file, re-reading
# only changed fingerprints. Stale or missing files cause a full read, never a wrong answer.
# Both live under the queue folder, so they follow wherever the queue is pointed.
ARCHIVE_AFTER_DAYS = 30        # no reader was measured looking further back; one constant, and nothing is deleted
ARCHIVED_STATUSES = ("done", "failed", "cancelled", "expired")
SNAPSHOT_EVERY_S = 600.0
# saved_at starts at import: the first periodic save comes SNAPSHOT_EVERY_S into a process (the stopping process saved
# its own), so a short-lived process - a test - never writes one unasked
_SNAPSHOT = {"loaded": False, "saved_at": time.monotonic(), "reads": 0, "saving": False}


def _archive_root(controls=False):
    """The archive BESIDE the queue: <...>/p20-render-queue -> <...>/p20-render-queue-archive (control slips under its
    `controls`). The lab's own precedent for Kirk's rotated state (run_ledger.archive_root: hai-runs-saved ->
    hai-runs-archive, "moved to the archive beside it, never deleted"), and it follows wherever the queue is pointed."""
    root = QUEUE_DIR.rstrip("/\\") + "-archive"
    return os.path.join(root, "controls") if controls else root


def _adopt_inside_archive():
    """Migrates any legacy archive folders inside the queue or controls directory to the canonical external archive location.
    Removes the source folders once empty. Never raises."""
    for old, new in ((os.path.join(QUEUE_DIR, "archive"), _archive_root()),
                     (os.path.join(CONTROL_DIR, "archive"), _archive_root(controls=True))):
        try:
            for month in os.listdir(old):
                src = os.path.join(old, month)
                if not os.path.isdir(src):
                    continue
                os.makedirs(os.path.join(new, month), exist_ok=True)
                for name in os.listdir(src):
                    os.replace(os.path.join(src, name), os.path.join(new, month, name))
                os.rmdir(src)
            os.rmdir(old)
        except OSError:
            continue


def _load_archived(job_id):
    root = _archive_root()
    try:
        months = sorted(os.listdir(root), reverse=True)
    except OSError:
        return None
    for m in months:
        rec = _load(os.path.join(root, m, job_id + ".json"))
        if rec:
            return rec
    return None


def _age_days(rec, now, *fields):
    for f in fields:
        try:
            t = datetime.datetime.fromisoformat(str(rec.get(f)))
            if t.tzinfo is None:
                t = t.replace(tzinfo=datetime.timezone.utc)
            return (now - t).total_seconds() / 86400.0, t
        except (TypeError, ValueError):
            continue
    return None, None


def archive_old(now=None, days=None):
    """Move finished factory orders and collected control slips older than `days` out of the folders every factory's
    ask scans -> {"orders": n, "controls": n}. A record whose age cannot be read stays where it is. Never raises."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    days = ARCHIVE_AFTER_DAYS if days is None else days
    moved = {"orders": 0, "controls": 0}
    try:
        _adopt_inside_archive()
        recs = _scan_records(QUEUE_DIR)
        needed = {a for r in recs if r.get("status") not in ARCHIVED_STATUSES for a in (r.get("after_ids") or [])}
        work = [(r, _path(r["id"]), _archive_root(), "orders") for r in recs
                if r.get("kind") == "factory_order" and r.get("status") in ARCHIVED_STATUSES
                and r.get("id") and r["id"] not in needed]
        work += [(r, _control_path(r["id"]), _archive_root(controls=True), "controls")
                 for r in _scan_records(CONTROL_DIR) if r.get("status") == "collected" and r.get("id")]
        for rec, src, root, what in work:
            age, t = _age_days(rec, now, "updated", "collected_at", "created")
            if age is None or age < days or not os.path.isfile(src):
                continue
            dest = os.path.join(root, t.strftime("%Y-%m"))
            try:
                os.makedirs(dest, exist_ok=True)
                os.replace(src, os.path.join(dest, os.path.basename(src)))
                moved[what] += 1
            except OSError:
                continue
        if moved["orders"] or moved["controls"]:
            _wrote()
    except Exception:                                               # noqa: BLE001 - housekeeping never breaks the desk
        pass
    return moved


def _snapshot_file():
    return os.path.join(QUEUE_DIR, "cache", "parsed-records.json")


def save_snapshot():
    """Write the parsed records to one file for the next process. Never raises -> how many were written."""
    try:
        root = os.path.join(QUEUE_DIR, "")           # only this queue's own records (its control slips are under it)
        body = {p: [fp[0], fp[1], rec] for p, (fp, rec) in list(_PARSED.items()) if p.startswith(root)}
        path = _snapshot_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp." + uuid.uuid4().hex
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(body, f)
        os.replace(tmp, path)
        _SNAPSHOT.update(saved_at=time.monotonic(), reads=0)
        return len(body)
    except Exception:                                               # noqa: BLE001
        return 0
    finally:
        _SNAPSHOT["saving"] = False


def _load_snapshot():
    """Once per process, before its first scan: the last process's parsed records. Each is still checked against its
    file's fingerprint by the scan, so a stale entry is simply read again."""
    _SNAPSHOT["loaded"] = True
    try:
        with open(_snapshot_file(), encoding="utf-8") as f:
            body = json.load(f)
        root = os.path.join(QUEUE_DIR, "")
        for p, (mtime_ns, size, rec) in body.items():
            if p.startswith(root):
                _PARSED.setdefault(p, ((mtime_ns, size), rec))
        return len(body)
    except Exception:                                               # noqa: BLE001 - no file, or a torn one: a cold read
        return 0


def _snapshot_if_due():
    if _SNAPSHOT["reads"] and not _SNAPSHOT["saving"] and time.monotonic() - _SNAPSHOT["saved_at"] >= SNAPSHOT_EVERY_S:
        _SNAPSHOT["saving"] = True
        threading.Thread(target=save_snapshot, name="desk-snapshot", daemon=True).start()


DEAD_STATUSES = ("failed", "cancelled")
LIVE_STATUSES = ("claimed", "running", "done")    # an attempt that is under way or succeeded: earlier errors go stale

# The sentence a factory writes when an operator's cancel slip ends a desk order. Factories that predate
# the explicit `cancelled` receipt still report it as status=failed with this text.
_LEGACY_CANCEL_PREFIX = "cancelled on "
_LEGACY_CANCEL_MARK = "by operator slip"


def is_operator_cancel(status, error=""):
    """True when a receipt (or a stored record) is an operator CANCEL, not a failure.

    ONE derivation, used by the desk's result endpoint and by the record migration: the explicit
    `cancelled` status, or the legacy form (`failed` + `cancelled on <box> by operator slip: ...`).
    A real failure, and the separate `stopped by operator slip` text, are NOT cancels."""
    s = str(status or "").strip().lower()
    if s == "cancelled":
        return True
    e = str(error or "")
    return s == "failed" and e.startswith(_LEGACY_CANCEL_PREFIX) and _LEGACY_CANCEL_MARK in e


def mark(job_id, status, **extra):
    job = get(job_id)
    if not job:
        return None
    job["status"] = status
    job["updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if str(status).lower() in LIVE_STATUSES:    # a later attempt that runs or succeeds must not keep an earlier
        for k in ("error", "hold_reason"):      # attempt's failure text: a done record with a stop error reads stopped
            if k not in extra:
                job.pop(k, None)
    job.update(extra)
    _save(job)
    if str(status).lower() in DEAD_STATUSES:
        _block_successors_of(job_id)
    return job


def _releases(job, prev):
    """Return True if `prev` releases `job`.

    Releases when `prev` is 'done'. If `prev` is 'failed', releases only if `job` declares
    `go_on_after_failure` in its spec. A 'cancelled' predecessor never releases, ensuring an
    operator Stop halts the chain."""
    st = (prev or {}).get("status")
    return st == "done" or (st == "failed" and bool((job.get("spec") or {}).get("go_on_after_failure")))


def _block_successors_of(job_id, _depth=0):
    """Mark successors as 'blocked' if a predecessor died, preventing indefinite pending states.

    Distinct from 'failed': these steps never ran and aren't defective, so we don't mark them
    failed (which implies error) or delete them (which hides the broken chain). This allows
    operators to identify stalled chains and requeue from the actual failure point.

    Recurses through the chain (bounded by _depth > 32 to prevent infinite loops on malformed cycles)
    to ensure all downstream steps are blocked. Note: this does not affect the fail-closed rule for
    MISSING predecessors in 'blocked_by', which is intentionally conservative."""
    if _depth > 32:
        return
    dead = get(job_id)
    for j in all_jobs():
        if j.get("status") != "pending":
            continue
        if job_id not in (j.get("after_ids") or []):
            continue
        if _releases(j, dead):              # an order that goes on after a failure is released, not blocked
            continue
        j["status"] = "blocked"
        j["updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        j["error"] = "predecessor %s did not complete; this step can never be released" % job_id
        _save(j)
        _block_successors_of(j.get("id"), _depth + 1)


GPU_STATUS_FILE = "_gpu_status.json"
# Kept for backward compatibility: _gpu_status.json is mirrored from the last writer
# and used as a fallback if per-host files are missing.
def _gpu_status_path(host=None):
    if not host:
        return os.path.join(QUEUE_DIR, GPU_STATUS_FILE)
    safe = "".join(c for c in str(host).lower() if c.isalnum() or c in "-_")
    return os.path.join(QUEUE_DIR, "_gpu_status_%s.json" % (safe or "unknown"))


def set_gpu_status(status):
    """Worker heartbeat payload schema.

    Keys:
      - host (str, optional): Factory hostname. If absent, the worker is legacy
        and writes to the global status file for backward compatibility during
        rollout."""
    os.makedirs(QUEUE_DIR, exist_ok=True)
    status = dict(status)
    status["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    host = status.get("host")
    if host:
        # A host-stamped worker writes ONLY its own file. Mirroring it into the legacy path as well
        # would recreate the very clobber this change exists to fix: /p20/training reads the legacy
        # file and means "the training box", so an idle second factory writing there would keep
        # describing itself as the training card. Attribution is the whole point.
        path = _gpu_status_path(host)
    else:
        # Pre-host worker: it cannot say who it is, so it keeps the legacy path exactly as before.
        # This is what lets the rollout be one host at a time instead of a flag day.
        path = _gpu_status_path()
    # Readers must see the previous complete heartbeat or the new complete one,
    # never an empty file that transiently makes a busy factory look idle.
    tmp = path + ".tmp." + uuid.uuid4().hex
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(status, f)
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


def gpu_status_hosts(max_age_secs=360):
    """{host: (status, age_s)} for every factory that has ever reported, freshness included.

    This is what a per-factory homepage reads: one tile per host, each with its OWN age, so a
    quiet factory reads as quiet instead of being masked by a chatty one.
    """
    out = {}
    try:
        names = os.listdir(QUEUE_DIR)
    except OSError:
        return out
    for n in names:
        if not (n.startswith("_gpu_status_") and n.endswith(".json")):
            continue
        st = _load(os.path.join(QUEUE_DIR, n))
        if not st:
            continue
        host = st.get("host") or n[len("_gpu_status_"):-len(".json")]
        try:
            ts = datetime.datetime.fromisoformat(st["ts"])
            age = (datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds()
        except Exception:
            age = None
        out[host] = (st, age)
    return out


def gpu_status(max_age_secs=360, host=None):
    """Return last worker heartbeat, or None if stale/absent.

    If `host` is provided, query only that factory. If omitted, fall back to
    legacy behavior (query all factories).
    """
    if host:
        st, age = gpu_status_hosts().get(host, (None, None))
        if st is None or age is None:
            return None
        return st if age <= max_age_secs else None
    st, age = gpu_status_any()
    if st is None or age is None:
        return None
    return st if age <= max_age_secs else None


def gpu_status_any():
    """(last heartbeat, age_secs) REGARDLESS of staleness — (None, None) if there has never been one.

    Freshness is the right rule for DISPLAY (never show a stale ETA as live) but the wrong rule for
    a SAFETY gate: a 14-hour training run does not end because the process reporting on it died.
    `gpu_status()` returning None must not be read as "the card is free": that is a guard going
    blind at exactly the moment it matters. Callers that protect the card use this and decide for
    themselves what an unknown means (see comfy_client.render_busy).
    """
    st = _load(os.path.join(QUEUE_DIR, GPU_STATUS_FILE))
    if not st:
        return None, None
    try:
        ts = datetime.datetime.fromisoformat(st["ts"])
        return st, (datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds()
    except Exception:
        return None, None


def flag_review(user, person_key, scene, matched):
    """Logs every body-mod render that proceeds for admin audit.
    Records requester, subject, scene, and matched terms in an
    append-only JSONL file."""
    os.makedirs(QUEUE_DIR, exist_ok=True)
    rec = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), "user": user,
           "person": person_key, "scene": scene, "matched": matched}
    with open(os.path.join(QUEUE_DIR, "_review.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def blocked_by(job, by_id=None):
    """Return IDs of predecessor steps that have not finished. An empty list means ready.

    A missing predecessor ID is treated as UNMET (fail-closed). Missing records are never
    treated as satisfied, preventing jobs from running on non-existent or unrendered data."""
    need = job.get("after_ids") or []
    if not need:
        return []
    if by_id is None:
        by_id = {j.get("id"): j for j in all_jobs()}
    out = []
    for a in need:
        prev = by_id.get(a)
        if not prev or not _releases(job, prev):
            out.append(a)
    return out


def dispatchable(energy_ok):
    """Return jobs eligible for immediate dispatch.

        Filters pending jobs based on energy cost and dependency readiness:
        - If `energy_ok` is True (cheap), all pending jobs are considered.
        - If False (expensive), only jobs marked "rush" are considered.
        - Jobs blocked by unfinished predecessors (`after_ids`) are excluded.

        Safety: Relies on `energy.is_runnable_now` being fail-closed;
        price glitches cause deferral, not execution.
    """
    # Read once via cache: multiple factories poll this, and per-poll CIFS reads of all records cause timeouts.
    jobs = _shared_records(QUEUE_DIR)
    by_id = {j.get("id"): j for j in jobs}
    ready = [j for j in jobs if j.get("status") == "pending"
             and (energy_ok or j.get("rush")) and not blocked_by(j, by_id)]
    return [copy.deepcopy(j) for j in sorted(ready, key=lambda j: j.get("created", ""))]


# Controls live in a subdirectory because all_jobs() is non-recursive.
# This prevents controls from being picked up by render workers or dashboards.

CONTROL_DIR = os.path.join(QUEUE_DIR, "controls")
CONTROL_ACTIONS = ("stop", "requeue", "cancel", "priority")


def _control_path(cid):
    return os.path.join(CONTROL_DIR, cid + ".json")


def enqueue_control(host, action, order, payload=None, source=""):
    """Hold one control for `host` to collect. `action` maps to the factory's own slip channels:
    'stop' -> `<order>.stop.json` (read by the RUNNING job); everything else -> a `*.ctl.json`
    slip (read by the order taker between orders). `payload` carries the slip's optional fields
    verbatim (why/force/value/at_step) — the CONTRACT is the receptionist's, defined in
    p20_foundry.process_control_orders / stop_order, never invented here."""
    action = (action or "").strip().lower()
    if action not in CONTROL_ACTIONS:
        raise ValueError("control action must be one of %s" % (CONTROL_ACTIONS,))
    host = (host or "").strip().lower()
    order = (order or "").strip()
    if not host or not order:
        raise ValueError("a control names its exact artifact: host and order are required")
    rec = {
        "id": uuid.uuid4().hex[:12],
        "kind": "factory_control",
        "host": host,
        "action": action,
        "order": order,
        "payload": dict(payload or {}),
        "source": str(source or "")[:200],
        "status": "pending",
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    os.makedirs(CONTROL_DIR, exist_ok=True)
    with open(_control_path(rec["id"]), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    _wrote()
    return rec


def pending_controls(host):
    """Controls waiting for THIS host, oldest-first (slips are processed oldest-first at the
    factory too — same ordering end to end)."""
    h = (host or "").strip().lower()
    if not os.path.isdir(CONTROL_DIR):
        return []
    recs = _shared_records(CONTROL_DIR)
    return [copy.deepcopy(r) for r in sorted((r for r in recs if r.get("status") == "pending" and r.get("host") == h),
                                             key=lambda r: r.get("created", ""))]


def claim_control(cid, host):
    """Single-claim, same contract as claim(): the record on success, None if already taken —
    'not mine, move on', never an error. Claim-before-write at the factory means a control that
    fails to land stays claimed and STOPS rather than being materialised twice."""
    r = _load(_control_path(cid))
    if not r or r.get("status") != "pending":
        return None
    if (r.get("host") or "") != (host or "").strip().lower():
        return None
    r["status"] = "collected"
    r["collected_by"] = host
    r["collected_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with open(_control_path(cid), "w", encoding="utf-8") as f:
        json.dump(r, f, indent=1)
    _wrote()
    return r
