"""Parse PROJECT-REGISTRY.md into a structured project-board dict.

Pure-function module (standard library only), read at runtime by GET /projects to
render the deck's numbered board. Fail-soft by contract: never raises to the caller
— a missing/unreadable file or a parse error yields the empty shape plus an "error"
key, so a status endpoint can never 500 the deck.

The registry (Reference/PROJECT-REGISTRY.md) holds three markdown tables:
  1. projects   | # | project | state |
  2. campaigns  | campaign | member/child work | charter |
  3. epics      | epic | the thread | members |
Column 2 of a project row may carry, as trailing " — "-joined suffixes (em-dash),
a [[charter]] wikilink and/or a bare CAMPAIGN/EPIC flag, and the cell may be **bold**;
the project name itself can contain an internal em-dash, so suffixes are removed by
identity (wikilink / flag word), never by splitting on the first em-dash.
"""
import os
import re

try:
    import config
    _DEFAULT_DOCS = config.DOCS_DIR
except Exception:  # pragma: no cover - config always importable in the app
    _DEFAULT_DOCS = os.getenv("DOCS_DIR", "/docs")

SOURCE_REL = os.path.join("Reference", "PROJECT-REGISTRY.md")
STATE_VOCAB = ("IDEA", "SCOPED", "REGISTERED", "ACTIVE", "PARKED", "SHIPPED", "CONCLUDED")

_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_BOLD = re.compile(r"\*\*(.*?)\*\*", re.DOTALL)
# " — [[wikilink]]" and " — CAMPAIGN/EPIC" suffixes (em-dash U+2014, matched literally).
_CHARTER_SUFFIX = re.compile(r"\s*—\s*\[\[[^\]]+\]\]")
_FLAG_SUFFIX = re.compile(r"\s*—\s*(?:CAMPAIGN|EPIC)\b")


def _strip_bold(s):
    return _BOLD.sub(r"\1", s)


def _first_wikilink(s):
    m = _WIKILINK.search(s or "")
    return m.group(1).strip() if m else ""


def _cells(row):
    """Split a markdown table row into trimmed cell strings (leading/trailing pipe removed)."""
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [c.strip() for c in row.split("|")]


def _is_separator(row):
    """True for a markdown separator row like ``|---|---|---|``.

    Cells are already trimmed by _cells, so a data cell such as ``#10`` cannot match
    ``[-:\\s]+`` (the ``#`` fails). This avoids the leading-whitespace false-positive
    that treats every data row as a separator.
    """
    cells = [c for c in _cells(row) if c]
    return bool(cells) and all(re.fullmatch(r"[-:\s]+", c) for c in cells)


def _read_rows(lines, i):
    """Collect contiguous table DATA rows from index i (separators skipped).

    Stops at the first blank line or non-table line. Returns (next_index, rows).
    """
    rows = []
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip() or not line.strip().startswith("|"):
            break
        if not _is_separator(line):
            rows.append(line)
        i += 1
    return i, rows


def _split_state(col3):
    """Return (state, state_detail) from a project's state cell.

    state: the leading vocabulary token (UPPERCASE) if it matches, else the raw
    trimmed text. state_detail: the remainder, de-parenthesised, "" if none.
    A trailing " — [[wikilink]]" (present on #30) is removed before parsing.
    """
    s = _strip_bold(col3).strip()
    s = _CHARTER_SUFFIX.sub("", s).strip()
    m = re.match(r"([A-Za-z]+)", s)
    token = m.group(1).upper() if m else ""
    if token in STATE_VOCAB:
        state = token
        detail = s[m.end():].strip()
    else:
        state = s
        detail = ""
    detail = detail.strip()
    if detail.startswith("(") and detail.endswith(")"):
        detail = detail[1:-1].strip()
    else:
        detail = detail.strip("()").strip()
    return state, detail.strip("—").strip()


def _project_name(col2):
    """Column-2 project name with charter wikilink + CAMPAIGN/EPIC flag suffixes removed
    (by identity, preserving any internal em-dash in the name) and bold stripped."""
    s = _CHARTER_SUFFIX.sub("", col2)
    s = _WIKILINK.sub("", s)          # any remaining bare wikilink
    s = _FLAG_SUFFIX.sub("", s)
    s = _strip_bold(s)
    return s.strip().strip("—").strip()


def _parse_project(row):
    cells = _cells(row)
    if len(cells) < 3:
        return None
    col1, col2, col3 = cells[0], cells[1], cells[2]
    m = re.search(r"#\s*(\d+)", col1)
    if not m:
        return None
    state, detail = _split_state(col3)
    return {
        "num": int(m.group(1)),
        "name": _project_name(col2),
        "state": state,
        "state_detail": detail,
        "is_campaign": bool(re.search(r"\bCAMPAIGN\b", col2)),
        "is_epic": bool(re.search(r"\bEPIC\b", col2)),
        # charter usually rides in col2; #30 carries it in col3 — accept either.
        "charter": _first_wikilink(col2) or _first_wikilink(col3),
    }


def _parse_campaign(row):
    cells = _cells(row)
    if len(cells) < 3:
        return None
    m = re.match(r"#\s*(\d+)\s+(.*)", cells[0].strip())
    if not m:
        return None
    return {
        "num": int(m.group(1)),
        "name": _strip_bold(m.group(2)).strip(),
        "members": cells[1].strip(),
        "charter": _first_wikilink(cells[2]),
    }


def _parse_epic(row):
    cells = _cells(row)
    if len(cells) < 3:
        return None
    name = _strip_bold(cells[0]).strip()
    if not name:
        return None
    return {
        "name": name,
        "thread": cells[1].strip(),
        "members": re.findall(r"#\d+", cells[2]),
    }


_HEADERS = {
    ("#", "project", "state"): ("projects", _parse_project),
    ("campaign", "member/child work", "charter"): ("campaigns", _parse_campaign),
    ("epic", "the thread", "members"): ("epics", _parse_epic),
}


def load_registry(docs_dir=None):
    """Parse the registry and return the board dict. Fail-soft: never raises."""
    docs_dir = docs_dir if docs_dir is not None else _DEFAULT_DOCS
    path = os.path.join(docs_dir, "Reference", "PROJECT-REGISTRY.md")
    result = {"projects": [], "campaigns": [], "epics": [], "next_free": None,
              "source": SOURCE_REL}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except Exception as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        return result
    try:
        lines = text.splitlines()
        i, n = 0, len(lines)
        while i < n:
            line = lines[i]
            if line.strip().startswith("|"):
                key = tuple(c.lower() for c in _cells(line))
                target = _HEADERS.get(key)
                if target:
                    field, parse = target
                    i, rows = _read_rows(lines, i + 1)
                    result[field] = [r for r in (parse(row) for row in rows) if r]
                    continue
            i += 1
        m = re.search(r"Next free:\s*#(\d+)", text)
        if m:
            result["next_free"] = int(m.group(1))
    except Exception as exc:  # pragma: no cover - defensive; parser is total on valid tables
        result["error"] = "parse: %s: %s" % (type(exc).__name__, exc)
    return result
