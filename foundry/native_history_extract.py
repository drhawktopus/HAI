"""Deterministic transcript extraction.

Reads Claude/Codex/HAI history; writes redacted evidence packets to staging.
No model calls; never edits source history.

Modes:
  --mode positive: Extracts positive-language reactions.
  --mode corrective: Extracts corrective reactions with trajectory capture
    (assistant recovery and user followup).

Safety: <cross-session-message> blocks are stripped to prevent misattribution.

Usage:
  python native_history_extract.py --out-dir <dir> [--mode corrective] [seed-locators]"""
import argparse
import base64
import hashlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path

VERSION = "native-history-extract-v3 2026-08-26"  # v3: dedup replayed-uuid candidates
# the assistants' own transcript folders, under the account that runs them
CLAUDE_DEFAULT = os.path.join(os.path.expanduser("~"), ".claude", "projects")
CODEX_DEFAULT = os.path.join(os.path.expanduser("~"), ".codex", "sessions")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foundry_paths as _P  # noqa: E402
HAI_DEFAULT = _P.setting("HAI_HISTORY_DIR", "")       # the head's history folder on this box; empty = none here

POSITIVE_RE = re.compile(
    r"\b(?:thank(?:s| you)?|great|good|nice|perfect|excellent|awesome|wonderful|"
    r"fantastic|beautiful|exactly|correct|right|well[ -]done|love (?:it|that)|"
    r"nailed it|much better|approved|looks good|that works|works for me|proceed|"
    r"go ahead|keep going|continue|ship it|do it|hit it|sounds good|agreed)\b",
    re.IGNORECASE,
)
# High-recall corrective surface: correction/redirection/rejection/constraint/takeover language.
# Deliberately broad -- the semantic review layer decides what is genuinely corrective.
CORRECTIVE_RE = re.compile(
    r"\b(?:no[,.]|nope|stop|wait|hold on|hold up|don'?t|do not|not what i|that'?s (?:not|wrong)|"
    r"incorrect|wrong|instead|actually|undo|revert|roll(?: it)? back|put (?:it|that) back|"
    r"leave (?:it|that)|never mind|nevermind|as i (?:said|told)|i (?:already )?told you|"
    r"like i said|i'?ve (?:already )?said|we already|again[,.]|how many times|remember[,.]? (?:i|we|the)|"
    r"why (?:did|are|would) you|who told you|i didn'?t ask|you (?:were|are) supposed to|"
    r"should(?: not|n'?t)? have|stay (?:in|on)|out of scope|too far|slow down|back up|"
    r"i'?ll (?:do|handle|take) (?:it|this|that)|let me do|hands off|missed the point|"
    r"not the (?:point|goal|plan)|off track|re-?read|check the (?:docs|library|runbook|plan))\b",
    re.IGNORECASE,
)
DROP_BLOCKS = (
    "system-reminder", "local-command-caveat", "command-name", "command-message",
    "environment_context", "app-context", "skills_instructions", "permissions instructions",
    # cross-session messages are not the owner's words; do not treat them as first-person input
    "cross-session-message",
)
SECRET_PATTERNS = (
    (re.compile(r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
                re.IGNORECASE | re.DOTALL), "[REDACTED PRIVATE KEY]"),
    (re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b"), "[REDACTED KEY]"),
    (re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=-]{12,}"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)(\b(?:api[_ -]?key|token|password|passwd|secret)\b\s*[:=]\s*)"
                r"(?:\"[^\"\r\n]+\"|'[^'\r\n]+'|[^\s,;\r\n]{6,})"), r"\1[REDACTED]"),
)

MODES = {
    "positive": {"regex": POSITIVE_RE, "prefix": "native-pos-", "stem": "positive",
                 "purpose": "Native Intelligence reconnaissance Part 1: positive collaboration patterns"},
    "corrective": {"regex": CORRECTIVE_RE, "prefix": "native-cor-", "stem": "corrective",
                   "purpose": "Native Intelligence reconnaissance Part 2: corrections, rejections, recovery"},
}


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, indent=1, ensure_ascii=False, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp), str(path))


def _load_json(path, default=None):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _canonical_hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _redact(text):
    text = str(text or "").replace("\x00", "")
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _clip(text, limit):
    text = _redact(text).strip()
    if len(text) <= limit:
        return text
    head = int(limit * 0.72)
    tail = limit - head
    return text[:head].rstrip() + "\n[...bounded evidence omitted...]\n" + text[-tail:].lstrip()


def _strip_injected_blocks(text):
    text = str(text or "")
    for tag in DROP_BLOCKS:
        text = re.sub(r"<%s(?:\s[^>]*)?>.*?</%s>" % (re.escape(tag), re.escape(tag)),
                      " ", text, flags=re.IGNORECASE | re.DOTALL)
        # handles truncated blocks where the closing tag is missing but the content is still present
        text = re.sub(r"<%s(?:\s[^>]*)?>.*$" % re.escape(tag), " ",
                      text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _content_text(content, wanted=("text", "input_text", "output_text")):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    out = []
    for block in content:
        if isinstance(block, str):
            out.append(block)
        elif isinstance(block, dict) and block.get("type") in wanted:
            value = block.get("text") or block.get("content")
            if isinstance(value, str):
                out.append(value)
    return "\n".join(out)


def _positive_terms(text):
    return sorted({m.group(0).lower() for m in POSITIVE_RE.finditer(text or "")})


def _signal_terms(mode, text):
    return sorted({m.group(0).lower().strip(",.")
                   for m in MODES[mode]["regex"].finditer(text or "")})


def _packet(source, source_file, session_id, timestamp, reaction_line, event_id,
            previous_user, assistant_text, reaction, assistant_lines, tool_names,
            extra_locator=None, mode="positive", recovery_text="", followup_text="",
            recovery_lines=(), seeded=False):
    locator = {
        "source_file": str(source_file),
        "session_id": str(session_id or ""),
        "reaction_line": int(reaction_line),
        "reaction_event_id": str(event_id or ""),
        "assistant_lines": [int(x) for x in assistant_lines],
        "recovery_lines": [int(x) for x in recovery_lines],
    }
    if extra_locator:
        locator.update(extra_locator)
    packet = {
        "source": source,
        "locator": locator,
        "timestamp": str(timestamp or ""),
        "previous_user_request": _clip(previous_user, 8000),
        "assistant_response": _clip(assistant_text, 18000),
        "assistant_tool_names": sorted(set(str(x) for x in tool_names if x)),
        "owner_reaction": _clip(reaction, 8000),
        "assistant_recovery": _clip(recovery_text, 12000),
        "owner_followup": _clip(followup_text, 6000),
    }
    identity = "%s\0%s\0%s\0%s\0%s" % (
        source, source_file, session_id, event_id, reaction_line)
    rid = MODES[mode]["prefix"] + hashlib.sha256(
        identity.encode("utf-8", "replace")).hexdigest()[:20]
    return {
        "id": rid,
        "source": source,
        "locator": locator,
        "timestamp": str(timestamp or ""),
        "signal_terms": _signal_terms(mode, reaction),
        "seeded": bool(seeded),
        "packet_b64": base64.b64encode(
            json.dumps(packet, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).decode("ascii"),
        "control": False,
    }


def _decode_line(raw, stats):
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        stats["malformed_lines"] += 1
        return None


def _is_trigger(mode, text, seeds, source_key, line_no):
    if seeds and (source_key, int(line_no)) in seeds:
        return True, True
    return bool(_signal_terms(mode, text)), False


def _dedup_by_id(candidates):
    """One real message is one candidate.  A message replayed into a resumed transcript is recorded
    twice under the SAME uuid (verified: b7177d66... line 597 and line 4612 are byte-identical), so
    the same reaction can build the same candidate twice -- byte-identical id and packet.  Keep the
    first, drop id repeats, and return how many were dropped so the count is reported, never silent."""
    seen, unique = set(), []
    for c in candidates:
        rid = c.get("id")
        if rid in seen:
            continue
        seen.add(rid)
        unique.append(c)
    return unique, len(candidates) - len(unique)


def _parse_claude(path, mode="positive", seeds=None):
    sha = hashlib.sha256()
    stats = {"lines": 0, "malformed_lines": 0, "human_messages": 0,
             "assistant_messages": 0, "tool_events": 0, "candidates": 0,
             "relay_messages": 0}
    nodes, children, triggers = {}, {}, []
    source_key = str(Path(path).resolve()).lower()
    with open(path, "rb") as f:
        for line_no, raw in enumerate(f, 1):
            sha.update(raw)
            stats["lines"] += 1
            obj = _decode_line(raw, stats)
            if not isinstance(obj, dict):
                continue
            uid = str(obj.get("uuid") or "line-%d" % line_no)
            parent = str(obj.get("parentUuid") or "")
            typ = obj.get("type")
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            role = msg.get("role")
            content = msg.get("content")
            kind, text, tools = "other", "", []
            if typ == "assistant" or role == "assistant":
                kind = "assistant"
                text = _content_text(content, ("text", "output_text"))
                if isinstance(content, list):
                    tools = [b.get("name") for b in content
                             if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name")]
                stats["assistant_messages"] += 1
                stats["tool_events"] += len(tools)
            elif typ == "user" or role == "user":
                raw_text = _content_text(content, ("text", "input_text"))
                if "<cross-session-message" in raw_text:
                    stats["relay_messages"] += 1
                text = _strip_injected_blocks(raw_text)
                if text:
                    kind = "human"
                    stats["human_messages"] += 1
                else:
                    kind = "tool"
                    stats["tool_events"] += 1
            node = {"id": uid, "parent": parent, "kind": kind, "text": text,
                    "tools": tools, "line": line_no, "timestamp": obj.get("timestamp") or "",
                    "session": obj.get("sessionId"), "sidechain": bool(obj.get("isSidechain")),
                    "cwd": str(obj.get("cwd") or "")}
            nodes[uid] = node
            if parent:
                children.setdefault(parent, []).append(uid)
            if kind == "human":
                fired, seeded = _is_trigger(mode, text, seeds, source_key, line_no)
                if fired:
                    triggers.append((uid, seeded))

    candidates = []
    for uid, seeded in triggers:
        node = nodes[uid]
        # backward: the assistant turn being reacted to, and the request before it
        ancestor, assistant_nodes, prior_user, depth = node["parent"], [], None, 0
        while ancestor and depth < 200:
            previous = nodes.get(ancestor)
            if not previous:
                break
            if previous["kind"] == "human":
                prior_user = previous
                break
            if previous["kind"] == "assistant":
                assistant_nodes.append(previous)
            ancestor = previous.get("parent") or ""
            depth += 1
        assistant_nodes.reverse()
        assistant_text = "\n\n".join(n["text"] for n in assistant_nodes if n["text"])
        if not assistant_text:
            continue
        # forward: the assistant's response to this reaction (recovery) and the next human message
        recovery_nodes, followup, cursor, depth = [], None, uid, 0
        while depth < 200:
            kids = children.get(cursor) or []
            if not kids:
                break
            nxt = nodes[min(kids, key=lambda k: nodes[k]["line"])]
            if nxt["kind"] == "human":
                followup = nxt
                break
            if nxt["kind"] == "assistant":
                recovery_nodes.append(nxt)
            cursor = nxt["id"]
            depth += 1
        recovery_text = "\n\n".join(n["text"] for n in recovery_nodes if n["text"])
        candidates.append(_packet(
            "claude", path, node.get("session"), node["timestamp"], node["line"], uid,
            prior_user.get("text") if prior_user else "", assistant_text, node["text"],
            [n["line"] for n in assistant_nodes],
            [t for n in assistant_nodes for t in n["tools"]],
            {"parent_uuid": node["parent"], "is_sidechain": node["sidechain"],
             "cwd": node["cwd"]},
            mode=mode, recovery_text=recovery_text,
            followup_text=followup["text"] if followup else "",
            recovery_lines=[n["line"] for n in recovery_nodes], seeded=seeded,
        ))
    candidates, _dup = _dedup_by_id(candidates)
    stats["candidates"] = len(candidates)
    stats["duplicate_candidates"] = _dup
    return {"sha256": sha.hexdigest(), "stats": stats, "candidates": candidates}


def _codex_is_direct(path):
    try:
        with open(path, "rb") as f:
            for raw in f:
                obj = json.loads(raw.decode("utf-8", "replace"))
                if obj.get("type") == "session_meta":
                    source = (obj.get("payload") or {}).get("source")
                    return not isinstance(source, dict), source
    except Exception as e:
        return False, "unreadable metadata: %r" % e
    return False, "missing session_meta"


def _emit_from_turns(turns, source, path, session_id, mode, seeds, extra_locator_fn):
    """Turn list -> candidates with trajectory: turn i's reaction pairs with turn i+1's
    assistant block (recovery) and turn i+1's user text (followup)."""
    source_key = str(Path(path).resolve()).lower()
    candidates = []
    for i, turn in enumerate(turns):
        if not turn["assistant_text"]:
            continue
        fired, seeded = _is_trigger(mode, turn["user_text"], seeds, source_key, turn["line"])
        if not fired:
            continue
        nxt = turns[i + 1] if i + 1 < len(turns) else None
        candidates.append(_packet(
            source, path, session_id, turn["timestamp"], turn["line"], turn["event_id"],
            turn["previous_user"], turn["assistant_text"], turn["user_text"],
            turn["assistant_lines"], turn["tools"], extra_locator_fn(turn),
            mode=mode,
            recovery_text=(nxt or {}).get("assistant_text") or "",
            followup_text=(nxt or {}).get("user_text") or "",
            recovery_lines=(nxt or {}).get("assistant_lines") or [], seeded=seeded,
        ))
    return candidates


def _parse_codex(path, mode="positive", seeds=None):
    sha = hashlib.sha256()
    stats = {"lines": 0, "malformed_lines": 0, "human_messages": 0,
             "assistant_messages": 0, "tool_events": 0, "candidates": 0,
             "relay_messages": 0}
    session_id, cwd, current_turn = "", "", ""
    previous_user = None
    assistant_chunks, assistant_lines, tool_names = [], [], []
    last_user_key = None
    turns = []
    with open(path, "rb") as f:
        for line_no, raw in enumerate(f, 1):
            sha.update(raw)
            stats["lines"] += 1
            obj = _decode_line(raw, stats)
            if not isinstance(obj, dict):
                continue
            typ = obj.get("type")
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
            if typ == "session_meta":
                session_id = payload.get("id") or session_id
                cwd = payload.get("cwd") or cwd
                continue
            if typ == "event_msg" and payload.get("type") == "task_started":
                current_turn = payload.get("turn_id") or current_turn
                continue
            if typ != "response_item":
                continue
            ptype, role = payload.get("type"), payload.get("role")
            if ptype == "message" and role == "assistant":
                text = _content_text(payload.get("content"), ("output_text", "text"))
                if text:
                    assistant_chunks.append(text)
                    assistant_lines.append(line_no)
                    stats["assistant_messages"] += 1
                continue
            if ptype in ("function_call", "custom_tool_call", "local_shell_call", "web_search_call"):
                name = payload.get("name") or payload.get("status") or ptype
                tool_names.append(str(name))
                stats["tool_events"] += 1
                continue
            if ptype != "message" or role != "user":
                continue
            raw_text = _content_text(payload.get("content"), ("input_text", "text"))
            if "<cross-session-message" in raw_text:
                stats["relay_messages"] += 1
            text = _strip_injected_blocks(raw_text)
            if not text:
                continue
            user_key = str(payload.get("id") or "") + "\0" + text
            if user_key == last_user_key:
                continue
            last_user_key = user_key
            stats["human_messages"] += 1
            turns.append({"previous_user": previous_user or "", "user_text": text,
                          "line": line_no, "event_id": payload.get("id") or "line-%d" % line_no,
                          "timestamp": obj.get("timestamp"),
                          "assistant_text": "\n\n".join(assistant_chunks),
                          "assistant_lines": list(assistant_lines), "tools": list(tool_names),
                          "turn_id": str(current_turn or "")})
            previous_user = text
            assistant_chunks, assistant_lines, tool_names = [], [], []
    candidates = _emit_from_turns(
        turns, "codex", path, session_id, mode, seeds,
        lambda t: {"turn_id": t["turn_id"], "cwd": str(cwd or "")})
    candidates, _dup = _dedup_by_id(candidates)
    stats["candidates"] = len(candidates)
    stats["duplicate_candidates"] = _dup
    return {"sha256": sha.hexdigest(), "stats": stats, "candidates": candidates}


def _parse_hai(path, mode="positive", seeds=None):
    raw = Path(path).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    stats = {"lines": 0, "malformed_lines": 0, "human_messages": 0,
             "assistant_messages": 0, "tool_events": 0, "candidates": 0,
             "relay_messages": 0}
    try:
        messages = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        stats["malformed_lines"] = 1
        return {"sha256": sha, "stats": stats, "candidates": []}
    if not isinstance(messages, list):
        stats["malformed_lines"] = 1
        return {"sha256": sha, "stats": stats, "candidates": []}
    stats["lines"] = len(messages)
    previous_user = None
    assistant_chunks, assistant_lines = [], []
    turns = []
    for index, msg in enumerate(messages, 1):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        text = str(msg.get("content") or "")
        if role == "assistant":
            if text:
                assistant_chunks.append(text)
                assistant_lines.append(index)
                stats["assistant_messages"] += 1
        elif role == "user":
            if "<cross-session-message" in text:
                stats["relay_messages"] += 1
            text = _strip_injected_blocks(text)
            if not text:
                continue
            stats["human_messages"] += 1
            turns.append({"previous_user": previous_user or "", "user_text": text,
                          "line": index, "event_id": "message-%d" % index, "timestamp": "",
                          "assistant_text": "\n\n".join(assistant_chunks),
                          "assistant_lines": list(assistant_lines), "tools": []})
            previous_user = text
            assistant_chunks, assistant_lines = [], []
    candidates = _emit_from_turns(
        turns, "hai", path, Path(path).stem, mode, seeds,
        lambda t: {"message_index": t["line"]})
    candidates, _dup = _dedup_by_id(candidates)
    stats["candidates"] = len(candidates)
    stats["duplicate_candidates"] = _dup
    return {"sha256": sha, "stats": stats, "candidates": candidates}


def _control_row(mode, name, expected, prior, assistant, reaction,
                 recovery="", followup="", expected_recovery=None):
    packet = {"source": "control", "locator": {"control": name}, "timestamp": "",
              "previous_user_request": prior, "assistant_response": assistant,
              "assistant_tool_names": [], "owner_reaction": reaction,
              "assistant_recovery": recovery, "owner_followup": followup}
    row = {"id": "native-control-" + name, "source": "control",
           "locator": {"control": name}, "timestamp": "",
           "signal_terms": _signal_terms(mode, reaction), "seeded": False,
           "packet_b64": base64.b64encode(json.dumps(
               packet, ensure_ascii=False, sort_keys=True).encode("utf-8")).decode("ascii"),
           "control": True}
    if mode == "positive":
        row["expected_positive"] = expected
    else:
        row["expected_corrective"] = expected
        if expected_recovery is not None:
            row["expected_recovery"] = expected_recovery
    return row


def _controls(mode="positive"):
    if mode == "positive":
        rows = [
            ("positive", True,
             "Please use the mechanisms the Lab already has and verify the order interface first.",
             "I inspected the documented Starfleet capability and order contracts, then used the existing "
             "Project 30 work-packet path. No parallel orchestrator was introduced.",
             "Perfect. That's exactly the existing architecture I wanted you to use. Proceed."),
            ("negated", False, "Please inspect the existing architecture.",
             "I skipped inspection and built a second orchestration service.",
             "This is not good. Do not do that."),
            ("quoted", False, "Give an evidence-grounded answer.",
             "I have not inspected the evidence, but here is my conclusion.",
             "When I said 'good work' earlier, that was an example, not praise for this blind answer."),
            ("mixed", False, "Read the runbook before acting.",
             "I made a plan from memory and did not read the runbook.",
             "Good question, but you still haven't read the runbook or answered what I asked."),
        ]
        return [_control_row(mode, *r) for r in rows]
    return [
        _control_row(mode, "corr-genuine", True,
                     "Deploy through the documented gate only.",
                     "I edited the live config directly to save time; the gate felt unnecessary here.",
                     "No - stop. Put the config back and take it through the gate like I asked.",
                     recovery="Reverted the live config to the checked-in version, then re-ran the "
                              "documented gate; suite green and the deploy went through the gate.",
                     followup="Thank you. That's what I was after.",
                     expected_recovery="successful_recovery"),
        _control_row(mode, "corr-question", False,
                     "Place the job on the fleet.",
                     "I submitted the order through the scheduler and it landed on the free factory.",
                     "Wait, how does the scheduler decide which factory gets it?"),
        _control_row(mode, "corr-quoted", False,
                     "Continue the migration.",
                     "I moved the next batch of files and updated every consumer I found.",
                     "When I said 'stop doing that' last week, that was about the render queue - this "
                     "batch is fine, keep going."),
        _control_row(mode, "corr-ignored", True,
                     "Only report findings; do not change anything.",
                     "I found the stale entries and deleted them while I was in there.",
                     "Don't do that - I said report only. Undo the deletions.",
                     recovery="Understood - I also cleaned up the adjacent directory while restoring, "
                              "since it had the same stale pattern.",
                     followup="I just told you not to change things. Why are you doing it again?",
                     expected_recovery="ignored_or_repeated"),
        _control_row(mode, "corr-norecovery", True,
                     "Summarize the log.",
                     "I rewrote the whole logging module instead; the summary seemed less useful.",
                     "That's not what I asked for. Put it back and just summarize.",
                     expected_recovery="no_observable_recovery"),
    ]


def _load_seeds(path):
    if not path:
        return None
    data = _load_json(path)
    if not isinstance(data, list) or not data:
        raise SystemExit("REFUSED (rc 2): seed-locators file is missing, empty, or not a list: %s" % path)
    seeds = set()
    for row in data:
        sf, ln = row.get("source_file"), row.get("reaction_line")
        if not sf or not isinstance(ln, int):
            raise SystemExit("REFUSED (rc 2): malformed seed locator row: %r" % row)
        seeds.add((str(Path(sf).resolve()).lower(), ln))
    return seeds


def _discover(claude_root, codex_root, hai_root):
    all_claude = sorted(Path(claude_root).rglob("*.jsonl"))
    claude = [p for p in all_claude if "subagents" not in {x.lower() for x in p.parts}]
    all_codex = sorted(Path(codex_root).rglob("*.jsonl"))
    codex, codex_excluded = [], []
    for p in all_codex:
        direct, source = _codex_is_direct(p)
        (codex if direct else codex_excluded).append(p if direct else (p, source))
    hai_base = Path(hai_root)
    hai = []
    owner_dir = hai_base / _P.setting("OWNER_USER", "owner")
    if owner_dir.exists():
        # .summary.json = a compacted summary, .pending.json = a turn still running (#37): not transcripts
        not_transcript = (".summary.json", ".pending.json")
        hai.extend(p for p in owner_dir.glob("*.json")
                   if p.name != "index.json" and not p.name.endswith(not_transcript))
        trash = owner_dir / ".trash"
        if trash.exists():
            hai.extend(p for p in trash.glob("*.json") if not p.name.endswith(not_transcript))
    legacy = hai_base / (_P.setting("OWNER_USER", "owner") + ".json")
    if legacy.exists():
        hai.append(legacy)
    return {
        "claude": sorted(claude), "codex": sorted(codex), "hai": sorted(set(hai)),
        "discovery": {
            "claude_total": len(all_claude), "claude_eligible": len(claude),
            "claude_excluded_subagent": len(all_claude) - len(claude),
            "codex_total": len(all_codex), "codex_eligible": len(codex),
            "codex_excluded_non_direct": len(codex_excluded),
            "codex_exclusion_reasons": sorted({str(reason) for _, reason in codex_excluded}),
            "hai_eligible": len(set(hai)),
        },
    }


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--mode", choices=sorted(MODES), default="positive")
    ap.add_argument("--seed-locators", default=None,
                    help="JSON list of {source_file, reaction_line}: reactions to force-include "
                         "as candidates even when the mode's signal surface misses them")
    ap.add_argument("--claude-root", default=CLAUDE_DEFAULT)
    ap.add_argument("--codex-root", default=CODEX_DEFAULT)
    ap.add_argument("--hai-root", default=HAI_DEFAULT)
    args, _extra = ap.parse_known_args(argv)
    mode, stem = args.mode, MODES[args.mode]["stem"]
    seeds = _load_seeds(args.seed_locators)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    shard_dir = out_dir / "extract-shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / "extract-state.json"
    # seeds change what a shard contains, so they are part of checkpoint identity
    state_version = "%s|mode=%s|seeds=%s" % (
        VERSION, mode, _canonical_hash(sorted(seeds))[:16] if seeds else "none")
    state = _load_json(state_path, {"version": state_version, "files": {}})
    if state.get("version") != state_version:
        raise SystemExit("REFUSED (rc 2): checkpoint version %r != payload %r; use a fresh output "
                         "directory so old and new semantics cannot mix"
                         % (state.get("version"), state_version))

    discovered = _discover(args.claude_root, args.codex_root, args.hai_root)
    candidates, manifests, aggregate = [], [], {
        "lines": 0, "malformed_lines": 0, "human_messages": 0,
        "assistant_messages": 0, "tool_events": 0, "candidates": 0,
        "relay_messages": 0, "duplicate_candidates": 0,
    }
    all_jobs = [(source, p) for source in ("claude", "codex", "hai")
                for p in discovered[source]]
    if not all_jobs:
        raise SystemExit("REFUSED (rc 2): discovered zero eligible source files")
    print("[%s] mode=%s | %d eligible files: Claude %d, Codex %d, HAI %d" % (
        VERSION, mode, len(all_jobs), len(discovered["claude"]), len(discovered["codex"]),
        len(discovered["hai"])), flush=True)

    parsers = {"claude": _parse_claude, "codex": _parse_codex, "hai": _parse_hai}
    for index, (source, path) in enumerate(all_jobs, 1):
        key = str(path.resolve())
        stat = path.stat()
        shard_name = hashlib.sha256(key.lower().encode("utf-8")).hexdigest()[:24] + ".json"
        shard_path = shard_dir / shard_name
        old = (state.get("files") or {}).get(key) or {}
        if (old.get("size") == stat.st_size and old.get("mtime_ns") == stat.st_mtime_ns
                and shard_path.exists()):
            shard = _load_json(shard_path)
            action = "resume"
        else:
            shard = parsers[source](path, mode=mode, seeds=seeds)
            shard.update({"version": state_version, "source": source, "source_file": key,
                          "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
            _atomic_json(shard_path, shard)
            state.setdefault("files", {})[key] = {
                "source": source, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                "sha256": shard["sha256"], "shard": shard_name,
            }
            state["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            _atomic_json(state_path, state)
            action = "parsed"
        if not isinstance(shard, dict) or shard.get("source") not in (None, source):
            raise SystemExit("REFUSED (rc 2): malformed or wrong-source shard %s" % shard_path)
        candidates.extend(shard.get("candidates") or [])
        stats = shard.get("stats") or {}
        for field in aggregate:
            aggregate[field] += int(stats.get(field) or 0)
        manifests.append({"source": source, "source_file": key, "size": stat.st_size,
                          "mtime_ns": stat.st_mtime_ns, "sha256": shard.get("sha256"),
                          "stats": stats, "shard": shard_name})
        print("[extract] %d/%d %s %s: %d candidates" % (
            index, len(all_jobs), action, path.name, len(shard.get("candidates") or [])), flush=True)

    ids = [row.get("id") for row in candidates]
    if len(set(ids)) != len(ids) or any(not x for x in ids):
        raise SystemExit("REFUSED (rc 2): candidate IDs are missing or non-unique")
    if not candidates:
        raise SystemExit("REFUSED (rc 2): eligible transcripts produced zero %s-signal candidates" % mode)
    candidates.sort(key=lambda r: (r.get("timestamp") or "", r["id"]))
    controls = _controls(mode)
    items = candidates + controls
    work_sha = _canonical_hash(items)
    work = {"version": VERSION, "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "mode": mode, "purpose": MODES[mode]["purpose"],
            "work_sha256": work_sha, "candidate_count": len(candidates),
            "control_count": len(controls), "items": items}
    summary = {"version": VERSION, "mode": mode, "complete": len(manifests) == len(all_jobs),
               "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "discovery": discovered["discovery"], "eligible_files": len(all_jobs),
               "processed_files": len(manifests), "aggregate": aggregate,
               "candidate_count": len(candidates), "control_count": len(controls),
               "seeded_count": sum(1 for r in candidates if r.get("seeded")),
               "work_sha256": work_sha}
    _atomic_json(out_dir / "source-manifest.json", {"version": VERSION, "files": manifests})
    _atomic_json(out_dir / ("%s-candidates.json" % stem), {"version": VERSION, "items": candidates})
    _atomic_json(out_dir / ("%s.work.json" % stem), work)
    _atomic_json(out_dir / "extract-summary.json", summary)
    print("[extract] COMPLETE: %d files, %d candidates (%d seeded), work sha %s" % (
        len(manifests), len(candidates), summary["seeded_count"], work_sha[:16]), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    sys.exit(main(sys.argv[1:]))
