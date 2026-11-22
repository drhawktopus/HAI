"""Escalation feed: surface for coding-escalation packets so humans always see them.

Three responsibilities:
1. `pending()` – list undecided escalation packets from the runner, newest first, minus acked ones,
   AND auto-retire STALE ones (the job behind them is dead) so a human is never bothered by them.
2. `ack(eid, user)` – local bookkeeping only; writes to a JSON file in ESCALATION_STATE_DIR.
3. `notify_new(packets, seen_ids)` – pure function that returns HUMAN-LEGIBLE DM text for unseen packets.

Fail-soft: if the runner is unreachable the module returns an empty list with an error marker,
never raising into the endpoint layer.

NO import of `tools` (cycle risk). Config is imported plainly.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

import config as _config
import coding_client


# ---------------------------------------------------------------------------
# State file helpers
# ---------------------------------------------------------------------------

def _state_dir() -> str:
    return _config.ESCALATION_STATE_DIR


def _ack_file() -> str:
    return os.path.join(_state_dir(), "acked.json")


def _load_acked() -> dict[str, str]:
    """Load the acked-id -> user mapping from disk (empty dict if missing)."""
    path = _ack_file()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_acked(acked: dict[str, str]) -> None:
    """Persist the acked-id -> user mapping."""
    os.makedirs(_state_dir(), exist_ok=True)
    tmp = _ack_file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(acked, f, indent=2, ensure_ascii=True)
        f.write("\n")
    os.replace(tmp, _ack_file())


# ---------------------------------------------------------------------------
# Staleness + human-legibility helpers
# ---------------------------------------------------------------------------

# The runner's escalation reason codes (runner.py escalation_packet_create.allowed_reasons),
# mapped to plain language a human can act on without knowing the machine vocabulary.
_REASON_PLAIN = {
    "capability_limit": "ran into something its tools can't do",
    "repeated_failure": "kept failing the same step and couldn't get past it",
    "architecture_or_security": "hit a design or security decision it shouldn't make alone",
    "external_research": "needs information it can't find on its own",
    "user_requested": "is asking for your input by design",
}

# The runner's OWN terminal-task predicate (coding-runner/runner.py task_list): a task is finished
# when its status is complete/cancelled OR its worktree has been merged/discarded. Mirrored exactly
# here — one derivation, never a parallel guess — so the two definitions cannot drift. In particular
# a job finished the normal way (task_finish merge/discard) keeps status "active" and only sets
# worktree_finished, so a status-only check would wrongly read it as live.
_TERMINAL_STATES = ("complete", "cancelled")


def _task_state(task_id):
    """Classify an escalation's underlying task as ('live'|'dead'|'unknown', objective).

    'dead' is returned ONLY on positive evidence that the runner considers the task finished. Any
    uncertainty — no task_id, runner unreachable, task record gone, unexpected shape — returns
    'unknown', so the caller fails toward SHOWING the escalation. Hiding a LIVE escalation (a real
    blocker the human never sees) is the dangerous failure; showing a stale one occasionally is not.
    """
    if not task_id:
        return "unknown", ""
    try:
        rec = coding_client.task("get", task_id=task_id)
    except Exception:
        return "unknown", ""
    if not isinstance(rec, dict) or rec.get("error"):
        return "unknown", ""
    status = str(rec.get("status") or "").lower()
    objective = str(rec.get("objective") or "")
    if status in _TERMINAL_STATES or bool(rec.get("worktree_finished")):
        return "dead", objective
    return "live", objective


def _humanize_age(created, now=None):
    """A plain relative age ('moments ago' / 'N minutes' / 'N hours' / 'N days') from an ISO
    timestamp. `now` is injectable for tests. Returns '' if the timestamp is unparseable."""
    if not created:
        return "just now"
    try:
        dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        secs = ((now or datetime.now(timezone.utc)) - dt).total_seconds()
    except Exception:
        return ""
    if secs < 90:
        return "moments ago"
    mins = secs / 60.0
    if mins < 90:
        return "%d minutes" % round(mins)
    hours = mins / 60.0
    if hours < 36:
        return "%d hours" % round(hours)
    return "%d days" % round(hours / 24.0)


def _human_dm(pkt, now=None):
    """Build a human-legible escalation DM: what the job was doing, why it stopped, how long it has
    waited, and how to respond — never a raw reason code or the agent's internal notes."""
    reason = pkt.get("reason") or ""
    plain = _REASON_PLAIN.get(reason, "hit a problem it can't resolve on its own")
    objective = (pkt.get("objective") or "").strip()
    what = objective[:140] if objective else "a coding job"
    age = _humanize_age(pkt.get("created"), now=now)
    waited = (" (waiting %s)" % age) if age else ""
    return "\n".join([
        "\U0001F6A7 A HAI coding job is stuck and needs your call%s." % waited,
        "• What it was doing: %s" % what,
        "• Why it stopped: it %s." % plain,
        "• To resolve: tell me or HAI to approve it or dismiss it (or open the dashboard).",
    ])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_lock = threading.Lock()


def _retire(ids, reason="auto:stale-retired"):
    """Auto-ack (locally) a batch of stale escalation ids so they are never listed or DM'd again."""
    if not ids:
        return
    with _lock:
        acked = _load_acked()
        for i in ids:
            acked[i] = reason
        _save_acked(acked)


def pending() -> dict:
    """Return undecided, LIVE escalation packets newest first, minus locally acked ones.

    Stale packets — those whose underlying task is dead (cancelled/complete/finished) — are
    auto-retired here and excluded, so a human never sees an escalation for a job that is over.
    Returns a dict with key ``packets`` (list) and optionally ``error`` when the runner is
    unreachable. Never raises.
    """
    try:
        result = coding_client.escalation("list")
    except Exception as exc:
        return {"packets": [], "error": f"coding runner unavailable: {exc}"}

    if isinstance(result, dict) and result.get("error"):
        return {"packets": [], "error": result["error"]}

    all_packets = result.get("escalations", [])

    acked = _load_acked()
    undecided = []
    stale = []
    for pkt in all_packets:
        pid = pkt.get("id")
        status = pkt.get("status")
        # Skip resolved/returned/cancelled packets and anything we already acked
        if status not in ("ready",):
            continue
        if pid in acked:
            continue
        # Retire the escalation if the job behind it is dead — the human never sees a stale one.
        state, objective = _task_state(pkt.get("task_id"))
        if state == "dead":
            stale.append(pid)
            continue
        undecided.append({
            "id": pid,
            "task_id": pkt.get("task_id"),
            "workspace": pkt.get("workspace"),
            "reason": pkt.get("reason"),
            "requested_help": pkt.get("requested_help"),
            "objective": objective,            # human context (what the job was doing)
            "created": pkt.get("created_at"),
        })

    _retire(stale)

    # Sort newest first by created_at
    undecided.sort(key=lambda p: p.get("created", ""), reverse=True)
    return {"packets": undecided}


def get(eid: str) -> dict:
    """Return a single escalation packet by id.

    Returns the full packet dict or an error dict.  Never raises.
    """
    try:
        result = coding_client.escalation("get", packet_id=eid)
    except Exception as exc:
        return {"error": f"coding runner unavailable: {exc}"}

    if isinstance(result, dict) and result.get("error"):
        return result
    return result


def ack(eid: str, user: str = "Guest") -> dict:
    """Mark a packet as seen (local bookkeeping only).

    Returns ``{"ok": True}`` on success.  Double-ack is idempotent.
    If the id was never surfaced by the runner, returns a named error.
    Never raises.
    """
    with _lock:
        acked = _load_acked()

        # Check whether this id exists in the runner at all (otherwise it's unknown)
        try:
            check = coding_client.escalation("get", packet_id=eid)
        except Exception:
            # Runner down – still allow ack so the id is bookmarked locally
            pass
        else:
            if isinstance(check, dict) and check.get("error"):
                return {"error": f"unknown escalation id: {eid}"}

        acked[eid] = user
        _save_acked(acked)

    return {"ok": True, "id": eid, "user": user}


def notify_new(packets: list[dict], seen_ids: set[str]) -> tuple[list[dict], list[str]]:
    """Pure function: given candidate packets and previously-seen ids, return new ones + DM text.

    Each DM is HUMAN-LEGIBLE (see ``_human_dm``): what the job was doing, why it stopped, how long
    it waited, and how to respond — never a raw reason code or the agent's internal notes. Callers
    pass the LIVE packets from ``pending()`` (already stale-filtered), so a dead job never DMs.

    ``seen_ids`` must be a mutable set that this function updates with the ids it surfaces, so
    repeated calls do not re-notify.
    """
    new_packets = []
    dm_texts = []
    for pkt in packets:
        pid = pkt.get("id")
        if pid in seen_ids:
            continue
        new_packets.append(pkt)
        dm_texts.append(_human_dm(pkt))
        seen_ids.add(pid)
    return new_packets, dm_texts
