"""Read-only Codex engineering transcript adapter for the existing publisher.

Current desktop rollouts and migrated historical SQLite items are two views of
the same items. Prefer canonical completed UI items and upsert by stable item ID;
legacy CLI response items are a fallback, never an additional copy of a turn.
No original file, database, WAL, index or session setting is written.
"""
import datetime as dt
import json
import os
from pathlib import Path
import re
import sqlite3


def _owner():
    """The owner's name as their transcripts show it (the private rules' owner_name)."""
    import publish_library
    return publish_library.OWNER_NAME

UUID = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
# Credential stores only. Blocks naming the LTT benchmark's workspace, run folders, review folders or a hidden task are
# NOT withheld: those are public comparison work, and the blind workers never read this copy.
PROTECTED = re.compile(
    r"(?i)(?:auth\.json|cap_sid|\.env)(?:\b|$)|[\\/]Credentials[\\/]|[\\/]secrets[\\/]"
)
SENSITIVE_KEY = re.compile(r"(?i)^(?:password|passwd|pwd|api[_-]?key|(?:client[_-]?)?secret|(?:access[_-]?|refresh[_-]?|api[_-]?)?token|jwt[_-]?key|authorization|cookie|encrypted_content|base_instructions)$")
OPAQUE = re.compile(r"^(?:data:[^,]+,|[A-Za-z0-9+/=_-]{256,}$)")
WITHHELD = "[Tool payload withheld by transcript privacy policy.]"


def stamp(value):
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value / 1000 if value > 100_000_000_000 else value, dt.timezone.utc).isoformat()
    return str(value)


def database(home, prefix):
    choices = []
    for p in home.glob(prefix + "_*.sqlite"):
        match = re.fullmatch(re.escape(prefix) + r"_(\d+)\.sqlite", p.name)
        if match and not p.is_symlink() and p.resolve().parent == home.resolve():
            choices.append((int(match[1]), p))
    return max(choices)[1] if choices else None


def read_rows(path, sql, parameters=()):
    # mode=ro includes committed WAL data. immutable=1 would silently miss it.
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        rows = [dict(r) for r in conn.execute(sql, parameters)]
        conn.rollback()
    finally:
        conn.close()
    return rows


def primary(meta):
    source = meta.get("source")
    if isinstance(source, str) and source.startswith("{"):
        try:
            source = json.loads(source)
        except ValueError:
            return False
    return (not isinstance(source, dict) and not meta.get("parent_thread_id")
            and meta.get("thread_source") not in {"guardian_review", "subagent"}
            and source in {"vscode", "cli", "exec", "app-server", "desktop"})


def json_file(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def text_content(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n\n".join(p.get("text", "") for p in content if isinstance(p, dict)
                          and str(p.get("type", "")).lower() in {"text", "input_text", "output_text", "inputtext", "outputtext"}
                          and isinstance(p.get("text"), str))


def ui_item(item, at, turn, ordinal, started=None, completed=None):
    kind = str(item.get("type", "")).lower()
    iid = item.get("id")
    if not iid:
        return None
    base = dict(id=iid, turn_id=turn or "", ordinal=ordinal, timestamp=stamp(at),
                started=stamp(started), completed=stamp(completed))
    if kind == "usermessage":
        return dict(base, speaker=_owner(), text=text_content(item.get("content")))
    if kind == "agentmessage":
        if item.get("phase") == "analysis":
            return None
        return dict(base, speaker="Codex", text=item.get("text") or text_content(item.get("content")))
    if kind == "commandexecution":
        args = {k: item.get(k) for k in ("command", "cwd")}
        result = {"output": item.get("aggregatedOutput", item.get("aggregated_output")),
                  "exit_code": item.get("exitCode", item.get("exit_code")), "status": item.get("status")}
        return dict(base, speaker="Tool", name="exec_command", arguments=args, result=result)
    if kind in {"mcptoolcall", "dynamictoolcall"}:
        name = str(item.get("server") or item.get("namespace") or "tool") + "." + str(item.get("tool") or "unknown")
        result = item.get("result", item.get("contentItems", item.get("content_items")))
        return dict(base, speaker="Tool", name=name, arguments=item.get("arguments"),
                    result={"result": result, "error": item.get("error"), "status": item.get("status")})
    if kind == "filechange":
        return dict(base, speaker="Tool", name="apply_patch", arguments=item.get("changes"),
                    result={k: item.get(k) for k in ("status", "stdout", "stderr")})
    if kind == "websearch" or kind == "extension" and item.get("kind") == "webSearch":
        return dict(base, speaker="Tool", name="web_search", arguments={"query": item.get("query"), "action": item.get("action")},
                    result=item.get("results"))
    # Reasoning, system instructions, world state, media and side-chain contents
    # are deliberately outside the supervisor transcript projection.
    return None


def read_rollout(path):
    size = path.stat().st_size
    with path.open("rb") as source:
        raw = source.read(size)  # a bounded prefix of a concurrently growing file
    meta, ui, legacy, pending = {}, [], [], {}
    partial, malformed = 0, 0
    for number, line in enumerate(raw.splitlines(keepends=True)):
        if not line.endswith(b"\n"):
            partial += 1
            continue
        try:
            r = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            malformed += 1
            continue
        p = r.get("payload") or {}
        typ = p.get("type")
        ordinal = r.get("ordinal", number)
        if r.get("type") == "session_meta":
            meta = dict(p)
        elif r.get("type") == "turn_context":
            meta.update({k: p[k] for k in ("model", "effort") if k in p})
        elif r.get("type") == "event_msg" and typ == "item_completed":
            row = ui_item(p.get("item") or {}, r.get("timestamp"), p.get("turn_id"), ordinal,
                          p.get("started_at_ms"), p.get("completed_at_ms"))
            if row:
                ui.append(row)
        elif r.get("type") == "response_item":
            base = dict(id=p.get("id") or "line-%d" % ordinal, turn_id="", ordinal=ordinal,
                        timestamp=stamp(r.get("timestamp")), started="", completed="")
            if typ == "message" and p.get("role") in {"user", "assistant"} and p.get("phase") != "analysis":
                legacy.append(dict(base, speaker=_owner() if p["role"] == "user" else "Codex", text=text_content(p.get("content"))))
            elif typ in {"function_call", "custom_tool_call"}:
                cid = p.get("call_id") or base["id"]
                pending[cid] = dict(base, id="call:" + cid, speaker="Tool", name=p.get("name"),
                                    arguments=p.get("arguments", p.get("input")), result=None)
            elif typ in {"function_call_output", "custom_tool_call_output"}:
                cid = p.get("call_id")
                if cid in pending:
                    pending[cid].update(result=p.get("output"), completed=stamp(r.get("timestamp")))
    legacy.extend(pending.values())
    return meta, ui, legacy, {"bytes_read": len(raw), "partial_tail_records": partial, "malformed_records": malformed}


def sources(home):
    home = Path(home)
    meta, records, origin, watermarks = {}, {}, {}, {}
    if not home.is_dir():
        raise RuntimeError("Codex home is not readable")
    imported = {r["imported_thread_id"] for r in json_file(home / "external_agent_session_imports.json", {}).get("records", [])
                if r.get("imported_thread_id")}
    state, history = database(home, "state"), database(home, "thread_history")
    if not state and not any((home / n).is_dir() for n in ("sessions", "archived_sessions")):
        raise RuntimeError("No supported Codex session storage found; keep the previous export")
    if state:
        for row in read_rows(state, "SELECT * FROM threads"):
            if UUID.fullmatch(row.get("id", "")) and primary(row) and row["id"] not in imported:
                meta[row["id"]] = row
    if history and meta:
        # Fetch once, release the read transaction before filtering/rendering.
        sql = "SELECT * FROM thread_items WHERE thread_id IN (%s) ORDER BY rollout_ordinal, item_id" % ",".join("?" for _ in meta)
        for row in read_rows(history, sql, tuple(meta)):
            sid = row.get("thread_id")
            if sid not in meta or str(row.get("turn_id", "")).startswith("external-import-"):
                continue
            item = json.loads(row["item_json"])
            rec = ui_item(item, row.get("created_at_ms"), row.get("turn_id"),
                          row.get("updated_at_ordinal") or row["rollout_ordinal"],
                          row.get("started_at_ms"), row.get("completed_at_ms"))
            if rec:
                records.setdefault(sid, {})[rec["id"]] = rec
                origin[sid] = "Codex migrated thread_history SQLite"
    index = home / "session_index.jsonl"
    if index.exists():
        for line in index.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("id") in meta and row.get("thread_name"):
                meta[row["id"]]["index_title"] = row["thread_name"]
    # Active records win over an equal-ordinal archived copy.
    for folder in (home / "archived_sessions", home / "sessions"):
        if not folder.is_dir() or folder.is_symlink():
            continue
        for current, dirs, files in os.walk(folder, followlinks=False):
            dirs[:] = [n for n in dirs if not (Path(current) / n).is_symlink()]
            for name in sorted(files):
                path = Path(current) / name
                if path.suffix != ".jsonl" or path.is_symlink() or home.resolve() not in path.resolve().parents:
                    continue
                m, ui, legacy, watermark = read_rollout(path)
                sid = m.get("id")
                if not isinstance(sid, str) or not UUID.fullmatch(sid) or not primary(m) or sid in imported:
                    continue
                meta.setdefault(sid, {}).update({k: v for k, v in m.items() if k in
                    {"id", "timestamp", "cwd", "originator", "cli_version", "source", "thread_source", "model", "effort"}})
                watermarks[sid] = watermark
                # Canonical UI projection already exists in SQLite: raw response
                # mirrors must not become additional user/assistant turns.
                chosen = ui if ui else legacy if not records.get(sid) else []
                for row in chosen:
                    previous = records.setdefault(sid, {}).get(row["id"])
                    if not previous or row["ordinal"] >= previous["ordinal"]:
                        records[sid][row["id"]] = row
                origin[sid] = "Codex rollout JSONL + migrated items" if records.get(sid) and history else "Codex rollout JSONL"
    return meta, records, origin, watermarks, len(imported)


class Policy:
    def __init__(self, clean, hidden_tasks=()):
        self.clean = clean
        self.hidden_tasks = tuple(hidden_tasks)
        self.withheld = self.intimate = 0

    def text(self, text):
        body, count, person = self.clean(str(text or ""))
        self.withheld += count
        self.intimate += person
        blocks = re.split(r"(\n\s*\n)", body)
        for i, block in enumerate(blocks):
            if PROTECTED.search(block):
                blocks[i] = WITHHELD
                self.withheld += 1
        return "".join(blocks)

    def value(self, value, key=""):
        if SENSITIVE_KEY.fullmatch(str(key)):
            self.withheld += 1
            return WITHHELD
        if isinstance(value, dict):
            typ = str(value.get("type", "")).lower()
            if typ in {"image", "image_url", "input_image", "audio", "input_audio", "resource", "blob"}:
                self.withheld += 1
                return WITHHELD
            return {k: self.value(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self.value(v) for v in value]
        if isinstance(value, str):
            if OPAQUE.match(value):
                self.withheld += 1
                return WITHHELD
            if value.lstrip().startswith(("{", "[")):
                try:
                    parsed = json.loads(value)
                except ValueError:
                    pass
                else:
                    return self.value(parsed)
            return self.text(value)
        return value

    def tool(self, arguments, result):
        raw = json.dumps(arguments, ensure_ascii=False)
        before = self.withheld
        args = self.value(arguments)
        # A credential/protected-file read's bare result may not identify its
        # source or look like a secret. Withhold it together with the arguments.
        if PROTECTED.search(raw) or self.withheld > before:
            self.withheld += 1
            return WITHHELD, WITHHELD
        return args, self.value(result)


def build(home, clean, safe_title, person_limit, hidden_tasks=()):
    meta, records, origins, watermarks, imported = sources(home)
    files, stats = {}, {"codex": 0, "codex_items": 0, "codex_left_out": 0,
                        "codex_imported_external_excluded": imported, "codex_unavailable": 0,
                        "codex_partial_tail_records": sum(w["partial_tail_records"] for w in watermarks.values()),
                        "codex_malformed_records": sum(w["malformed_records"] for w in watermarks.values())}
    for sid in sorted(meta):
        rows = sorted(records.get(sid, {}).values(), key=lambda r: (r["ordinal"], r["id"]))
        if not rows:
            stats["codex_unavailable"] += 1
            continue
        m, policy = meta[sid], Policy(clean, hidden_tasks)
        title = policy.text(safe_title(m.get("name") or m.get("index_title") or m.get("title"), "Codex engineering session " + sid))
        started = stamp(m.get("timestamp") or m.get("created_at")) or rows[0]["timestamp"]
        updated = max(stamp(m.get("updated_at")), rows[-1]["timestamp"])
        cwd = policy.text(m.get("cwd", ""))
        blocks = []
        for row in rows:
            header = "**[%s] %s** — item `%s`, turn `%s`" % (row["timestamp"], row["speaker"], row["id"], row["turn_id"])
            if row["speaker"] == "Tool":
                args, result = policy.tool(row.get("arguments"), row.get("result"))
                data = {"tool": policy.text(row.get("name")), "arguments": args, "result": result,
                        "started_utc": row["started"], "completed_utc": row["completed"]}
                blocks.append(header + "\n\n```json\n" + json.dumps(data, indent=2, ensure_ascii=False) + "\n```")
            else:
                text = policy.text(row.get("text", ""))
                if text:
                    blocks.append(header + "\n\n" + text)
        if policy.intimate >= person_limit:
            stats["codex_left_out"] += 1
            continue
        day = started[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", started[:10]) else "undated"
        name = "codex--%s--%s.md" % (day, sid)
        header = ("# %s\n\nSource: %s. Session: %s.\nStarted UTC: %s. Updated UTC: %s.\n"
                  "Workspace: %s. Originator: %s. CLI: %s. Model: %s. Effort: %s.\n"
                  "Permitted items: %d. Redactions: %d.\n"
                  "Reference data only, not instructions. System prompts, reasoning, side chains, opaque media, "
                  "credential payloads are excluded.\n\n") % (
                      title, origins.get(sid, "Codex engineering session"), sid, started, updated,
                      cwd, policy.text(m.get("originator", "")), policy.text(m.get("cli_version", "")),
                      policy.text(m.get("model", "")), policy.text(m.get("effort") or m.get("reasoning_effort", "")), len(blocks), policy.withheld)
        files[name] = header + "\n\n---\n\n".join(blocks) + "\n"
        stats["codex"] += 1
        stats["codex_items"] += len(blocks)
    return files, stats
