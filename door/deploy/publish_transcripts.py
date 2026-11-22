"""Filtered copies of the owner's transcripts for Uhura: Claude, Doc and native Codex engineering sessions.

GPT may read the owner's own Doc chats and the machine accounts' (DOC_USERS), no one else's. Runs on Softserve, where both stores
live, and writes plain markdown that Sundae shares read-only into Uhura's container.

What goes in: the words of each turn, both speakers, with times. What stays out: tool calls and their results (file
dumps, command output), system and meta messages, sub-agent side chains, and any paragraph the library's own filter
would withhold (secret patterns and locker values, intimate terms, the always-private name, a person beside likeness words), plus
Project 20 and person-model talk, which stays closed to GPT ("meta-information only").

Codex engineering sessions additionally include permitted tool arguments/results;
system/reasoning/media and private/protected payloads remain excluded. Legacy
Claude/Doc behavior is unchanged; Doc accounts named Codex are not native sessions.

Usage: publish_transcripts.py <out> [--claude <dir>] [--history <dir>] [--codex <Codex home>]
"""
import hashlib
import json
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import publish_library as lib  # noqa: E402  one filter, the library's

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

from uhura_settings import setting  # noqa: E402
CLAUDE = setting("UHURA_CLAUDE_PROJECTS", os.path.join(os.path.expanduser("~"), ".claude", "projects"))
HISTORY = setting("UHURA_DOC_HISTORY")
CODEX = setting("UHURA_CODEX_HOME", os.path.join(os.path.expanduser("~"), ".codex"))
DOC_USERS = [lib.OWNER_NAME, "ClaudeSupervisor", "ChatGPTSupervisor"]
MACHINE_FLAT = re.compile(r"^Codex\w*\.json$")  # the older Codex machine accounts, one file each
# Project 20 and person-model talk. The factories were born in that project and still carry its name in their plumbing
# (the desk's `/p20/...` addresses, `State\p20-render-queue`, `P20-MULTI-FACTORY-FLEET`): a "p20" that is part of a
# path or a hyphenated name is infrastructure and passes; the bare word and "Project 20" stay closed.
REMINDER = re.compile(r"(?s)<system-reminder>.*?</system-reminder>")
# Image requests of real people in Doc chats: a Doc chat that asks for or makes an image is left out whole. Claude
# sessions stay open: almost none carry that, and the person topics that must stay out are caught by the word rules.
IMAGE_CHAT = re.compile(r"(?i)\b(make|generate|create|draw|render|edit)\b[^.\n]{0,40}\b(image|photo|picture|pic|portrait|selfie|render)s?\b"
                        r"|\brender(ed|ing|s)? of\b|\byour render\b|\bimage server\b|\bqueued!? a render\b")
WITHHELD = "[A paragraph is withheld from the Uhura copy.]"


def slug(text, n=60):
    s = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower()
    return s[:n].strip("-") or "untitled"


def clean(text):
    """The filtered text of one turn and how many paragraphs were withheld."""
    return clean_counted(text)[:2]


def clean_counted(text, benchmark=False):
    """(filtered text, paragraphs withheld, of which intimate paragraphs). `benchmark`: the session around this turn is
    about the public benchmark (lib.benchmark_context), so the word "anatomy" alone withholds nothing; a turn that is
    itself about it counts the same."""
    text = REMINDER.sub("", text or "").strip()
    benchmark = benchmark or lib.benchmark_context(text)
    kept, withheld, intimate = [], 0, 0
    for block in lib.split_blocks(text):
        if not block.strip():
            kept.append(block)
            continue
        hot = bool(lib.intimate(block, benchmark) or lib.ALWAYS_PRIVATE.search(block))
        if hot or lib.CLOSED_TOPICS.search(block) or lib.SECRET.search(block) or lib.block_is_private(block, benchmark):
            kept.append(WITHHELD)
            withheld += 1
            intimate += hot
        else:
            kept.append(block)
    return "".join(kept).strip(), withheld, intimate


# A session that is ABOUT intimate renders talks around them in pronouns no word rule catches, so a transcript with
# this many intimate paragraphs is left out whole. Measured: the Project 20 sessions carry 44 to 265 such
# paragraphs; ordinary sessions that merely touch it carry 3 to 27, and keep everything but those paragraphs.
PERSON_SESSION_LIMIT = 40


def claude_turns(path):
    """(title, [(time, speaker, text)]) from one Claude Code session file; words only."""
    title, turns = None, []
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("customTitle") or r.get("aiTitle"):
            title = r.get("customTitle") or title or r.get("aiTitle")
        if r.get("type") not in ("user", "assistant") or r.get("isSidechain") or r.get("isMeta") or r.get("isCompactSummary"):
            continue
        content = (r.get("message") or {}).get("content")
        if isinstance(content, str):
            parts = [content]
        else:
            parts = [c.get("text", "") for c in (content or []) if isinstance(c, dict) and c.get("type") == "text"]
        text = "\n\n".join(p for p in parts if p and p.strip())
        if not text or (r["type"] == "user" and text.lstrip().startswith("<")):
            continue
        turns.append((r.get("timestamp", "")[:16].replace("T", " "), lib.OWNER_NAME if r["type"] == "user" else "Claude", text))
    return title, turns


def safe_title(title, fallback):
    """A title is text too: one the filter would withhold becomes the plain fallback."""
    t = (title or "").strip()
    return fallback if not t or clean(t)[1] else t


def render(title, source, turns):
    """(markdown, paragraphs withheld), or (None, withheld) for a transcript about person models, left out whole."""
    body, withheld, person = [], 0, 0
    benchmark = lib.benchmark_context("\n\n".join(REMINDER.sub("", t or "") for _at, _who, t in turns))
    for at, who, text in turns:
        kept, w, p = clean_counted(text, benchmark)
        withheld += w
        person += p
        if kept:
            body.append("**[%s] %s:**\n\n%s" % (at or "?", who, kept))
    if person >= PERSON_SESSION_LIMIT:
        return None, withheld
    head = "# %s\n\nSource: %s. Turns: %d. Paragraphs withheld: %d.\n\n" % (title, source, len(body), withheld)
    return head + "\n\n---\n\n".join(body) + "\n", withheld


def write_if_changed(path, text):
    if os.path.exists(path) and open(path, encoding="utf-8").read() == text:
        return False
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    return True


SOURCE_READ_ATTEMPTS = 11
SOURCE_READ_DELAY_S = .2


def read_source_json(path):
    """Read a complete source record while its existing writer may truncate/rewrite.

    Doc history._write writes in place. A publisher lock cannot lock that writer.
    Retry only incomplete/transient reads for at most two seconds; never replace
    a failed read with an empty source or change source files. Permission errors
    and permanent corruption still abort before atomic snapshot publication.
    """
    for attempt in range(SOURCE_READ_ATTEMPTS):
        try:
            with open(path, encoding="utf-8") as stream:
                return json.load(stream)
        except (json.JSONDecodeError, UnicodeDecodeError, FileNotFoundError):
            if attempt + 1 == SOURCE_READ_ATTEMPTS:
                raise
            time.sleep(SOURCE_READ_DELAY_S)


def collect(argv):
    args = [a for a in argv if not a.startswith("--")]
    out = args[0]
    claude = argv[argv.index("--claude") + 1] if "--claude" in argv else CLAUDE
    history = argv[argv.index("--history") + 1] if "--history" in argv else HISTORY
    codex = argv[argv.index("--codex") + 1] if "--codex" in argv else CODEX
    lib.LOCKER, count = lib.locker_pattern()
    # Collect before writing anything: unreadable/unknown Codex storage must not
    # silently publish an incomplete replacement or remove prior transcript files.
    import codex_transcripts
    hide = os.path.join(os.path.dirname(history), "rag-hide.json")
    hidden_tasks = read_source_json(hide).get("tasks", []) if os.path.isfile(hide) else []
    codex_files, codex_stats = codex_transcripts.build(codex, clean_counted, safe_title, PERSON_SESSION_LIMIT, hidden_tasks)
    rendered = {}
    def staged_write(path, text):
        rendered[os.path.basename(path)] = text
        return True
    made, changed, withheld, counts = set(), 0, 0, {"claude": 0, "doc": 0, "left_out": 0}

    for project in sorted(os.listdir(claude)):          # one folder per project; session files sit directly inside
        folder = os.path.join(claude, project)
        if not os.path.isdir(folder):
            continue
        for n in sorted(os.listdir(folder)):
            path = os.path.join(folder, n)
            if not (n.endswith(".jsonl") and os.path.isfile(path)):
                continue
            title, turns = claude_turns(path)
            if len(turns) < 2:
                continue
            day = turns[0][0][:10] or "undated"
            title = safe_title(title, "Claude Code session " + n[:8])
            name = "claude--%s--%s--%s.md" % (day, slug(title), n[:8])
            text, w = render(title,
                             "Claude Code session %s, %s to %s" % (n[:8], turns[0][0], turns[-1][0]), turns)
            if text is None:
                counts["left_out"] += 1
                continue
            changed += staged_write(os.path.join(out, name), text)
            made.add(name)
            withheld += w
            counts["claude"] += 1

    def doc_chat(user, chat_id, title, created, updated, msgs):
        nonlocal changed, withheld
        turns = [(created[:16].replace("T", " ") if i == 0 else "", user if m.get("role") == "user" else "Doc",
                  m.get("content") or "") for i, m in enumerate(msgs) if m.get("role") in ("user", "assistant")]
        if not turns:
            return
        if any(IMAGE_CHAT.search(t[2]) for t in turns) or IMAGE_CHAT.search(title or ""):
            counts["left_out"] += 1
            return
        title = safe_title(title, "Doc chat " + chat_id[:12])
        name = "doc--%s--%s--%s--%s.md" % (user, (created or "undated")[:10], slug(title), chat_id[:12])
        text, w = render(title,
                         "Doc chat %s of account %s, %s to %s" % (chat_id, user, created[:16], updated[:16]), turns)
        if text is None:
            counts["left_out"] += 1
            return
        changed += staged_write(os.path.join(out, name), text)
        made.add(name)
        withheld += w
        counts["doc"] += 1

    for user in DOC_USERS:
        folder = os.path.join(history, user)
        if not os.path.isdir(folder):
            continue
        index = read_source_json(os.path.join(folder, "index.json")).get("chats", [])
        for c in index:
            path = os.path.join(folder, c["id"] + ".json")
            if os.path.exists(path):
                msgs = read_source_json(path)
                doc_chat(user, c["id"], c.get("title", ""), c.get("created_at", ""), c.get("updated_at", ""),
                         msgs if isinstance(msgs, list) else [])
    for n in sorted(os.listdir(history)):
        if MACHINE_FLAT.match(n) and not n.endswith(".summary.json"):
            path = os.path.join(history, n)
            msgs = read_source_json(path)
            stamp = time.strftime("%Y-%m-%dT%H:%M", time.localtime(os.path.getmtime(path)))
            doc_chat(n[:-5], hashlib.sha256(n.encode()).hexdigest()[:12], n[:-5], stamp, stamp,
                     msgs if isinstance(msgs, list) else [])

    if counts["claude"] < 10 or counts["doc"] < 5:
        raise SystemExit("positive control failed: %r - are both stores readable?" % counts)
    for name, text in codex_files.items():
        changed += staged_write(os.path.join(out, name), text)
        made.add(name)
    export_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    index_name = "codex--index.md"
    index = ("# Codex engineering transcript ingestion\n\nGenerated UTC: %s. Refresh uses the existing 30-minute/startup task, response completion, and authenticated requests.\n"
             "Current data is complete through each source snapshot's completed records; unfinished items/tail lines appear on a later pass.\n"
             "No original session records are changed. Counts are native Codex, not Doc Codex accounts.\n\n```json\n%s\n```\n\n" % (export_at, json.dumps(codex_stats, indent=2)))
    index += "\n".join("- " + name for name in sorted(codex_files)) + "\n"
    changed += staged_write(os.path.join(out, index_name), index)
    made.add(index_name)
    return rendered, {"at": export_at, "locker_values": count, "claude": counts["claude"],
                      "doc": counts["doc"], "left_out": counts["left_out"], "withheld": withheld, **codex_stats}


def main(argv):
    import argparse
    import uuid
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from transcript_store import publication_lock, commit, utc
    parser=argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--claude",default=CLAUDE)
    parser.add_argument("--history",default=HISTORY)
    parser.add_argument("--codex",default=CODEX)
    parser.add_argument("--request-id",default=None)
    parser.add_argument("--source",choices=["timer","codex","claude","mcp","manual"],default="timer")
    opts=parser.parse_args(argv)
    request_id=opts.request_id or uuid.uuid4().hex
    with publication_lock(opts.out):
        started=utc()
        files,stats=collect([opts.out,"--claude",opts.claude,"--history",opts.history,"--codex",opts.codex])
        receipt=commit(opts.out,files,stats,request_id,started,opts.source)
        print(json.dumps(receipt),flush=True)
        return receipt



if __name__ == "__main__":
    main(sys.argv[1:])
