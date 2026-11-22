"""HAI writes to two constrained surfaces; all paths/names are code-generated.

1. journal_append(text): Appends to Journal/YYYY-MM-DD-hai.md. Non-indexed (no RAG hazard).
2. handoff_note(headline, lines): Prepends to Reference/CURRENT-PROJECTS.md. Indexed.
   Code prepends fixed H1; file is re-read before write to prevent clobbering.

Invariants:
- Both writers byte-verify the write (read-back assert) and raise on failure.
- Sensitive content must go to person records only, never these files."""
from __future__ import annotations

import datetime
import os
import re

import config

_HAI_JOURNAL_HEADER = """# {date} — HAI work journal

*Written by HAI itself via `journal_append` (librarian reflex, 10d). One file per day, HAI's own
(side-session convention). NOT RAG-indexed (Journal is fetch-only); reviewed by claim_sweep.*
"""

_MAX_ENTRY_CHARS = 8000
_MAX_NOTE_LINES = 12
_MAX_LINE_CHARS = 400


def _now_ct():
    """The lab's wall clock for headers (config.LAB_ZONE; the head's own clock is UTC)."""
    return datetime.datetime.now(config.LAB_ZONE)


def journal_path(now=None) -> str:
    now = now or _now_ct()
    return os.path.join(config.DOCS_DIR, "Journal", "%s-hai.md" % now.strftime("%Y-%m-%d"))


def journal_append(text, now=None) -> dict:
    """Append one timestamped section to today's HAI journal. Creates the file with its fixed
    header on first write. Returns {"path", "bytes", "created"}. Raises on any failure —
    loud, never silent."""
    text = (text or "").strip()
    if not text:
        raise ValueError("journal_append: an empty entry is not a record")
    text = text[:_MAX_ENTRY_CHARS]
    now = now or _now_ct()
    path = journal_path(now)
    created = not os.path.exists(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    section = "\n## %s\n\n%s\n" % (now.strftime("%H:%M CT"), text)
    payload = (_HAI_JOURNAL_HEADER.format(date=now.strftime("%Y-%m-%d")) + section) if created \
        else section
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(payload)
    # BYTE-VERIFY: the entry must be readable back from the file it claims to live in.
    with open(path, encoding="utf-8") as fh:
        back = fh.read()
    if text not in back:
        raise IOError("journal_append: write not found on read-back of %s" % path)
    return {"path": path, "bytes": len(back.encode("utf-8")), "created": created}


def handoff_note(headline, lines, author="HAI", now=None) -> dict:
    """Prepend a fixed-format [HAI SESSION] block to CURRENT-PROJECTS, directly under the H1.

    The model chooses only `headline` (one line) and `lines` (content bullets, capped). The
    block shape, tag, date and position are code-generated. Re-reads the file immediately
    before writing and byte-verifies after. Returns {"path", "block_chars"}."""
    headline = re.sub(r"\s+", " ", str(headline or "")).strip()
    if not headline:
        raise ValueError("handoff_note: a headline is required (one line: what happened)")
    if not lines:
        raise ValueError("handoff_note: content lines are required (what/where/next)")
    if isinstance(lines, str):
        lines = [ln for ln in lines.splitlines() if ln.strip()]
    lines = [str(ln).strip()[:_MAX_LINE_CHARS] for ln in list(lines)[:_MAX_NOTE_LINES] if str(ln).strip()]
    now = now or _now_ct()
    path = os.path.join(config.DOCS_DIR, "Reference", "CURRENT-PROJECTS.md")

    # RE-READ AT WRITE TIME — the shared-doc rule. Never write from a stale copy of a file
    # other sessions also prepend to.
    with open(path, encoding="utf-8") as fh:
        doc = fh.read()

    block_lines = ["> ## 🤖 [HAI SESSION] %s — %s" % (now.strftime("%Y-%m-%d %H:%M CT"),
                                                      headline[:160]), ">"]
    for ln in lines:
        block_lines.append("> %s" % ln)
    block_lines.append(">")
    block_lines.append("> *(written by %s via handoff_note — librarian reflex, 10d)*" % author)
    block = "\n".join(block_lines) + "\n\n"

    # Insert AFTER the H1 line — the additive newest-first-at-TOP law. If the doc somehow has
    # no H1 (should never happen), refuse rather than guess a position.
    m = re.match(r"^(# [^\n]*\n\n?)", doc)
    if not m:
        raise ValueError("handoff_note: CURRENT-PROJECTS has no leading H1 - refusing to guess "
                         "a position in a shared document")
    out = doc[:m.end()] + block + doc[m.end():]
    tmp = path + ".hai-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(out)
    os.replace(tmp, path)
    with open(path, encoding="utf-8") as fh:
        back = fh.read()
    if block_lines[0] not in back:
        raise IOError("handoff_note: block not found on read-back of %s" % path)
    return {"path": path, "block_chars": len(block)}
