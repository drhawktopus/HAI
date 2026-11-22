"""Apply #30 code-cleanup dispositions to a source file — archive-first, ast-checked, re-scanned.

Consumes a source file, its detector candidates (code_narrative_scan --json, which carry exact
off_start/off_end char spans), and a dispositions map (candidate index -> KEEP-CLEAN | CUT | MOVE
with a rewrite). KEEP-CLEAN splices the terse rewrite in place of the narrative span, re-indented to
the original; CUT removes the span and its now-empty line; MOVE is left untouched and reported,
because its knowledge must be filed in a manual by hand first (files-twice). Splices by char offset
bottom-up so positions stay valid and a trailing comment never takes its code line with it. Refuses
to write if the result fails to parse, or if any candidate span no longer matches what was scanned.

Usage: python code_apply_cleanup.py <source.py> <candidates.json> <dispositions.json> [--apply --stamp <tag>]
       Dry-run by default (prints the plan); --apply archives the original then writes.
"""
import ast
import io
import importlib.util
import json
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _detector():
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "code_narrative_scan", os.path.join(here, "code_narrative_scan.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _reindent(new_text, indent):
    """First line sits right after the existing indent at the splice point; later lines get `indent`."""
    lines = new_text.split("\n")
    tail = [l for l in lines[1:] if l.strip()]
    common = min((len(l) - len(l.lstrip()) for l in tail), default=0)
    out = [lines[0].lstrip()]
    for l in lines[1:]:
        out.append(indent + l[common:] if l.strip() else "")
    return "\n".join(out)


def plan(src, candidates, disp):
    """Return (new_src, summary, notes). Raises on a coverage gap or a drifted span."""
    items = []
    for i, c in enumerate(candidates, 1):
        d = disp.get(i, disp.get(str(i)))
        if d is None:
            raise ValueError("candidate %d has NO disposition (parse gap) - refusing a partial plan" % i)
        items.append((i, c, d))
    items.sort(key=lambda t: t[1]["off_start"], reverse=True)  # bottom-up: lower offsets stay valid
    summary = {"KEEP-CLEAN": 0, "CUT": 0, "MOVE": 0}
    notes = []
    out = src
    for i, c, d in items:
        os_, oe = c["off_start"], c["off_end"]
        if out[os_:oe] != c["raw"]:
            raise ValueError("candidate %d span no longer matches scanned raw - source drifted, refusing" % i)
        name = str(d["disposition"]).upper().strip()
        line_start = out.rfind("\n", 0, os_) + 1
        lead = out[line_start:os_]
        indent = lead if lead.strip() == "" else ""       # true indent only for full-line comment/docstring
        if name == "KEEP-CLEAN":
            nt = (d.get("new_text") or "").rstrip("\n")
            if not nt.strip():
                raise ValueError("KEEP-CLEAN candidate %d has empty new_text" % i)
            out = out[:os_] + _reindent(nt, indent) + out[oe:]
            summary["KEEP-CLEAN"] += 1
        elif name == "CUT":
            a, b = os_, oe
            while a > 0 and out[a - 1] in " \t":
                a -= 1
            if (a == 0 or out[a - 1] == "\n") and b < len(out) and out[b] == "\n":
                b += 1                                     # full-line comment: take the whole line
            out = out[:a] + out[b:]
            summary["CUT"] += 1
        elif name == "MOVE":
            notes.append("MOVE left in place (file its knowledge first): cand %d L%s: %s"
                         % (i, c.get("line_start"), c["text"][:70]))
            summary["MOVE"] += 1
        else:
            raise ValueError("unknown disposition %r for candidate %d" % (name, i))
    return out, summary, notes


def _load_disp(raw):
    disp = {}
    rows = raw if isinstance(raw, list) else [dict(v, index=k) for k, v in raw.items()]
    for d in rows:
        disp[int(d["index"])] = d
    return disp


def _self_test():
    det = _detector()
    src = (
        "import os\n"
        "x = 3  # trailing note 2026-01-01, war story\n"
        "\n"
        "# a dated block 2026-02-02\n"
        "# second line, the owner said so\n"
        "def f():\n"
        '    """Docstring ' + "2026-03-03" + ': fixed after the outage."""\n'
        "    return x\n"
    )
    cands = det.scan_source(src, "t.py")
    assert len(cands) == 3, "expected 3 candidates (trailing, block, docstring), got %d" % len(cands)
    disp = {}
    for i, c in enumerate(cands, 1):
        if c["kind"] == "docstring":
            disp[i] = {"disposition": "KEEP-CLEAN", "new_text": '"""Return x."""'}
        else:
            disp[i] = {"disposition": "CUT"}
    new_src, summary, notes = plan(src, cands, disp)
    ast.parse(new_src)
    assert "x = 3" in new_src, "CUT ate the trailing-comment's code line: %r" % new_src
    assert "return x" in new_src, "lost code"
    assert '"""Return x."""' in new_src, "KEEP-CLEAN rewrite missing"
    for gone in ("war story", "outage", "the owner said", "dated block", "# trailing"):
        assert gone not in new_src, "narrative survived: %r in %r" % (gone, new_src)
    assert "x = 3\n" in new_src, "trailing-comment cut left junk on the code line: %r" % new_src
    assert not det.scan_source(new_src, "t.py"), "detector still finds narrative after apply"
    assert summary == {"KEEP-CLEAN": 1, "CUT": 2, "MOVE": 0}, summary
    print("apply self-test OK: code intact, narrative gone, rewrite placed, re-scan clean.")
    print("--- result ---\n%s---" % new_src)


def main(argv):
    if "--self-test" in argv:
        _self_test()
        return 0
    args = [a for a in argv[1:] if not a.startswith("--")]
    if len(args) < 3:
        print(__doc__)
        return 2
    src_path, cand_path, disp_path = args[0], args[1], args[2]
    src = io.open(src_path, encoding="utf-8").read()
    candidates = json.load(io.open(cand_path, encoding="utf-8"))
    disp = _load_disp(json.load(io.open(disp_path, encoding="utf-8")))
    new_src, summary, notes = plan(src, candidates, disp)
    try:
        ast.parse(new_src)
    except SyntaxError as e:
        print("ABORT: result does not parse: %s" % e)
        return 1
    remaining = _detector().scan_source(new_src, src_path)
    print("plan: KEEP-CLEAN=%d CUT=%d MOVE=%d | lines %d -> %d | narrative remaining after: %d"
          % (summary["KEEP-CLEAN"], summary["CUT"], summary["MOVE"],
             src.count("\n") + 1, new_src.count("\n") + 1, len(remaining)))
    for n in notes:
        print("  " + n)
    for r in remaining[:8]:
        print("  STILL-NARRATIVE L%s: %s" % (r["line_start"], r["text"][:70]))
    if "--apply" not in argv:
        print("(dry-run - pass --apply to archive + write)")
        return 0
    stamp = argv[argv.index("--stamp") + 1] if "--stamp" in argv else "manual"
    bak = "%s.preclean-%s" % (src_path, stamp)
    io.open(bak, "w", encoding="utf-8", newline="").write(src)
    io.open(src_path, "w", encoding="utf-8", newline="").write(new_src)
    print("APPLIED. archived original -> %s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
