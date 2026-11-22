"""Per-user conversation transcripts and rolling summaries.

File layout (per user):
    State/history/<user>/<chat_id>.json            transcript
    State/history/<user>/<chat_id>.summary.json    compacted summary
    State/history/<user>/index.json                chat index and active ID

Legacy single-thread files auto-migrate to chat id 'default' on first access.
Passing chat=None resolves to the user's currently active chat, preserving
backward compatibility for older callers. User memory (usermemory.py) remains
global per user, distinct from conversation-specific history."""
import datetime
import json
import pathlib
import re
import threading
import uuid

import config

# Serializes read-modify-write cycles for transcripts to prevent lost updates when appending or rewriting attachments concurrently.
_WRITE_LOCK = threading.RLock()


def _index_writer(fn):
    """A user's chat index is one file that several writers read, change and save: every one of them holds
    _WRITE_LOCK for the whole read-change-save, or a writer saving an older copy loses another's change (a chat
    created while a turn is being saved vanished from the index). The lock is reentrant, so writers may nest."""
    import functools

    @functools.wraps(fn)
    def locked(*a, **kw):
        with _WRITE_LOCK:
            return fn(*a, **kw)
    return locked

HISTORY_DIR = pathlib.Path(config.STATE_DIR) / "history"


def _safe_user(user):
    return re.sub(r"[^A-Za-z0-9_-]", "_", user or "Guest")


def _safe_chat(chat):
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(chat or ""))[:40]


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _user_dir(user):
    return HISTORY_DIR / _safe_user(user)


def _index_path(user):
    return _user_dir(user) / "index.json"


def _chat_path_raw(user, chat_id):
    return _user_dir(user) / f"{_safe_chat(chat_id)}.json"


def chat_upload_root(user, chat_id):
    """Storage location for conversation-only uploads: user's history dir, one folder per chat. Bytes are kept by `ingest.save_received` in `.uploads/`. Chat ID is sanitized."""
    return _user_dir(user) / "chat-uploads" / _safe_chat(chat_id)


def rewrite_attachment(user, chat_id, upload_id, replacement):
    """Replace every `[Attached: <name> (upload <upload_id>)…]` line in this chat's transcript with `replacement`, in
    each message's shown text AND its sent text (append-stable replay) -> how many were replaced. Used when an
    archived chat's photo is replaced by its description (photo_archive). Only this user's chat; under _WRITE_LOCK."""
    pat = re.compile(r"\[Attached: [^\[\]]*?\(upload " + re.escape(str(upload_id)) + r"\)[^\[\]]*\]")
    with _WRITE_LOCK:
        chat_id = _safe_chat(chat_id)
        if not any(c["id"] == chat_id for c in _load_index(user)["chats"]):
            return 0
        path = _chat_path_raw(user, chat_id)
        msgs = load(user, chat_id)
        n = 0
        for m in msgs:
            for key in ("content", "sent"):
                if isinstance(m.get(key), str) and pat.search(m[key]):
                    m[key], k = pat.subn(lambda _m: replacement, m[key])
                    n += k
        if n:
            _write(path, msgs)
        return n


def chat_upload_roots(user, chat_id):
    """This chat's upload root, then the root of each chat it was BRANCHED from (a branch carries its transcript, so
    it carries the attachments that transcript names). This user's index only; a cycle cannot loop."""
    index = _load_index(user)
    roots, seen, cur = [], set(), _safe_chat(chat_id)
    while cur and cur not in seen:
        seen.add(cur)
        roots.append(chat_upload_root(user, cur))
        entry = next((c for c in index["chats"] if c["id"] == cur), None)
        cur = _safe_chat(((entry or {}).get("branched_from") or {}).get("chat"))
    return roots


def _summary_path_raw(user, chat_id):
    return _user_dir(user) / f"{_safe_chat(chat_id)}.summary.json"


def _legacy_path(user):
    return HISTORY_DIR / f"{_safe_user(user)}.json"


def _legacy_summary_path(user):
    return HISTORY_DIR / f"{_safe_user(user)}.summary.json"


def _legacy_safe_path(user):
    # Retained as a named helper for old call sites/backups that may import it.
    return _legacy_path(user)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _migrate(user):
    """One-time move of the legacy single thread into chat 'default'. Idempotent: keyed on the
    index file existing. A failed file move degrades to an empty default chat and LEAVES the
    legacy file on disk for hand recovery — migration must never destroy history."""
    if _index_path(user).exists():
        return
    _user_dir(user).mkdir(parents=True, exist_ok=True)
    try:
        if _legacy_path(user).exists():
            _legacy_path(user).replace(_chat_path_raw(user, "default"))
        if _legacy_summary_path(user).exists():
            _legacy_summary_path(user).replace(_summary_path_raw(user, "default"))
    except Exception:
        pass
    entry = {"id": "default", "title": "", "created_at": _now_iso(), "updated_at": _now_iso()}
    _write(_index_path(user), {"chats": [entry], "active": "default"})


def _load_index(user):
    _migrate(user)
    try:
        d = json.loads(_index_path(user).read_text(encoding="utf-8"))
        chats = [c for c in (d.get("chats") or []) if isinstance(c, dict) and c.get("id")]
        active = str(d.get("active") or "")
    except Exception:
        chats, active = [], ""
    if not chats:
        chats = [{"id": "default", "title": "", "created_at": _now_iso(), "updated_at": _now_iso()}]
    if active not in {c["id"] for c in chats}:
        active = chats[0]["id"]
    return {"chats": chats, "active": active}


def _save_index(user, index):
    _write(_index_path(user), index)


def resolve_chat(user, chat=None):
    """A VALID chat id for this user: the requested one when it exists, else the active one.
    Never raises, never leaks another user's ids (lookup is inside this user's index only)."""
    index = _load_index(user)
    chat = _safe_chat(chat)
    if chat and any(c["id"] == chat for c in index["chats"]):
        return chat
    return index["active"]


def machine_chat_error(user, chat_id):
    """Refusal message if a MACHINE user's packet targets a non-existent chat; else None.

    Machine users must explicitly name an existing chat. They are denied the active-chat
    fallback to prevent packets from leaking into other users' live chats.
    The caller exempts ephemeral one-shots."""
    if user not in config.MACHINE_USERS:
        return None
    cid = str(chat_id or "")
    if not cid or _safe_chat(cid) != cid or resolve_chat(user, cid) != cid:
        return ("machine user %r must target an existing chat_id explicitly (got %r); refusing "
                "rather than falling back to the active chat. List its threads: "
                "GET /chats?user=%s" % (user, chat_id, user))
    return None


def title_clip(text):
    """The canonical raw-title clip (shared by _touch and the auto-titler's placeholder check)."""
    return re.sub(r"\s+", " ", str(text or "")).strip()[:60]


@_index_writer
def _touch(user, chat_id, first_user_text=None):
    """Bump updated_at; set the title from the first user message while the title is empty.
    (That raw title is a PLACEHOLDER — the auto-titler upgrades it moments later, #18.)"""
    index = _load_index(user)
    for c in index["chats"]:
        if c["id"] == chat_id:
            c["updated_at"] = _now_iso()
            if first_user_text and not c.get("title"):
                c["title"] = title_clip(first_user_text)
    _save_index(user, index)


@_index_writer
def set_title_if_unchanged(user, chat_id, new_title, only_if_current=None):
    """Set a chat's title ONLY while the current title still equals only_if_current — the
    auto-titler's guard so a manual rename that lands first always wins. Returns True when set."""
    index = _load_index(user)
    chat_id = _safe_chat(chat_id)
    new_title = title_clip(new_title)
    if not new_title:
        return False
    for c in index["chats"]:
        if c["id"] == chat_id:
            if only_if_current is not None and (c.get("title") or "") != only_if_current:
                return False
            c["title"] = new_title
            _save_index(user, index)
            return True
    return False


# --- multi-chat public API (Project #18 P1) -----------------------------------------------------

def list_chats(user, include_archived=False):
    """{"chats": [...most-recent-first...], "active": id} for the switcher UI. Archived chats are
    hidden unless asked for (P2) — EXCEPT the active one, which always shows (a user viewing an
    archived chat must see where they are)."""
    index = _load_index(user)
    chats = [c for c in index["chats"]
             if include_archived or not c.get("archived") or c["id"] == index["active"]]
    chats = sorted(chats, key=lambda c: str(c.get("updated_at") or ""), reverse=True)
    return {"chats": chats, "active": index["active"]}


@_index_writer
def new_chat(user):
    """Create an empty chat, make it active, return its entry. Never touches other chats —
    'New chat' preserving the old thread is the whole point of #18."""
    index = _load_index(user)
    entry = {"id": uuid.uuid4().hex[:12], "title": "", "created_at": _now_iso(),
             "updated_at": _now_iso()}
    index["chats"].append(entry)
    index["active"] = entry["id"]
    _save_index(user, index)
    return entry


@_index_writer
def branch_chat(user, chat_id, exchanges, check=None):
    """Branch a conversation from a specific answer to create a new active chat.

    The new chat contains the source transcript up to the `exchanges`-th answer (plus any compacted summaries). The source chat remains untouched.

    `check` is used to resolve the answer index if `exchanges` is ambiguous or incorrect; it matches the start of the answer the user clicked. Returns None if the chat or target answer does not exist."""
    index = _load_index(user)
    chat_id = _safe_chat(chat_id)
    src = next((c for c in index["chats"] if c["id"] == chat_id), None)
    try:
        exchanges = int(exchanges)
    except (TypeError, ValueError):
        return None
    if src is None or exchanges < 1:
        return None
    msgs = load(user, chat_id)
    answers = [i for i, m in enumerate(msgs) if m.get("role") == "assistant"]
    def _starts(i):
        return not check or str(msgs[i].get("content") or "").strip().startswith(str(check).strip())
    if exchanges <= len(answers) and _starts(answers[exchanges - 1]):
        upto = answers[exchanges - 1] + 1
    else:
        hits = [i for i in answers if check and _starts(i)]
        if not hits:
            return None
        upto, exchanges = hits[-1] + 1, answers.index(hits[-1]) + 1
    summary = load_summary(user, chat_id)
    summary["covered_messages"] = min(summary["covered_messages"], upto)
    entry = new_chat(user)
    _write(_chat_path_raw(user, entry["id"]), msgs[:upto])
    _write(_summary_path_raw(user, entry["id"]), summary)
    index = _load_index(user)
    for c in index["chats"]:
        if c["id"] == entry["id"]:
            c["title"] = title_clip("⑂ " + (src.get("title") or "chat"))
            c["branched_from"] = {"chat": chat_id, "exchanges": exchanges}
            entry = c
    _save_index(user, index)
    return entry


@_index_writer
def switch_chat(user, chat_id):
    """Make chat_id active; None when it doesn't exist (caller 404s)."""
    index = _load_index(user)
    chat_id = _safe_chat(chat_id)
    if not any(c["id"] == chat_id for c in index["chats"]):
        return None
    index["active"] = chat_id
    _save_index(user, index)
    return chat_id


# --- #18 Phase 2: rename · archive · soft-delete ------------------------------------------------

@_index_writer
def rename_chat(user, chat_id, title):
    """Set a chat's title (whitespace-collapsed, 60 chars). None when the chat doesn't exist."""
    index = _load_index(user)
    chat_id = _safe_chat(chat_id)
    title = re.sub(r"\s+", " ", str(title or "")).strip()[:60]
    for c in index["chats"]:
        if c["id"] == chat_id:
            c["title"] = title
            c["updated_at"] = _now_iso()
            _save_index(user, index)
            return c
    return None


def _reassign_active(index, leaving_id):
    """Point 'active' at the most recent non-archived chat that isn't leaving_id, creating a fresh
    one when none remains — the user must always land somewhere usable."""
    candidates = [c for c in index["chats"]
                  if c["id"] != leaving_id and not c.get("archived")]
    if candidates:
        candidates.sort(key=lambda c: str(c.get("updated_at") or ""), reverse=True)
        index["active"] = candidates[0]["id"]
    else:
        entry = {"id": uuid.uuid4().hex[:12], "title": "", "created_at": _now_iso(),
                 "updated_at": _now_iso()}
        index["chats"].append(entry)
        index["active"] = entry["id"]


@_index_writer
def archive_chat(user, chat_id, archived=True):
    """Flag/unflag a chat archived (hidden from the default switcher list, content untouched).
    Archiving the ACTIVE chat hands active to the most recent live one (or a fresh chat).
    None when the chat doesn't exist."""
    index = _load_index(user)
    chat_id = _safe_chat(chat_id)
    for c in index["chats"]:
        if c["id"] == chat_id:
            c["archived"] = bool(archived)
            c["updated_at"] = _now_iso()
            if archived and index["active"] == chat_id:
                _reassign_active(index, chat_id)
            _save_index(user, index)
            return c
    return None


@_index_writer
def delete_chat(user, chat_id):
    """Soft-delete: move files to <user>/.trash/ and remove from index.

    Nothing is hard-deleted; recovery requires moving files back and
    re-adding the index entry. Returns None if chat does not exist."""
    index = _load_index(user)
    chat_id = _safe_chat(chat_id)
    if not any(c["id"] == chat_id for c in index["chats"]):
        return None
    trash = _user_dir(user) / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    stamp = _now_iso().replace(":", "-")
    for f, suffix in ((_chat_path_raw(user, chat_id), ".json"),
                      (_summary_path_raw(user, chat_id), ".summary.json"),
                      (_pending_path_raw(user, chat_id), ".pending.json")):
        if f.exists():
            try:
                f.replace(trash / f"{chat_id}.{stamp}{suffix}")
            except Exception:
                pass
    uploads = chat_upload_root(user, chat_id)      # a conversation-only upload goes to the trash with its chat
    if uploads.exists():
        try:
            uploads.replace(trash / f"{chat_id}.{stamp}.uploads")
        except Exception:
            pass
    index["chats"] = [c for c in index["chats"] if c["id"] != chat_id]
    if index["active"] == chat_id or not index["chats"]:
        _reassign_active(index, chat_id)
    _save_index(user, index)
    return chat_id



def chat_pins(user, chat=None):
    """The pinned doc sources for a chat (empty list when none)."""
    chat_id = resolve_chat(user, chat)
    for c in _load_index(user)["chats"]:
        if c["id"] == chat_id:
            return [str(p) for p in (c.get("pins") or [])]
    return []


@_index_writer
def pin_doc(user, chat, source, pinned=True):
    """Toggle a doc source on a chat's pin list (cap 10 — pins are FOCUS, not a second library).
    Returns the updated pin list, or None when the chat doesn't exist."""
    index = _load_index(user)
    chat_id = resolve_chat(user, chat)
    source = str(source or "").strip()[:200]
    if not source:
        return None
    for c in index["chats"]:
        if c["id"] == chat_id:
            pins = [str(p) for p in (c.get("pins") or [])]
            if pinned and source not in pins and len(pins) < 10:
                pins.append(source)
            if not pinned and source in pins:
                pins.remove(source)
            c["pins"] = pins
            c["updated_at"] = _now_iso()
            _save_index(user, index)
            return pins
    return None


# --- #18 Phase 3: search across own chats -------------------------------------------------------

def search_chats(user, query, limit=20):
    """Case-insensitive substring search across THIS user's chats (archived included, trash
    excluded). Most-recently-updated chats first: [{chat_id, title, archived, updated_at,
    matches: [{role, snippet}]}], ±60-char snippets, ≤3 matches per chat, ≤limit chats. Plain
    text by design — conversations need findability, not semantics (the RAG machinery is for
    documents)."""
    q = str(query or "").strip().lower()
    if not q:
        return []
    out = []
    index = _load_index(user)
    chats = sorted(index["chats"], key=lambda c: str(c.get("updated_at") or ""), reverse=True)
    for c in chats:
        msgs = load(user, c["id"])
        matches = []
        for m in msgs:
            content = str(m.get("content") or "")
            i = content.lower().find(q)
            if i < 0:
                continue
            start = max(0, i - 60)
            end = i + len(q) + 60
            snippet = (("…" if start else "") + content[start:end]
                       + ("…" if end < len(content) else ""))
            matches.append({"role": m.get("role"), "snippet": snippet})
            if len(matches) >= 3:
                break
        if matches:
            out.append({"chat_id": c["id"], "title": c.get("title") or "",
                        "archived": bool(c.get("archived")),
                        "updated_at": c.get("updated_at"), "matches": matches})
            if len(out) >= max(1, int(limit)):
                break
    return out


# --- transcript + summary (pre-#18 signatures, now chat-aware; chat=None = active chat) ---------

def _default_summary():
    return {"summary": "", "covered_messages": 0, "compactions": 0}


def load_summary(user, chat=None):
    f = _summary_path_raw(user, resolve_chat(user, chat))
    if not f.exists():
        return _default_summary()
    try:
        state = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return _default_summary()
        return {
            "summary": str(state.get("summary") or ""),
            "covered_messages": max(0, int(state.get("covered_messages") or 0)),
            "compactions": max(0, int(state.get("compactions") or 0)),
        }
    except Exception:
        return _default_summary()


def _trim(msgs, summary):
    max_messages = max(config.HISTORY_MAX_MESSAGES, config.CONTEXT_RECENT_MESSAGES + 2)
    if len(msgs) <= max_messages:
        return msgs, summary
    drop = len(msgs) - max_messages
    # Only discard transcript entries already represented by the durable summary.
    safe_drop = min(drop, summary["covered_messages"])
    if safe_drop:
        msgs = msgs[safe_drop:]
        summary["covered_messages"] -= safe_drop
    return msgs, summary


def save_summary(user, summary_text, covered_messages, chat=None):
    chat_id = resolve_chat(user, chat)
    old = load_summary(user, chat_id)
    state = {
        "summary": summary_text.strip(),
        "covered_messages": max(0, int(covered_messages)),
        "compactions": old["compactions"] + 1,
    }
    _write(_summary_path_raw(user, chat_id), state)
    msgs, state = _trim(load(user, chat_id), state)
    _write(_chat_path_raw(user, chat_id), msgs)
    _write(_summary_path_raw(user, chat_id), state)
    return state


def context_state(user, chat=None):
    chat_id = resolve_chat(user, chat)
    msgs = load(user, chat_id)
    summary = load_summary(user, chat_id)
    summary["covered_messages"] = min(summary["covered_messages"], len(msgs))
    uncovered = msgs[summary["covered_messages"]:]
    chars = len(summary["summary"]) + sum(len(m.get("content", "")) for m in uncovered)
    return {
        **summary,
        "messages": len(msgs),
        "uncovered_messages": len(uncovered),
        "estimated_tokens": max(1, chars // 4),
    }


def compaction_plan(user, force=False, chat=None, budget=None):
    """Determines what to summarise (or None). `budget` is the character limit for the serving window (defaults to the small brain's limit). Use character count, not message count, to determine fit."""
    chat_id = resolve_chat(user, chat)
    msgs = load(user, chat_id)
    state = load_summary(user, chat_id)
    covered = min(state["covered_messages"], len(msgs))
    keep = max(2, config.CONTEXT_RECENT_MESSAGES)
    cutoff = max(covered, len(msgs) - keep)
    candidates = msgs[covered:cutoff]
    uncovered = msgs[covered:]
    chars = len(state["summary"]) + sum(len(m.get("sent") or m.get("content", "")) for m in uncovered)
    should = bool(candidates) and (force or chars >= (budget or config.CONTEXT_CHAR_BUDGET))
    if not should:
        return None
    total = 0
    bounded = []
    for message in candidates:
        size = len(message.get("content", ""))
        if bounded and total + size > config.COMPACT_SOURCE_CHAR_LIMIT:
            break
        bounded.append(message)
        total += size
    return {
        "previous_summary": state["summary"],
        "messages": bounded,
        "covered_messages": covered + len(bounded),
        "remaining_messages": len(candidates) - len(bounded),
    }


def context_messages(user, chat=None, budget=None):
    chat_id = resolve_chat(user, chat)
    msgs = load(user, chat_id)
    state = load_summary(user, chat_id)
    covered = min(state["covered_messages"], len(msgs))
    recent = msgs[covered:]
    char_budget = max(2000, (budget or config.CONTEXT_CHAR_BUDGET) - len(state["summary"]))
    selected = []
    used = 0
    for message in reversed(recent):
        size = len(message.get("sent") or message.get("content", ""))   # what the prompt will actually carry
        if selected and used + size > char_budget:
            break
        selected.append(message)
        used += size
    selected.reverse()
    return state["summary"], selected


def load(user, chat=None):
    f = _chat_path_raw(user, resolve_chat(user, chat))
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []


def append(user, user_text, assistant_text, chat=None, extras=None, user_sent=None):
    """Merge `extras` dict onto the assistant message for persistence.

    These fields are stored in history but excluded from prompt context
    assembly (which uses role/content only), preventing them from reaching
    the model. `user_sent` = what the model was actually sent for this turn (the text plus its notes): kept as the
    user message's `sent` so the next turn replays it exactly (append-stable chat); `content` stays the plain text
    the transcript shows."""
    if not assistant_text:
        return load(user, chat)
    with _WRITE_LOCK:
        return _append_locked(user, user_text, assistant_text, chat, extras, user_sent)


def _append_locked(user, user_text, assistant_text, chat, extras, user_sent):
    chat_id = resolve_chat(user, chat)
    msgs = load(user, chat_id)
    first = not msgs
    user_msg = {"role": "user", "content": user_text}
    if user_sent and user_sent != user_text:
        user_msg["sent"] = user_sent
    msgs.append(user_msg)
    assistant_msg = {"role": "assistant", "content": assistant_text}
    if isinstance(extras, dict):
        for k, v in extras.items():
            if v and k not in ("role", "content"):
                assistant_msg[k] = v
    msgs.append(assistant_msg)
    try:
        summary = load_summary(user, chat_id)
        msgs, summary = _trim(msgs, summary)
        _write(_chat_path_raw(user, chat_id), msgs)
        _write(_summary_path_raw(user, chat_id), summary)
        _touch(user, chat_id, first_user_text=user_text if first else None)
    except Exception:
        pass
    return msgs


# Pending turns live in their own file (not the transcript) to avoid duplicating the user message in context_messages(); end_turn() removes the row on all completion routes.

def _pending_path_raw(user, chat_id):
    return _user_dir(user) / f"{_safe_chat(chat_id)}.pending.json"


def pending(user, chat=None):
    """This chat's accepted-but-unanswered user messages, oldest first: [{cancel_id, content, at}]."""
    f = _pending_path_raw(user, resolve_chat(user, chat))
    if not f.exists():
        return []
    try:
        rows = json.loads(f.read_text(encoding="utf-8"))
        return [r for r in rows if isinstance(r, dict) and r.get("cancel_id")]
    except Exception:
        return []


def begin_turn(user, chat, text, cancel_id, live_ids=()):
    """Record an accepted user message and return the chat id it belongs to. A new chat gets its
    placeholder title now (the auto-titler upgrades it when the answer lands). Rows whose run is no
    longer live (`live_ids` = runs still working) are runs the process lost; they are dropped here,
    once the conversation moves on."""
    chat_id = resolve_chat(user, chat)
    keep = set(live_ids or ())
    rows = [r for r in pending(user, chat_id) if r["cancel_id"] in keep]
    rows.append({"cancel_id": cancel_id, "content": text, "at": _now_iso()})
    _write(_pending_path_raw(user, chat_id), rows)
    _touch(user, chat_id, first_user_text=text if not load(user, chat_id) else None)
    return chat_id


def add_pending(user, chat, text, cancel_id):
    """A message the user sent to a RUNNING turn (#37: talk to it while it works): shown on every open page at once,
    like the turn's own message, and removed with it by end_turn (the answer then carries it as `interjections`)."""
    chat_id = resolve_chat(user, chat)
    rows = pending(user, chat_id)
    rows.append({"cancel_id": cancel_id, "content": text, "at": _now_iso(), "interjection": True})
    _write(_pending_path_raw(user, chat_id), rows)
    _touch(user, chat_id)
    return chat_id


def end_turn(user, chat, cancel_id):
    """Remove one pending row (its run has ended by any route). Never raises."""
    try:
        chat_id = resolve_chat(user, chat)
        f = _pending_path_raw(user, chat_id)
        rows = [r for r in pending(user, chat_id) if r["cancel_id"] != cancel_id]
        if rows:
            _write(f, rows)
        elif f.exists():
            f.unlink()
    except Exception:
        pass


def refused_context(user, chat=None):
    """Context for reconsidering a held answer: the user's question, the withheld draft, and the
    findings that held it, from the most recent un-overridden refused message. None when there is
    nothing to override."""
    chat_id = resolve_chat(user, chat)
    msgs = load(user, chat_id)
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if m.get("role") == "assistant" and m.get("refused_draft") and not m.get("overridden"):
            q = msgs[i - 1].get("content", "") if i > 0 and msgs[i - 1].get("role") == "user" else ""
            return {"question": q, "draft": m.get("refused_draft"),
                    "findings": m.get("unresolved_findings") or []}
    return None


def apply_override(user, answer, reason=None, chat=None):
    """Record an override of a tiered-refusal answer: replace the refusal with `answer` (the
    reconsidered answer), mark it overridden, and record the user's reason. Returns True when a
    refused message was found and updated, else False."""
    chat_id = resolve_chat(user, chat)
    msgs = load(user, chat_id)
    target = None
    for m in reversed(msgs):
        if m.get("role") == "assistant" and m.get("refused_draft") and not m.get("overridden"):
            target = m
            break
    if target is None:
        return False
    target["content"] = answer
    target["overridden"] = True
    if reason:
        target["override_reason"] = str(reason)[:2000]
    try:
        _write(_chat_path_raw(user, chat_id), msgs)
    except Exception:
        pass
    return True


@_index_writer
def reset(user, chat=None):
    """Clear ONE chat's content (the active one by default). The chat keeps its id and place in
    the index; its title clears so the next message retitles it. Pre-#18 this wiped the user's
    only thread — same visible behavior, but other chats are now never collateral."""
    chat_id = resolve_chat(user, chat)
    for f in (_chat_path_raw(user, chat_id), _summary_path_raw(user, chat_id), _pending_path_raw(user, chat_id)):
        if not f.exists():
            continue
        try:
            f.unlink()
        except Exception:
            pass
    try:
        index = _load_index(user)
        for c in index["chats"]:
            if c["id"] == chat_id:
                c["title"] = ""
                c["updated_at"] = _now_iso()
        _save_index(user, index)
    except Exception:
        pass
