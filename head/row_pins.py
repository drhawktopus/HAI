"""Exact model-and-box pins allow admins and power users to override routing logic.

Setting methods (priority order):
1. Per-message: /on <box>[:<model words>] (parsed via parse_slash).
2. Per-chat: Stored in <STATE_DIR>/user-prefs/<user>.chatpins.json as {chat_id: row_id}.
3. Per-account: Stored in Q.pin_row (the selected menu row).

Behavior:
Pins skip automatic routing and weighing; they do not escalate or reroute.
If the target box is busy, the system waits and notifies the user.

Authorization:
Only config.ADMIN_USERS and config.POWER_USERS may set pins."""
import json
import os
import re

import config
import scheduler

PIN_FAMILIES = ("chat-c1", "work-w1", "coding-f1")
LABELS = scheduler.roster_labels()          # every box a pin may name, from the roster, in its order
_SHORT = ((r"flash-next", "Flash-Next"), (r"qwen3\.8:27b", "Qwen 3.8 27B"), (r"35b-a3b", "35B-A3B"),
          (r"qwen3-coder:30b", "Qwen3 Coder 30B"), (r"nemotron-3-nano:4b", "Nemotron Nano 4B"),
          (r"nemotron-3-nano:30b", "Nemotron Nano 30B"),
          (r"qwen3\.5:9b", "Qwen 3.5 9B"), (r"qwen3:8b", "Qwen3 8B"))


def allowed(user):
    """Only admins and power users may pin a box."""
    u = str(user or "")
    return bool(u) and (u in config.ADMIN_USERS or u in getattr(config, "POWER_USERS", set()))


def short(model):
    m = str(model or "")
    for pat, name in _SHORT:
        if re.search(pat, m, re.I):
            return name
    return re.sub(r"\.gguf$", "", m)


def _table():
    import scheduler
    return scheduler.policy_data() or {}


def rows(table=None):
    """Every pinnable row: [{id, box, model, think, order, label}] - the crown first, then the families' best-quality
    order, one per (box, model). `order` is a Tolkien box row's own launch fields (Tolkien's chat window), else None."""
    d = table if table is not None else _table()
    out, seen = [], set()

    def add(e, fam=None, tier=None):
        box, model = (e or {}).get("box"), (e or {}).get("model")
        if box not in LABELS or not model or (box, model) in seen:
            return
        seen.add((box, model))
        # family + tier: used by the desk to rank the Tolkien box windows. Required for valid pins; do not omit.
        out.append({"id": "%s/%s" % (box, model), "box": box, "model": model, "think": e.get("think"),
                    "order": e.get("order") or None, "label": "%s · %s" % (short(model), LABELS[box]),
                    "family": e.get("family") or fam, "tier": tier})

    add((d.get("roles") or {}).get("crown"))
    for fam in PIN_FAMILIES:
        for name, tier in (((d.get("families") or {}).get(fam) or {}).get("tiers") or {}).items():
            for e in (tier or {}).get("best_quality") or []:
                add(e, fam, name)
    return out


def resolve(row_id, table=None):
    rid = str(row_id or "").strip()
    return next((r for r in rows(table) if r["id"] == rid), None) if rid else None


_SLASH = re.compile(r"^/on\s+([A-Za-z]+)(?::(\S+))?(?:\s+(.*))?$", re.S)


def parse_slash(text, table=None):
    """`/on <box>[:<model words>] <message>` -> (row or None, the message without the prefix, error or None). Not a
    /on message -> (None, text, None). The box's first row (crown first, then best quality) unless model words name
    another row on that box."""
    m = _SLASH.match(str(text or "").strip())
    if not m:
        return None, text, None
    box, words, rest = m.group(1).lower(), (m.group(2) or "").lower(), (m.group(3) or "").strip()
    if box not in LABELS:
        return None, text, "Unknown box %r - /on takes one of: %s." % (box, ", ".join(LABELS))
    on_box = [r for r in rows(table) if r["box"] == box]
    pick = next((r for r in on_box if words in r["model"].lower() or words in r["label"].lower()), None) \
        if words else (on_box[0] if on_box else None)
    if pick is None:
        return None, text, "No menu row on %s%s." % (LABELS[box], (" matches %r" % words) if words else "")
    if not rest:
        return None, text, "Nothing to send after /on %s." % box
    return pick, rest, None


def _chatpins_path(user):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(user or "Guest"))[:64] or "Guest"
    return os.path.join(config.STATE_DIR, "user-prefs", "%s.chatpins.json" % safe)


def _chatpins(user):
    try:
        with open(_chatpins_path(user), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def chat_pin(user, chat_id):
    return _chatpins(user).get(str(chat_id or "")) if chat_id else None


def set_chat_pin(user, chat_id, row_id):
    """Pin (or with an empty row_id, unpin) one chat. -> the stored row id or None."""
    if not chat_id:
        return None
    d = _chatpins(user)
    if row_id:
        d[str(chat_id)] = str(row_id)
    else:
        d.pop(str(chat_id), None)
    p = _chatpins_path(user)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, p)
    return d.get(str(chat_id))


def for_turn(user, text, pin_row=None, chat_id=None, table=None, once=False):
    """-> (row or None, text to answer, source or None, error or None). /on (this message) > this chat's pin > the
    page's pick. `once`: `pin_row` is a /on row already taken off the message (a resumed turn), so it ranks as the
    message's. A non-admin, non-power user who names a pin is told so, never silently placed elsewhere."""
    row, rest, err = parse_slash(text, table)
    if err or row or (pin_row and not allowed(user)):
        if not allowed(user):
            return None, text, None, "Pinning a box is for admins and power users."
        return row, rest, ("message" if row else None), err

    def _page(source):
        r = resolve(pin_row, table)
        if r is None:
            return None, text, None, "That pinned model and box (%s) is not on the menu any more - pick another." % pin_row
        return r, text, source, None
    if pin_row and once:
        return _page("message")
    if chat_id and allowed(user):
        row = resolve(chat_pin(user, chat_id), table)
        if row:
            return row, text, "chat", None
    return _page("page") if pin_row else (None, text, None, None)
