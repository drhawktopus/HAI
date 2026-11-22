"""Construct deterministic, size-bounded context packets for tool-less adversarial critics.

To prevent hallucinations from bare diffs and expose cross-boundary invariants,
this module assembles evidence without invoking the LLM:

    1. Full post-change content of touched files (read once).
    2. Call sites for every changed symbol (one grep per symbol).

Assembly is pure logic with injected I/O callables (read_file/find_refs),
enabling unit testing without a runner.
"""
from __future__ import annotations

import re
from typing import Callable

_FILE_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)", re.M)
_DEV_NULL = "/dev/null"
# symbols introduced/removed by the diff itself + the enclosing defs git names in hunk headers
_DEF_RE = re.compile(r"^[+-]\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", re.M)
_HUNK_RE = re.compile(r"^@@[^@]*@@\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", re.M)


def changed_files(diff: str) -> list[str]:
    """Post-change (b-side) paths the diff touches, de-duped in order; deleted files skipped."""
    out, seen = [], set()
    for m in _FILE_RE.finditer(diff or ""):
        path = m.group(2)
        if path and path != _DEV_NULL and path not in seen:
            seen.add(path)
            out.append(path)
    return out


def changed_symbols(diff: str, *, cap: int = 8) -> list[str]:
    """Function/class names the diff defines, removes, or edits inside — the things whose CALLERS a
    reviewer must see to judge a claimed behavior change. Test functions and dunders are skipped
    (nothing calls them by name). Order-preserving, de-duped, capped."""
    out, seen = [], set()
    for regex in (_DEF_RE, _HUNK_RE):
        for m in regex.finditer(diff or ""):
            name = m.group(1)
            if name.startswith("test_") or (name.startswith("__") and name.endswith("__")):
                continue
            if name not in seen:
                seen.add(name)
                out.append(name)
            if len(out) >= cap:
                return out
    return out


def _clip(text: str, limit: int, label: str) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return (text[:limit]
            + f"\n[NOTE: {label} truncated to {limit} chars for prompt length — the actual "
              "content is COMPLETE; do not report truncation as a finding.]")


def build_packet(diff: str, *,
                 read_file: Callable[[str], str | None],
                 find_refs: Callable[[str], list],
                 max_files: int = 4, max_file_chars: int = 8000,
                 max_ref_lines: int = 40, max_total: int = 16000) -> str:
    """The evidence packet for one critique: post-change file contents + call sites. Pure control —
    read_file(path) -> text|None and find_refs(symbol) -> ["path:line: text", ...] are injected.
    Every fetch is best-effort: a failed read/search just omits that section (the critic still has
    the diff). Bounded at every level so the prompt can't blow the context window."""
    diff = str(diff or "")
    sections: list[str] = []

    files = changed_files(diff)[:max_files]
    shown = set(files)
    for path in files:
        try:
            content = read_file(path)
        except Exception:
            content = None
        if content:
            sections.append(f"=== POST-CHANGE FILE: {path} ===\n"
                            + _clip(content, max_file_chars, f"file {path}"))

    ref_lines: list[str] = []
    for symbol in changed_symbols(diff):
        try:
            refs = find_refs(symbol) or []
        except Exception:
            refs = []
        for ref in refs:
            line = str(ref)
            # call sites in files already shown add nothing; keep the budget for elsewhere
            if any(line.startswith(p + ":") for p in shown):
                continue
            ref_lines.append(line)
            if len(ref_lines) >= max_ref_lines:
                break
        if len(ref_lines) >= max_ref_lines:
            break
    if ref_lines:
        sections.append("=== CALL SITES OF CHANGED SYMBOLS (elsewhere in the workspace) ===\n"
                        + "\n".join(ref_lines))

    if not sections:
        return ""
    packet = ("[REVIEW CONTEXT — assembled mechanically from the workspace so you can verify claims "
              "against the REAL code instead of guessing. Quote evidence from here or from the diff.]\n\n"
              + "\n\n".join(sections))
    return _clip(packet, max_total, "review context")



_PY_FILE_RE = re.compile(r"\b([a-z_][a-z0-9_]*\.py)\b")
_DOTTED_RE = re.compile(r"\b([a-z_][a-z0-9_]+\.[A-Za-z_]\w+)\b")          # module.func / obj.method
_SNAKE_RE = re.compile(r"\b([A-Za-z_]\w*_\w+)\b")                          # snake_case identifiers (have a _)
_STOP = {"e.g", "i.e", "etc", "e.g.", "test_verifiable", "approved_overnight"}


def extract_seed_symbols(text: str, *, cap: int = 8) -> list[str]:
    """Pull code-like identifiers from an objective/acceptance: `*.py` filenames, `module.func` dotted
    refs, and snake_case identifiers. Order-preserving, de-duped, capped. Heuristic on purpose — a
    wrong guess just wastes one grep; a right one saves the author several discovery turns."""
    out, seen = [], set()
    for regex in (_PY_FILE_RE, _DOTTED_RE, _SNAKE_RE):
        for m in regex.finditer(str(text or "")):
            sym = m.group(1)
            low = sym.lower()
            if low in _STOP or sym in seen or len(sym) < 4:
                continue
            seen.add(sym)
            out.append(sym)
            if len(out) >= cap:
                return out
    return out



# Split on ; / newline / a period FOLLOWED BY whitespace-or-end — never a bare '.', which would
# shred filenames ("app.py") and dotted refs ("overnight.parse_test_count") across clauses.
_CLAUSE_SPLIT_RE = re.compile(r"[;\n]|\.(?=\s|$)")
_NEG_MARKERS = (
    "not ", "n't ", "never ", "without ", "avoid ", "except ", "leave ", "keep ", "keeps ",
    "unchanged", "untouched", "as-is", "as is", "no change", "stays green", "stay green",
    "still pass", "still passes", "existing", "remain", "remains",
)
_EG_NORM_RE = re.compile(r"\be\.g\.", re.IGNORECASE)
_IE_NORM_RE = re.compile(r"\bi\.e\.", re.IGNORECASE)
_EXAMPLE_MARKERS = ("for example", "for instance", "such as", "example:")


def strip_negated_clauses(text: str) -> str:
    """Drop every clause (sentence fragment split on ``. ; newline``) that contains a negation /
    steady-state marker ("do NOT change cancel_task", "existing tests all pass") or an EXAMPLE
    marker ("keys survive, e.g. drive_tele" — illustrative data, not a demanded target), so neither
    ever turns into demanded scope targets. Coarse on purpose: a dropped positive clause only makes
    the gate MORE permissive (fail open), never bails an item."""
    normalized = _IE_NORM_RE.sub("that is", _EG_NORM_RE.sub("for example", str(text or "")))
    kept = []
    for clause in _CLAUSE_SPLIT_RE.split(normalized):
        low = clause.lower()
        if any(marker in low for marker in _NEG_MARKERS):
            continue
        if any(marker in low for marker in _EXAMPLE_MARKERS):
            continue
        kept.append(clause)
    return ". ".join(kept)


def strip_diff_comments(text: str) -> str:
    """Remove ``#``-comments from diff/code text (line-local).

    Preserves ``#`` inside string literals to keep data-key wiring matchable.
    Ignores triple-quoted docstrings since line-local scanning cannot parse them;
    this permissive bias is backstopped by B1.2 corroboration rules.
    """
    out = []
    for line in str(text or "").splitlines():
        in_single = in_double = False
        cut = None
        for i, ch in enumerate(line):
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == "#" and not in_single and not in_double:
                cut = i
                break
        out.append(line if cut is None else line[:cut])
    return "\n".join(out)


def extract_scope_targets(text: str, *, cap: int = 12) -> list[str]:
    """Symbols/filenames required by the spec. Shared by B1 and B2 gates.

    Filters out bare test symbols (e.g., ``test_fixture``) which appear in non-test code due
    to snake-case regex extraction but are invalid identifiers there. Keeps ``test_*.py`` filenames
    because file coverage counts tests and B2 keys off filenames."""
    return [t for t in extract_seed_symbols(strip_negated_clauses(text), cap=cap)
            if t.endswith(".py") or not t.rsplit(".", 1)[-1].startswith("test_")]


def build_seed_packet(symbols, *, read_file: Callable[[str], str | None],
                      find_refs: Callable[[str], list],
                      max_files: int = 4, max_file_chars: int = 6000,
                      max_ref_lines: int = 30, max_total: int = 12000) -> str:
    """Author seed packet: for each candidate symbol, grep the workspace (find_refs) to locate it, read
    the top files it lives in, and list where it's referenced. Bounded at every level. "" when nothing
    resolves (the author just falls back to exploring, as today). Best-effort per fetch."""
    # Resolve each symbol's referenced files up front, preserving per-symbol order (the first hit for a
    # symbol is its primary/definition site) so we can allocate the file budget FAIRLY across symbols.
    per_symbol_paths: list[list[str]] = []
    ref_lines: list[str] = []
    for symbol in list(symbols or []):
        try:
            refs = find_refs(symbol) or []
        except Exception:
            refs = []
        paths: list[str] = []
        for ref in refs:
            line = str(ref)
            path = line.split(":", 1)[0] if ":" in line else ""
            if path and path not in paths:
                paths.append(path)
            if len(ref_lines) < max_ref_lines:
                ref_lines.append(line)
        per_symbol_paths.append(paths)

    file_hits: list[str] = []          # ordered distinct file paths to read
    depth = 0
    while len(file_hits) < max_files and any(depth < len(p) for p in per_symbol_paths):
        for paths in per_symbol_paths:
            if depth < len(paths):
                path = paths[depth]
                if path not in file_hits and len(file_hits) < max_files:
                    file_hits.append(path)
        depth += 1

    sections: list[str] = []
    for path in file_hits:
        try:
            content = read_file(path)
        except Exception:
            content = None
        if content:
            sections.append(f"=== EXISTING FILE (touch here): {path} ===\n"
                            + _clip(content, max_file_chars, f"file {path}"))
    if ref_lines:
        sections.append("=== WHERE THE RELEVANT SYMBOLS ARE USED ===\n" + "\n".join(ref_lines[:max_ref_lines]))
    if not sections:
        return ""
    packet = ("[RELEVANT CODE — assembled mechanically from your workspace so you can go straight to the "
              "right place instead of searching. This is the CURRENT code; edit it to meet the objective.]\n\n"
              + "\n\n".join(sections))
    return _clip(packet, max_total, "author seed context")
