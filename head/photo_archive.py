"""Describes and deletes photos in archived chats (outside CHATS_KEPT_PER_USER or manually archived) after PHOTO_DESCRIBE_AFTER_S. The description must be persisted to DESCRIPTIONS before deletion; failures retain the photo. The clock resets if the chat becomes active again. This module never raises as it runs from background hooks."""
import base64
import datetime
import hashlib
import json
import os
import threading
import time
import uuid

import config
import history

STATE = config.PHOTO_ARCHIVE_STATE_DIR
CLOCK = os.path.join(STATE, "archive-clock.json")      # {"<user>/<chat>": first seen archived, epoch seconds}
PENDING = os.path.join(STATE, "pending.json")
DESCRIPTIONS = "descriptions.json"                     # per chat, beside its .uploads: {upload_id: {...}}

FAMILY, TIER, KIND = "vlm", "lead", "review"
REL_DIR = "campaigns/photo-archive"
ORDER_LOCAL = os.environ.get("PHOTO_ARCHIVE_ORDER_LOCAL") or os.path.join(
    os.environ.get("TABFOUNDRY_SHARE", "/tabjobs"), *REL_DIR.split("/"))
ORDER_UNC = os.environ.get("PHOTO_ARCHIVE_ORDER_UNC") or ""
PAYLOAD = "{FOUNDRY_ROOT}\\bin\\infer_ollama.py"
TERMINAL = ("done", "failed", "cancelled", "expired")
PHOTO_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

SYSTEM = ("You describe a photo for someone who will never see it again: it is being removed from an old conversation "
          "and your description takes its place. Say what is in it: the scene, objects, any readable text (quote it), "
          "people by appearance only (never guess who anyone is), the setting, and anything notable or funny about it. "
          "Plain prose, a short paragraph. Describe only what you see; if something is unclear, say so.")
PROMPT = "Describe this photo. Its file name was %s."

_LOCK = threading.RLock()


def _note(message, dm=False):
    """Every outcome is said: Kirk's notes always, a DM when something was DONE or FAILED (a daily "nothing due" DM
    would be noise; the notes line means a quiet sweep is still never a silent one)."""
    try:
        import notes
        notes.log("photo archive: " + message)
    except Exception:                                                  # noqa: BLE001
        pass
    if dm:
        try:
            import claim_sweep
            claim_sweep.notify_desk(message, tag="photos")
        except Exception:                                              # noqa: BLE001
            pass


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else default
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def _is_photo(path):
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return False
    return (head[:8] == b"\x89PNG\r\n\x1a\n" or head[:3] == b"\xff\xd8\xff" or head[:6] in (b"GIF87a", b"GIF89a")
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP") or head[:2] == b"BM")


def _users():
    try:
        return sorted(p.name for p in history.HISTORY_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []


def archived_chats(user, now=None, clock=None):
    """-> (archived chat ids, clock updated for this user). Outside the most recent CHATS_KEPT_PER_USER by last use
    (index `updated_at`), or archived by hand. A chat back among the recent ones loses its clock entry."""
    now = now or time.time()
    clock = dict(clock or {})
    chats = history._load_index(user)["chats"]
    recent = {c["id"] for c in sorted(chats, key=lambda c: c.get("updated_at") or "", reverse=True)
              [:config.CHATS_KEPT_PER_USER]}
    archived = []
    for c in chats:
        key = "%s/%s" % (user, c["id"])
        if c["id"] in recent and not c.get("archived"):
            clock.pop(key, None)
            continue
        clock.setdefault(key, now)
        archived.append(c["id"])
    return archived, clock


def due_photos(now=None):
    """-> (photos due [{user, chat, upload_id, path}], the whole updated clock). Due = in a chat archived for at least
    PHOTO_DESCRIBE_AFTER_S, a photo by its first bytes, not yet described."""
    now = now or time.time()
    clock = _read_json(CLOCK, {}) or {}
    mine = {}
    due = []
    for user in _users():
        if config.is_test_user(user):
            continue
        archived, user_clock = archived_chats(user, now, {k: v for k, v in clock.items() if k.split("/", 1)[0] == user})
        mine.update(user_clock)
        for chat in archived:
            if now - user_clock.get("%s/%s" % (user, chat), now) < config.PHOTO_DESCRIBE_AFTER_S:
                continue
            root = history.chat_upload_root(user, chat)
            folder = root / ".uploads"
            if not folder.is_dir():
                continue
            done = _read_json(str(root / DESCRIPTIONS), {}) or {}
            for f in sorted(folder.iterdir()):
                if f.is_file() and not f.is_symlink() and f.name not in done and _is_photo(str(f)):
                    due.append({"user": user, "chat": chat, "upload_id": f.name, "path": str(f)})
    return due, mine


def order_body(name, photos):
    """(FactoryOrderReq kwargs, paths) for ONE description order over `photos` (due_photos rows)."""
    day = time.strftime("%Y%m%d")
    local = os.path.join(ORDER_LOCAL, day)
    unc = ORDER_UNC + "\\" + day
    os.makedirs(local, exist_ok=True)
    items = []
    for i, p in enumerate(photos):
        with open(p["path"], "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        items.append({"id": "p%03d" % (i + 1), "system": SYSTEM,
                      "prompt": PROMPT % p["upload_id"].split("-", 1)[-1], "images": [b64]})
    _write_json(os.path.join(local, name + ".items.json"), {"items": items})
    spec = {"name": name, "mode": "compute", "load": "gpu", "initiator": "hai", "payload": PAYLOAD,
            "args": [unc + "\\" + name + ".items.json", unc + "\\" + name + ".out.json",
                     "--model", "{POLICY_MODEL}", "--rounds", "1", "--num-ctx", "{POLICY_NUM_CTX}",
                     "{POLICY_TEMPERATURE_ARGS}", "{POLICY_THINK_ARGS}", "{POLICY_OFFLOAD_ARGS}"],
            "progress_glob": unc + "\\" + name + ".out.progress\\*.done", "progress_total": len(items),
            "notify": "failures"}
    body = {"spec": spec, "requires": ["order.compute"], "kind": KIND, "family": FAMILY, "tier": TIER,
            "urgency": "background"}
    paths = {"items": os.path.join(local, name + ".items.json"), "out": os.path.join(local, name + ".out.json")}
    return body, paths


def _desk():
    import app
    return app


def _forget(pend):
    """Clear the pending order and its items file (the photos' base64 copy on the share) - every outcome."""
    try:
        os.remove(pend["paths"]["items"])
    except (OSError, KeyError, TypeError):
        pass
    try:
        os.remove(PENDING)
    except OSError:
        pass


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def apply_description(p, text, who, now=None):
    """Record the description, rewrite the transcript's attachment line, then delete the photo and its image-generation
    copy (the same bytes in comfy_client.uploads_dir). -> True when the photo is gone. The record is written FIRST: a
    photo is never deleted without its description saved."""
    text = " ".join(str(text or "").split())
    if not text:
        return False
    root = history.chat_upload_root(p["user"], p["chat"])
    when = datetime.datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%d")
    dpath = str(root / DESCRIPTIONS)
    done = _read_json(dpath, {}) or {}
    sha = _sha(p["path"])
    done[p["upload_id"]] = {"description": text, "described": when, "by": who, "sha256": sha,
                            "file": p["upload_id"].split("-", 1)[-1]}
    _write_json(dpath, done)
    shown = p["upload_id"].split("-", 1)[-1]
    history.rewrite_attachment(p["user"], p["chat"], p["upload_id"],
                               "[Attached photo: %s (upload %s). Removed %s, two weeks after this chat was archived; "
                               "a model that could see it described it: %s]" % (shown, p["upload_id"], when, text))
    try:
        import comfy_client
        gen = comfy_client.uploads_dir(p["user"])
        for f in (gen.iterdir() if gen.is_dir() else []):
            if f.is_file() and f.stat().st_size == os.path.getsize(p["path"]) and _sha(str(f)) == sha:
                os.remove(str(f))
    except Exception:                                                  # noqa: BLE001 - the main photo still goes
        pass
    try:
        os.remove(p["path"])
        return True
    except OSError:
        return False


def collect(desk=None):
    """Read the pending description order's fate and act on it. -> None (nothing pending), {"pending": ...}, or the
    outcome. Never raises."""
    with _LOCK:
        pend = _read_json(PENDING)
        if not pend:
            return None
        try:
            view = (desk or _desk())._order_status_view(pend["id"]) or {}
        except Exception as exc:                                       # noqa: BLE001 - no verdict: keep it pending
            return {"pending": "unknown", "why": "could not read the order: %r" % (exc,)}
        st = view.get("status")
        if not view.get("ok", True) and st is None:
            _note("order %s is gone from the desk; its %d photo(s) stay for the next sweep" % (
                pend.get("name"), len(pend.get("photos") or [])), dm=True)
            _forget(pend)
            return {"lost": True}
        if st not in TERMINAL:
            return {"pending": st or "unknown", "id": pend["id"]}
        if st != "done":
            _note("order %s ended %s: nothing described, nothing deleted; its %d photo(s) stay for the next sweep" % (
                pend.get("name"), st, len(pend.get("photos") or [])), dm=True)
            _forget(pend)
            return {"ended": st}
        out = _read_json(pend["paths"]["out"])
        if out is None:
            if not pend.get("missing_seen"):
                pend["missing_seen"] = time.strftime("%Y-%m-%d %H:%M")     # the delivery can trail the receipt once
                _write_json(PENDING, pend)
                return {"pending": "delivery not on the share yet"}
            _note("order %s is done but delivered nothing; nothing deleted" % pend.get("name"), dm=True)
            _forget(pend)
            return {"undelivered": True}
        model = out.get("served") or out.get("model")
        who = "%s %s" % (view.get("host") or "?", model) if model else (view.get("host") or "?")
        rows = {r.get("id"): (r.get("answer") or "") for r in out.get("rows") or [] if r.get("id") and not r.get("error")}
        described, kept = [], []
        for item_id, p in zip(["p%03d" % (i + 1) for i in range(len(pend["photos"]))], pend["photos"]):
            if os.path.exists(p["path"]) and apply_description(p, rows.get(item_id), who):
                described.append(p["upload_id"])
            else:
                kept.append(p["upload_id"])
        _note("described and removed %d archived photo(s) on %s%s" % (
            len(described), who, ("; %d kept (no usable description): %s" % (len(kept), ", ".join(kept))) if kept else ""),
            dm=True)
        _forget(pend)
        return {"described": described, "kept": kept, "by": who}


def on_order_result(job):
    """The desk's completion receipt: collect at once when it is the pending description order."""
    try:
        pend = _read_json(PENDING)
        if pend and (job or {}).get("id") == pend.get("id"):
            collect()
    except Exception as exc:                                           # noqa: BLE001 - a receipt hook never raises
        print("[DESK] photo archive receipt hook failed: %s: %s" % (type(exc).__name__, exc))


def sweep(desk=None, now=None):
    """The timer's round: collect the previous order, advance every chat's archive clock, submit the photos that are due.
    -> a summary. Never raises."""
    with _LOCK:
        try:
            prev = collect(desk)
            if prev and "pending" in prev:
                _note("sweep waited: the previous order is still %s at the desk" % prev["pending"])
                return {"skipped": True, "pending": prev}
            due, clock = due_photos(now)
            _write_json(CLOCK, clock)
            archived = len(clock)
            if not due:
                _note("nothing due (%d archived chat(s) on the clock)" % archived)
                return {"due": 0, "archived_chats": archived, "collected": prev}
            name = "photo-archive-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:4])
            body, paths = order_body(name, due)
            app = desk or _desk()
            reply = app.p20_order(app.FactoryOrderReq(**body))
            steps = [s for s in (reply.get("steps") or []) if isinstance(s, dict) and s.get("id")]
            oid = reply.get("id") if reply.get("held") else (steps[0]["id"] if len(steps) == 1 else None)
            if not reply.get("ok") or not oid:
                try:
                    os.remove(paths["items"])
                except OSError:
                    pass
                why = reply.get("error") or "; ".join(reply.get("why") or []) or str(reply)[:200]
                _note("the desk refused the description order for %d photo(s): %s" % (len(due), why), dm=True)
                return {"refused": why, "due": len(due)}
            _write_json(PENDING, {"id": oid, "name": name, "paths": paths, "photos": due,
                                  "submitted": time.strftime("%Y-%m-%d %H:%M")})
            where = "held at the desk" if reply.get("held") else "placed on %s" % steps[0].get("host")
            _note("submitted %d archived photo(s) for description (%s, order %s)" % (len(due), where, name))
            return {"submitted": len(due), "id": oid, "where": where, "collected": prev}
        except Exception as exc:                                       # noqa: BLE001 - a timer's round never raises
            _note("sweep failed: %s: %s" % (type(exc).__name__, exc), dm=True)
            return {"error": "%s: %s" % (type(exc).__name__, exc)}
