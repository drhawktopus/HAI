"""Detect narrative comments and docstrings.

Uses `tokenize` for comments and `ast` for docstrings to avoid false positives
from strings or literals. Classification and rewriting are handled separately."""
import ast
import io
import json
import os
import re
import sys
import tokenize

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

DATE = re.compile(r"20\d\d[-/.]\d")
def _owner_name():
    """The owner's name: OWNER_USER in the environment, else the lab's settings; empty turns the signal off."""
    name = os.environ.get("OWNER_USER", "").strip()
    if not name:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import lab_settings
            name = (lab_settings.setting("OWNER_USER", "") or "").strip()
        except Exception:                                            # noqa: BLE001
            name = ""
    return name


def owner_rx(name):
    return re.compile(r"\b%s\b" % re.escape(name)) if name else None


# the owner's name in a comment is a story signal: a ruling quoted by who gave it
OWNER = owner_rx(_owner_name())
# a citation names WHERE a rule came from: a memory, a numbered doctrine class or row. The bare words "doctrine" and
# "incident" are ordinary terms in this code (a triage disposition, the stall rescue's incident window), not citations.
CITE = re.compile(r"\b(feedback-[a-z]|gotcha-[a-z]|doctrine (?:class |row )?\d|class \d+\b|row \d)", re.I)
QUOTE = re.compile(r'\*"[^"]{10,}"\*')  # a pasted, emphasised quote; bare long strings are too noisy
WARN = "\u26a0"  # the legacy war-story block marker
# A story needs no date: narrated time, what happened once, or what a line or tile "said" at the time. Plain words a
# rule may use (outage, yesterday, last time) are not signals; the drafter judges each candidate, so a rule that trips a
# signal costs one KEEP-CLEAN verdict, never a wrong cut.
INCIDENT = re.compile(
    r"\b(?:all (?:afternoon|night|morning|day) |one (?:evening|afternoon|morning|night)\b|that (?:night|morning|afternoon|evening)\b"
    r"|(?<![\"'])last (?:night|week)\b(?![\"'])|the other day"
    r"|once (?:believed|had|did|was|were|said|ran|took|lost|killed|rendered|read|made)\b"
    r"|nearly (?:lost|published|shipped|killed|deployed|reported|wrote)\b|almost (?:lost|published|shipped)\b"
    r"|bit (?:us|me)\b|cost (?:us|me) |burned (?:us|me|a night|an afternoon)|caught (?:it|this|us|me)\b"
    r"|the first live\b|on the first (?:run|call|try|night)\b|which is how (?:the|we|it|a) "
    r"|we (?:found|saw|learned|noticed|lost|watched|discovered|believed) |I (?:found|saw|lost|wrote|told|nearly|watched|believed|read) "
    r"|went (?:down|dark|blind|wrong|red|green)\b|(?:was|were|had been) (?:down|dead) (?:for|all|since)\b|hung for\b"
    r"|after the (?:outage|incident|crash)\b|a postmortem\b"
    r"|this (?:line|message|row|tile|field|endpoint) (?:said|read|showed|printed)\b|it is what it said\b)", re.I)

SKIP_DIRS = {"__pycache__", "backups", ".git", "node_modules", "workspaces",
             ".pytest_cache", ".venv", "venv", "coding-runner", "polyglot-benchmark"}   # the last: vendored Exercism exercises
# a virtual environment under any name (.venv-inspect: third-party code), and restore snapshots beside live source
SKIP_PREFIXES = (".venv", "_pre-sync-")
# upstream source trees the lab engine builds from, beside its own scripts: llama.cpp-<commit or name>,
# ollama-<version>-src, strata-<version> (third-party code, git-ignored there)
UPSTREAM_TREE_RX = re.compile(r"^(?:llama\.cpp-|ollama-\d|strata-\d)")
# Files whose comments are plain line comments: the prefixes that open one. Scanned with the same signals as Python
# comments (a story reads the same in a PowerShell header), kind "comment". Dockerfiles by name.
# Config files (yaml, json, env) are not code: their comments are not read.
PLAIN_COMMENT = {".ps1": ("#",), ".psm1": ("#",), ".sh": ("#",), ".bash": ("#",),
                 ".vbs": ("'",), ".cmd": ("rem ", "rem\t", "::"), ".bat": ("rem ", "rem\t", "::")}
PLAIN_NAMES = ("Dockerfile",)


def plain_prefixes(path):
    """The comment prefixes for a plain-comment file, or None for a file this scanner does not read as plain text."""
    base = os.path.basename(path)
    if base in PLAIN_NAMES or base.startswith("Dockerfile."):
        return ("#",)
    return PLAIN_COMMENT.get(os.path.splitext(base)[1].lower())


def scan_plain(src, path, prefixes):
    """Narrative candidates in a plain-comment file: each run of consecutive comment lines is one block, classified
    whole; offsets and the verbatim raw span are kept so an applier can splice by position."""
    cands, block, pos = [], [], 0
    lows = tuple(x.lower() for x in prefixes)

    def flush():
        if not block:
            return
        text = "\n".join(t for _, _, _, t in block)
        cls = classify(text)
        if cls:
            os_, oe = block[0][1], block[-1][2]
            cands.append({"path": path, "kind": "comment", "line_start": block[0][0], "line_end": block[-1][0],
                          "off_start": os_, "off_end": oe, "raw": src[os_:oe], "classes": cls, "text": text.strip()})
        block.clear()

    blocky = "#" in lows                    # PowerShell also has block comments: <# ... #>, every line inside is comment
    in_block = False
    for ln, line in enumerate(src.splitlines(keepends=True), 1):
        start, end = pos, pos + len(line.rstrip("\r\n"))
        pos += len(line)
        stripped = line.lstrip()
        low = stripped.lower()
        if blocky and (in_block or stripped.startswith("<#")):
            lead = len(line) - len(stripped)
            block.append((ln, start + lead, end, stripped.rstrip("\r\n").replace("<#", "").replace("#>", "").strip()))
            in_block = "#>" not in line
        elif any(low.startswith(x) for x in lows) and not low.startswith("#!"):
            lead = len(line) - len(stripped)
            block.append((ln, start + lead, end, stripped.rstrip("\r\n")))
        else:
            flush()
    flush()
    return cands



def skip_dir(name):
    """Whether a folder is outside the lab's own code: a named build/vendor/backup folder, a prefixed one, or an
    upstream source tree."""
    return name in SKIP_DIRS or name.startswith(SKIP_PREFIXES) or bool(UPSTREAM_TREE_RX.match(name))


def classify(text):
    """The narrative signals present in a block of comment/docstring text (empty list = clean)."""
    cls = []
    if DATE.search(text):
        cls.append("dated")
    if WARN in text:
        cls.append("warn_block")
    if OWNER is not None and OWNER.search(text):
        cls.append("owner")
    if CITE.search(text):
        cls.append("citation")
    if QUOTE.search(text):
        cls.append("quote")
    if INCIDENT.search(text):
        cls.append("incident")
    return cls


def scan_source(src, path="", embedded_js=False):
    """Narrative candidates in one Python source string. Never raises on malformed input.
    `embedded_js`: also the `//` and `/* */` comments inside a string literal that holds a page (`<script`), kind
    "jscomment" - for the story gate and the publish check; the cleanup pipeline drafts Python comments only.
    Each candidate carries exact char offsets (off_start/off_end) and the verbatim `raw` span, so an
    applier can splice a replacement by position — trailing comments (`x = 3  # c`) included."""
    prefixes = plain_prefixes(path) if path and not path.endswith(".py") else None
    if prefixes:
        return scan_plain(src, path, prefixes)
    cands = []
    source_lines = src.splitlines(keepends=True)
    los = [0]
    _run = 0
    for _ln in source_lines:
        _run += len(_ln)
        los.append(_run)

    def _off(pos):
        r, c = pos
        return (los[r - 1] if 0 < r <= len(los) else 0) + c

    def _ast_off(pos):
        """AST columns are UTF-8 byte offsets; convert them to Python character offsets."""
        r, byte_col = pos
        if not (0 < r <= len(source_lines)):
            return 0
        prefix = source_lines[r - 1].encode("utf-8")[:byte_col].decode("utf-8")
        return los[r - 1] + len(prefix)

    block = []

    def flush():
        if not block:
            return
        text = "\n".join(t.string for t in block)
        cls = classify(text)
        if cls:
            os_, oe = _off(block[0].start), _off(block[-1].end)
            cands.append({"path": path, "kind": "comment",
                          "line_start": block[0].start[0], "line_end": block[-1].start[0],
                          "off_start": os_, "off_end": oe, "raw": src[os_:oe],
                          "classes": cls, "text": text.strip()})
        block.clear()

    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        toks = []
    prev = None
    for tok in toks:
        if tok.type == tokenize.COMMENT:
            ln = tok.start[0]
            if prev is not None and ln > prev + 1:
                flush()
            block.append(tok)
            prev = ln
        elif tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                              tokenize.DEDENT, tokenize.ENCODING):
            flush()
            prev = None
    flush()

    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                ds = ast.get_docstring(node, clean=False)
                if not ds:
                    continue
                cls = classify(ds)
                if cls:
                    n0 = node.body[0]
                    os_ = _ast_off((n0.lineno, n0.col_offset))
                    oe = _ast_off((n0.end_lineno, n0.end_col_offset))
                    cands.append({"path": path, "kind": "docstring",
                                  "line_start": n0.lineno, "line_end": n0.end_lineno or n0.lineno,
                                  "off_start": os_, "off_end": oe, "raw": src[os_:oe],
                                  "classes": cls, "text": ds.strip()})
    if embedded_js:
        cands.extend(_embedded_js_comments(src, path))
        cands.extend(_embedded_code_comments(src, path))
    return cands


def _embedded_docstrings(value, lines, first, last, starts, path):
    """Docstrings of a Python script carried inside a string literal (a template run elsewhere): kind "codestring",
    located by the docstring's first line within the literal's source lines, so an applier can find it."""
    if "def " not in value and "class " not in value:
        return []
    try:
        sub = ast.parse(value)
    except (SyntaxError, ValueError):
        return []
    out = []
    for node in ast.walk(sub):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        ds = ast.get_docstring(node, clean=False)
        if not ds:
            continue
        cls = classify(ds)
        if not cls:
            continue
        probe = next((l.strip() for l in ds.splitlines() if l.strip()), "")[:40]
        hit = [i for i in range(first, last + 1) if probe and probe in lines[i - 1]]
        a = hit[0] if hit else first
        b = min(last, a + ds.count("\n"))
        os_ = starts[a - 1] + (len(lines[a - 1]) - len(lines[a - 1].lstrip()))
        oe = starts[b - 1] + len(lines[b - 1].rstrip("\r\n"))
        out.append({"path": path, "kind": "codestring", "line_start": a, "line_end": b, "off_start": os_, "off_end": oe,
                    "raw": "".join(lines[a - 1:b]), "classes": cls, "text": ds.strip()})
    return out


def _embedded_code_comments(src, path):
    """Comment runs (`#` lines) in the SOURCE lines of every multi-line string literal that holds a script body (a
    Python or PowerShell template run elsewhere): kind "codestring". A page (`<script`) is the js scanner's. Offsets are
    the source's, the same as every other candidate."""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []
    lines = src.splitlines(keepends=True)
    starts = [0]
    for ln in lines:
        starts.append(starts[-1] + len(ln))
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str) and "\n" in node.value
                and "<script" not in node.value):
            continue
        first, last = node.lineno, node.end_lineno or node.lineno
        if last <= first:
            continue
        out.extend(_embedded_docstrings(node.value, lines, first, last, starts, path))
        run = []

        def flush():
            if not run:
                return
            a, b = run[0], run[-1]
            text = " ".join(re.sub(r"^\s*#", "", lines[i - 1]).strip() for i in run)
            cls = classify(text)
            if cls:
                os_ = starts[a - 1] + (len(lines[a - 1]) - len(lines[a - 1].lstrip()))
                out.append({"path": path, "kind": "codestring", "line_start": a, "line_end": b,
                            "off_start": os_, "off_end": starts[b - 1] + len(lines[b - 1].rstrip("\r\n")),
                            "raw": "".join(lines[a - 1:b]).strip(), "classes": cls, "text": text})
            del run[:]

        for i in range(first + 1, last):          # the inner lines only: the quote lines are code, not the script
            if re.match(r"\s*#(?!!)", lines[i - 1]):
                run.append(i)
            else:
                flush()
        flush()
    return out


def _embedded_js_comments(src, path):
    """Comment runs in the SOURCE lines of every string literal holding `<script`: a run of `//` lines is one
    candidate, each `/* */` block another. Offsets are the source's, the same as every other candidate."""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []
    lines = src.splitlines(keepends=True)
    starts = [0]
    for ln in lines:
        starts.append(starts[-1] + len(ln))
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str) and "<script" in node.value):
            continue
        first, last = node.lineno, node.end_lineno or node.lineno
        run = []

        def flush():
            if not run:
                return
            a, b = run[0], run[-1]
            raw = "".join(lines[a - 1:b])
            text = " ".join(re.sub(r"^\s*//", "", lines[i - 1]).strip() for i in run)
            cls = classify(text)
            if cls:
                os_ = starts[a - 1] + (len(lines[a - 1]) - len(lines[a - 1].lstrip()))
                out.append({"path": path, "kind": "jscomment", "line_start": a, "line_end": b,
                            "off_start": os_, "off_end": starts[b - 1] + len(lines[b - 1].rstrip("\r\n")),
                            "raw": raw.strip(), "classes": cls, "text": text})
            del run[:]

        for i in range(first, last + 1):
            if re.match(r"\s*//", lines[i - 1]):
                run.append(i)
            else:
                flush()
        flush()
        seg_a, seg_b = starts[first - 1], starts[last]
        for m in re.finditer(r"/\*(.*?)\*/", src[seg_a:seg_b], re.S):
            text = " ".join(m.group(1).split())
            cls = classify(text)
            if cls:
                o = seg_a + m.start()
                a = src.count("\n", 0, o) + 1
                out.append({"path": path, "kind": "jscomment", "line_start": a,
                            "line_end": a + m.group(0).count("\n"), "off_start": o, "off_end": seg_a + m.end(),
                            "raw": m.group(0), "classes": cls, "text": text})
    return out


BUILD_DIRS = {"__pycache__", ".git", "node_modules", ".pytest_cache", ".venv", "venv", "polyglot-benchmark"}


def scan_path(path, embedded_js=False, whole_tree=False):
    """Scan a file or recurse a directory (skipping build/vendor/backup dirs). whole_tree: skip only build and vendor
    folders - for a tree that ships whole (the publish check), where every component it holds must be read."""
    if not os.path.exists(path):
        raise FileNotFoundError("no such path: %s (a missing path must not read as 'clean')" % path)
    if os.path.isfile(path):
        files = [path]
    else:
        files = []
        for root, dirs, names in os.walk(path):
            if whole_tree:
                dirs[:] = [d for d in dirs if d not in BUILD_DIRS]
            else:
                dirs[:] = [d for d in dirs if not skip_dir(d)]
            for n in names:
                if (n.endswith(".py") or plain_prefixes(n)) and ".bak" not in n:
                    files.append(os.path.join(root, n))
    out = []
    for f in sorted(files):
        try:
            src = io.open(f, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        out.extend(scan_source(src, f, embedded_js=embedded_js))
    return out


def _self_test():
    """The controls run with a known owner name, whatever this lab's setting is."""
    global OWNER
    saved, OWNER = OWNER, owner_rx("Alex")
    try:
        return _self_test_checks()
    finally:
        OWNER = saved


def _self_test_checks():
    """Positive control (must FIND) + negative control (must find NOTHING) — a scan that silently
    finds nothing reads exactly like a clean codebase, so validate the instrument before trusting it."""
    dirty = (
        '"""Module docstring — fixed 2026-08-01 after the outage."""\n'
        "x = 1  # plain constraint, no narrative: callers must hold the lock\n"
        "# " + WARN + " 2026-07-26: Alex said the card was lost; reboot cured it.\n"
        "# a second line of the same war-story block\n"
        "def f():\n"
        '    """Pin the property, per doctrine class 8."""\n'
        "    return x\n"
    )
    clean = (
        "# insert the script dir before sibling imports (._pth interpreters omit it)\n"
        "y = '2026-08-01 is data inside a string, not a comment'\n"
        "def g():\n"
        '    """Return y unchanged."""\n'
        "    return y\n"
    )
    d = scan_source(dirty, "dirty.py")
    c = scan_source(clean, "clean.py")
    ps_dirty = "# WHY (2026-08-07): the daemons had been dead for a day -- a story\n# second line\nparam($x)\n"
    ps_clean = "# Installs the logger as a SYSTEM task (boot + 15-min repeat).\nparam($x)\n# 2026 is in data: $y = 2026\n"
    pd = scan_source(ps_dirty, "dirty.ps1")
    assert len(pd) == 1 and pd[0]["kind"] == "comment" and pd[0]["line_start"] == 1 and pd[0]["line_end"] == 2, pd
    assert pd[0]["raw"].startswith("# WHY (2026-08-07)") and "dated" in pd[0]["classes"], pd[0]
    assert not scan_source(ps_clean, "clean.ps1"), "a dateless PowerShell header must read clean"
    vb = scan_source("' WHY (2026-08-20). The task ran on an interactive session\nSet sh = 1\n", "run.vbs")
    assert len(vb) == 1 and vb[0]["line_start"] == 1, vb
    dk = scan_source("FROM x\n# the 2026-10-17 gate caught the import error\nCOPY a .\n", "Dockerfile")
    assert len(dk) == 1 and dk[0]["line_start"] == 2, dk
    kinds = {x["kind"] for x in d}
    all_classes = {cl for x in d for cl in x["classes"]}
    for cand in (x for x in d if x["kind"] == "docstring"):
        assert cand["raw"].endswith(('"""', "'''")), \
            "docstring span swallowed following bytes (AST columns are UTF-8 bytes): %r" % cand["raw"]
    assert len(d) >= 3, "positive control must find the module docstring, the warn-block, and the citation docstring (got %d)" % len(d)
    assert "comment" in kinds and "docstring" in kinds, "must detect BOTH comments and docstrings: %r" % kinds
    assert {"dated", "warn_block", "owner", "citation"} <= all_classes, "missing a signal class: %r" % all_classes
    assert not c, "negative control must be clean — a date inside a STRING and a bare constraint comment are not narrative (got %r)" % c
    # the fixture's dates are built at run time, so this file's own page-string constants carry no story
    d1, d2 = "20" + "26-07-25", "20" + "26-08-01"
    page = ('PAGE = """<html><script>\n'
            "  // " + d1 + ": the owner asked for this button\n"
            "  // and said so twice\n"
            "  var a = 1;   // a plain rule: a is the first index\n"
            "  /* fixed " + d2 + " after the outage */\n"
            '</script></html>"""\n')
    js = [x for x in scan_source(page, "page.py", embedded_js=True) if x["kind"] == "jscomment"]
    assert len(js) == 2, "embedded page mode must find the dated // run and the dated /* */ block (got %r)" % js
    assert js[0]["raw"].startswith("//") and js[0]["line_start"] == 2 and js[0]["line_end"] == 3, js[0]
    assert page[js[0]["off_start"]:js[0]["off_end"]] == js[0]["raw"], "a jscomment span must be the source's bytes"
    assert not scan_source(page, "page.py"), "without embedded_js a page string is data, as before"
    # a story needs no date; a rule that explains itself is not one
    told = "# it is what it said all afternoon while the brain box was down\nz = 1\n# we found the file by its name\n"
    inc = scan_source(told, "told.py")
    assert len(inc) == 2 and all("incident" in x["classes"] for x in inc), "undated incident narration must be found: %r" % inc
    assert not scan_source("# a hung box is skipped for one minute, then asked again\nz = 1\n", "rule.py"), "a rule is clean"
    # a script carried as a string: its # lines are comments too (embedded mode), found by the same signals
    body = ('BODY = """\nimport subprocess\n# once believed a trainer was dead and rendered over it (' + d1 + ')\n'
            "# /create /f re-registers; /change prompts for a password\nprint(1)\n\"\"\"\n")
    cs = [x for x in scan_source(body, "body.py", embedded_js=True) if x["kind"] == "codestring"]
    assert len(cs) == 1 and cs[0]["line_start"] == 3 and cs[0]["line_end"] == 4, "a dated comment inside a script string: %r" % cs
    assert body[cs[0]["off_start"]:cs[0]["off_end"]] == cs[0]["raw"], "a codestring span must be the source's bytes"
    assert not [x for x in scan_source(body, "body.py") if x["kind"] == "codestring"], "without embedded mode a script string is data"
    upstream = ("llama.cpp-620d222-r5", "llama.cpp-latest", "ollama-0.40.16-src", "strata-0.1.45-lab")
    ours = ("patches", "rigby", "flashnext", "ollama_tools", "strata", "research")
    assert all(skip_dir(n) for n in upstream), [n for n in upstream if not skip_dir(n)]
    assert not any(skip_dir(n) for n in ours), [n for n in ours if skip_dir(n)]
    print("self-test OK: positive control found %d candidates (%s); negative control clean." % (len(d), ",".join(sorted(all_classes))))


def main(argv):
    if "--self-test" in argv:
        _self_test()
        return 0
    args = [a for a in argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    cands = []
    for a in args:                       # every path given: a later one must never be silently skipped
        cands.extend(scan_path(a, embedded_js="--embedded-js" in argv, whole_tree="--whole-tree" in argv))
    if "--json" in argv:
        print(json.dumps(cands, ensure_ascii=False, indent=2))
        return 0
    per_file = {}
    for c in cands:
        per_file.setdefault(c["path"], []).append(c)
    for path in sorted(per_file, key=lambda p: -len(per_file[p])):
        rows = per_file[path]
        classes = {}
        for r in rows:
            for cl in r["classes"]:
                classes[cl] = classes.get(cl, 0) + 1
        summary = " ".join("%s=%d" % (k, classes[k]) for k in sorted(classes))
        print("%4d  %s  [%s]" % (len(rows), path, summary))
    print("---\nTOTAL candidates: %d across %d files (%d path%s scanned)" % (len(cands), len(per_file), len(args),
                                                                         "" if len(args) == 1 else "s"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
