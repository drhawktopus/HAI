"""Generic pending-action confirmation framework for Home AI write tools.

Write tools register a preview plus an execute callback, then return the token to the
model/user. confirm_action executes only the stored action for the same user before expiry.
This is intentionally small and in-memory for Phase 0; future UI work can surface the same
records through an endpoint or persistent store.
"""
import datetime as _dt
import json
import os
import secrets
import threading
import traceback

import config
import notes

_LOCK = threading.Lock()
_PENDING = {}
_DEFAULT_TTL_SECONDS = int(getattr(config, "PENDING_ACTION_TTL_SECONDS", 900))
_RISK_ORDER = {"LOW": 0, "MED": 1, "HIGH": 2}
# Extra rules an extension adds (tools: RISK_RULES): each takes (kind, metadata) and returns [(level, reason)].
RISK_RULES = []


def _affected_count(meta):
    """Best-effort count of items a pending action affects, for bulk-risk detection."""
    if not isinstance(meta, dict):
        return 1
    if isinstance(meta.get("count"), int):
        return meta["count"]
    h = meta.get("hashes")
    if isinstance(h, str):
        return len([x for x in h.split("|") if x])
    if isinstance(h, (list, tuple)):
        return len(h)
    return 1


def assess_risk(kind, destructive, metadata):
    """Deterministic risk level (LOW/MED/HIGH) + reasons for a pending write action. Rule-based on purpose —
    NO model: a destructive flag, on-disk data loss, bulk size, and a few known dangerous combos. Robust
    against prompt injection (it counts effects, it doesn't 'judge' attacker-influenced text)."""
    meta = metadata if isinstance(metadata, dict) else {}
    level, reasons = "LOW", []

    def bump(to, why):
        nonlocal level
        if _RISK_ORDER[to] > _RISK_ORDER[level]:
            level = to
        reasons.append(why)

    n = _affected_count(meta)
    if destructive:
        bump("MED", "destructive operation")
    if meta.get("self_deploy"):
        bump("HIGH", "rebuilds and redeploys the orchestrator itself")
    if meta.get("delete_files"):
        bump("HIGH", "deletes downloaded files from disk")
    if n >= getattr(config, "RISK_BULK_HIGH", 25):
        bump("HIGH", f"affects {n} items (bulk)")
    elif n >= getattr(config, "RISK_BULK_MED", 5):
        bump("MED", f"affects {n} items")
    for rule in RISK_RULES:
        for to, why in rule(kind, meta) or ():
            bump(to, why)
    return level, reasons


def _risk_banner(level, reasons):
    if level == "LOW":
        return ""
    icon = "\U0001f6d1" if level == "HIGH" else "⚠️"
    return f"{icon} RISK {level} — " + "; ".join(reasons)


def _audit(event, record, result=None):
    """Append a structured JSON-line audit entry (fail-soft) for a write action's lifecycle."""
    path = getattr(config, "AUDIT_FILE", None)
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = {
            "ts": _iso(_now()),
            "event": event,
            "kind": record.get("kind"),
            "user": record.get("user"),
            "risk": record.get("risk"),
            "token": record.get("token"),
            "metadata": record.get("metadata"),
        }
        if result is not None:
            entry["result"] = str(result)[:300]
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(ts):
    return ts.astimezone().isoformat(timespec="seconds")


def _prune(now=None):
    now = now or _now()
    expired = [token for token, action in _PENDING.items() if action["expires_at"] <= now]
    for token in expired:
        action = _PENDING.pop(token, None)
        if action:
            notes.log(f"PENDING ACTION EXPIRED: {action['kind']} token={token} user={action['user']}")
            _audit("EXPIRED", action)


def _dedup_signature(user, kind, metadata, preview):
    """Stable identity for a pending action so an identical re-registration reuses the existing card
    instead of creating a duplicate (e.g. a retried or cancel-raced deploy request)."""
    try:
        meta_sig = json.dumps(metadata or {}, sort_keys=True, default=str)
    except Exception:
        meta_sig = repr(metadata)
    return (user or "", kind or "action", meta_sig, preview or "")


def _payload(record):
    """Public pending-action payload returned to the caller/UI."""
    warning = ("\nDESTRUCTIVE ACTION: confirm only if the user explicitly approves this exact operation."
               if record["destructive"] else "")
    return {
        "status": "pending",
        "kind": record["kind"],
        "action_token": record["token"],
        "expires_at": _iso(record["expires_at"]),
        "destructive": record["destructive"],
        "risk": record.get("risk", "LOW"),
        "risk_reasons": record.get("risk_reasons", []),
        "preview": record["preview"] + warning,
    }


def register(kind, preview, execute, user, ttl_seconds=None, destructive=False, metadata=None):
    """Register a pending write action and return a preview/token payload.

    execute must be a zero-argument callable. It is held in memory and called only by confirm().
    Idempotent: an identical, non-expired pending action for the same user reuses the existing card
    instead of creating a duplicate (prevents duplicate deploy/confirm cards on retry or cancel races).
    """
    if not callable(execute):
        raise TypeError("execute must be callable")
    user = user or ""
    now = _now()
    risk, risk_reasons = assess_risk(kind, destructive, metadata)
    banner = _risk_banner(risk, risk_reasons)
    full_preview = (banner + "\n" + (preview or "")) if banner else (preview or "")
    signature = _dedup_signature(user, kind, metadata, full_preview)
    with _LOCK:
        _prune(now)
        for existing in _PENDING.values():
            if existing.get("_signature") == signature:
                notes.log(f"PENDING ACTION DEDUP: reused {existing['kind']} token={existing['token']} user={user}")
                return _payload(existing)
        token = secrets.token_urlsafe(18)
        expires_at = now + _dt.timedelta(seconds=ttl_seconds or _DEFAULT_TTL_SECONDS)
        record = {
            "token": token,
            "kind": kind or "action",
            "preview": full_preview,
            "execute": execute,
            "user": user,
            "created_at": now,
            "expires_at": expires_at,
            "destructive": bool(destructive),
            "metadata": metadata or {},
            "risk": risk,
            "risk_reasons": risk_reasons,
            "_signature": signature,
        }
        _PENDING[token] = record
    notes.log(f"PENDING ACTION PREVIEW: {record['kind']} token={token} user={user} risk={risk} destructive={record['destructive']}")
    _audit("PREVIEW", record)
    return _payload(record)

def list_pending(user=None):
    with _LOCK:
        _prune()
        items = list(_PENDING.values())
    if user is not None:
        items = [a for a in items if a["user"] == (user or "")]
    return [{
        "kind": a["kind"],
        "action_token": a["token"],
        "expires_at": _iso(a["expires_at"]),
        "destructive": a["destructive"],
        "risk": a.get("risk", "LOW"),
        "preview": a["preview"],
    } for a in items]


def confirm(token, user=None):
    token = (token or "").strip()
    if not token:
        return {"status": "error", "message": "confirm_action needs an action_token."}
    with _LOCK:
        _prune()
        action = _PENDING.get(token)
        if not action:
            return {"status": "error", "message": "No pending action matched that token. It may have expired or already been used."}
        if action["user"] != (user or ""):
            notes.log(f"PENDING ACTION USER MISMATCH: token={token} owner={action['user']} caller={user}")
            return {"status": "error", "message": "That pending action belongs to a different user."}
        _PENDING.pop(token, None)
    try:
        result = action["execute"]()
    except Exception as exc:
        notes.log(f"PENDING ACTION FAILED: {action['kind']} token={token} user={user}: {exc}\n{traceback.format_exc()}")
        _audit("FAILED", action, exc)
        return {"status": "error", "message": f"Action failed: {exc}"}
    notes.log(f"PENDING ACTION APPLIED: {action['kind']} token={token} user={user} risk={action.get('risk')}")
    _audit("APPLIED", action, result)
    return {"status": "applied", "kind": action["kind"], "risk": action.get("risk", "LOW"), "result": result}


def deny(token, user=None):
    token = (token or "").strip()
    if not token:
        return {"status": "error", "message": "deny_action needs an action_token."}
    with _LOCK:
        _prune()
        action = _PENDING.get(token)
        if not action:
            return {"status": "error", "message": "No pending action matched that token."}
        if action["user"] != (user or ""):
            return {"status": "error", "message": "That pending action belongs to a different user."}
        _PENDING.pop(token, None)
    notes.log(f"PENDING ACTION DENIED: {action['kind']} token={token} user={user}")
    _audit("DENIED", action)
    return {"status": "denied", "kind": action["kind"]}


def _clear_for_tests():
    with _LOCK:
        _PENDING.clear()
