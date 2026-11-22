"""Deterministic handoff reader for project status.

Reads the top of Reference/CURRENT-PROJECTS.md and recent day journals to answer:
"What was done, what is left, and where are we?"

Constraints:
- Read-only: no model calls, network I/O, or file writes.
- Paths sourced from config only (never hardcoded).
- Uses glob("*.md") instead of rglob to exclude Reference/Credentials/;
  this exclusion is enforced by tests/test_credentials_exclusion.py.
- Do not import `tools` here: `tools` imports this module, causing a cycle."""
import datetime
import pathlib
import re

import config

# Output bounds. The payload must fit comfortably inside one GET_DOC_MAX_CHARS-shaped window —
# callers treat tool output like a fetched document, and an unbounded CURRENT-PROJECTS.md is
# >800 KB. Truncation is always STATED, never silent.
CP_HEAD_CHARS = 6000        # the top of CURRENT-PROJECTS.md (newest handoff blocks live at the top)
JOURNAL_HEAD_CHARS = 1200   # bounded head of each journal
MAX_JOURNALS = 6            # newest-first cap; the header states how many existed vs shown
DAYS_DEFAULT = 2
DAYS_CAP = 7

_DATED = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def _cp_path():
    return pathlib.Path(config.DOCS_DIR) / "Reference" / "CURRENT-PROJECTS.md"


def _journal_dirs():
    """Journal dirs from config.FETCH_ONLY_DIRS (fetchable by name, deliberately unindexed)."""
    dirs = (getattr(config, "FETCH_ONLY_DIRS", None) or {}).get("homelab") or []
    return [pathlib.Path(d) for d in dirs]


def _file_date(path):
    """The date a journal claims in its FILENAME (the lab convention) — never mtime, which lies
    after copies and migrations. None when the name carries no date."""
    m = _DATED.match(path.name)
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _first_heading(text):
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            return line.strip()
    return "(no heading found)"


def catch_up(topic="", days=DAYS_DEFAULT):
    """One text payload: header with counts -> read rules -> top of CURRENT-PROJECTS ->
    newest journals within `days` (newest first) -> a closing pointer. Every failure mode is
    LOUD and NAMED in the returned text; an empty section is a bug, not a result."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = DAYS_DEFAULT
    days_requested = days
    days = max(1, min(DAYS_CAP, days))
    topic = str(topic or "").strip()

    out = []

    # -- gather journals first so the header can state real counts -------------------------------
    dirs = _journal_dirs()
    journal_notes = []      # loud, named problems — never silence
    candidates = []         # (date, path) within the window
    total_md = 0
    if not dirs:
        journal_notes.append("PROBLEM: no journal directory is configured "
                             "(config.FETCH_ONLY_DIRS['homelab'] is empty) — journals unreachable.")
    for d in dirs:
        if not d.is_dir():
            journal_notes.append(f"PROBLEM: journal directory {d} is missing or unreadable — "
                                 "that is different evidence from 'no recent journals'.")
            continue
        found = sorted(d.glob("*.md"))     # glob, never rglob — see module docstring
        total_md += len(found)
        if not found:
            journal_notes.append(f"PROBLEM: journal directory {d} exists but contains 0 .md files.")
            continue
        today = datetime.date.today()
        for f in found:
            fdate = _file_date(f)
            if fdate is None:
                continue
            if (today - fdate).days < days:
                candidates.append((fdate, f))
    undated = total_md - len(candidates) if total_md else 0

    matched = sorted(candidates, key=lambda t: (t[0], t[1].name), reverse=True)
    filter_note = ""
    if topic and matched:
        t_low = topic.lower()

        def _hit(path):
            if t_low in path.name.lower():
                return True
            try:
                head = path.read_text(encoding="utf-8", errors="ignore")[:JOURNAL_HEAD_CHARS * 4]
            except OSError:
                return False
            return t_low in head.lower()

        filtered = [(dte, f) for dte, f in matched if _hit(f)]
        if not filtered:
            filter_note = (f"Topic filter {topic!r} matched NOTHING — filtered "
                           f"{len(matched)} -> 0 candidate journal(s); showing all {len(matched)} unfiltered.")
        else:
            filter_note = f"Topic filter {topic!r}: {len(matched)} -> {len(filtered)} journal(s)."
            matched = filtered
    shown = matched[:MAX_JOURNALS]

    # -- header with counts ----------------------------------------------------------------------
    cp = _cp_path()
    out.append("# Catch-up — the handoff surface, read deterministically")
    cap_note = f" (requested {days_requested}, capped at {DAYS_CAP})" if days != days_requested else ""
    out.append(f"Read: top of {cp.name} + {len(shown)} of {len(matched)} journal(s) from the last "
               f"{days} day(s){cap_note}. Journal files on disk: {total_md}.")
    if filter_note:
        out.append(filter_note)

    # -- the read rules travel with the data -------------------------------------------------------
    out.append(
        "READ RULES: CURRENT-PROJECTS blocks are ADDITIVE and newest-first — a LATER block "
        "supersedes an earlier one on the same subject. A definitive doc is read WHOLE via "
        "get_document, not from this excerpt.")

    # -- top of CURRENT-PROJECTS -------------------------------------------------------------------
    out.append("\n## Top of " + cp.name)
    try:
        cp_text = cp.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        cp_text = None
        out.append(f"PROBLEM: {cp} is missing or unreadable ({exc.__class__.__name__}: {exc}). "
                   "The primary handoff surface could not be read — say so, do not guess.")
    if cp_text is not None:
        out.append(
            f"NOTE: get_document is capped at {config.GET_DOC_MAX_CHARS} characters and "
            f"{cp.name} is {len(cp_text)} characters, so only the TOP is ever visible to any "
            "reader — including this one. Nothing below the cut is being reported.")
        head = cp_text[:CP_HEAD_CHARS]
        out.append(head)
        if len(cp_text) > CP_HEAD_CHARS:
            out.append(f"…[{cp.name} truncated at {CP_HEAD_CHARS} of {len(cp_text)} characters]")

    # -- journals, newest first --------------------------------------------------------------------
    out.append("\n## Newest journals (newest first)")
    for note in journal_notes:
        out.append(note)
    if not journal_notes and not matched:
        dirs_named = ", ".join(str(d) for d in dirs)
        out.append(f"No journal within the last {days} day(s): {total_md} .md file(s) exist in "
                   f"{dirs_named} but none is dated inside the window"
                   + (f" ({undated} carry no parseable YYYY-MM-DD name)" if undated else "")
                   + ". The directory IS readable — this is 'nothing recent', not 'could not look'.")
    for fdate, f in shown:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            out.append(f"\n### {f.name} ({fdate})\nPROBLEM: unreadable ({exc.__class__.__name__}: {exc}).")
            continue
        out.append(f"\n### {f.name} ({fdate}) — {_first_heading(text)}")
        out.append(text[:JOURNAL_HEAD_CHARS])
        if len(text) > JOURNAL_HEAD_CHARS:
            out.append(f"…[head shown: {JOURNAL_HEAD_CHARS} of {len(text)} characters — fetch the "
                       f"whole file with get_document('{f.name}')]")
    if len(matched) > len(shown):
        out.append(f"\n({len(matched) - len(shown)} more journal(s) in the window not shown — "
                   "narrow with a topic or fetch by filename.)")

    # -- closing pointer ---------------------------------------------------------------------------
    target = shown[0][1].name if shown else cp.name
    out.append(f"\nNEXT: for the full detail behind any item above, call get_document('{target}') "
               "or name another file listed here.")
    return "\n".join(out)
