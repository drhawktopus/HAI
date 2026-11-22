"""library_split -- the DETERMINISTIC apply for the Obsidian wiki restructure (#30).

Splits one oversized library page into single-topic child pages, from a REVIEWED split spec.
No model, no paraphrase: it only MOVES existing section bytes and adds a mechanical child-links
list. The parent keeps its canonical path and becomes a stub (its pre-first-H2 preamble, verbatim,
plus a "Pages in this document" list) so every inbound `see FILE §heading` ref and RAG citation
still resolves. The pre-split original is archived verbatim (sha-verified) first, so the split is
reversible.

Spec (JSON):
  {"doc": "<abs path to parent>",
   "children": [{"file": "<basename>.md", "title": "# One sharp H1", "sections": ["## A", ...]}],
   "archive_dir": "<abs dir for the verbatim pre-split copy>"}

Every H2 section in the parent must be assigned to exactly one child (no drop, no dup, no invent) --
enforced mechanically; a mismatch refuses. Children are written FLAT beside the parent (RAG globs
`*.md` non-recursively in Reference/LLM-Project/Runbook, so a subdir would silently de-index).

`python library_split.py <spec.json> [--dry-run]`
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_sections(text):
    """Fence-aware split into (preamble, [(heading_line, section_text)]). A `## ` inside a
    code fence is body, never a heading. Section text INCLUDES its heading line."""
    lines = text.splitlines(keepends=True)
    preamble, sections = [], []
    cur_head, cur_body, in_fence = None, [], False
    for ln in lines:
        stripped = ln.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            (cur_body if cur_head is not None else preamble).append(ln)
            continue
        is_h2 = (not in_fence) and re.match(r"##\s+\S", ln) and not ln.startswith("###")
        if is_h2:
            if cur_head is not None:
                sections.append((cur_head, "".join(cur_body)))
            cur_head = ln.rstrip("\n")
            cur_body = [ln]
        elif cur_head is None:
            preamble.append(ln)
        else:
            cur_body.append(ln)
    if cur_head is not None:
        sections.append((cur_head, "".join(cur_body)))
    return "".join(preamble), sections


def _interior_h1(section_text):
    """Fence-aware check for a `# ` (H1) inside a moved section body -- a single-H1 doc should
    have none; its presence means the split assumption is wrong, so refuse rather than bury it."""
    in_fence = False
    for ln in section_text.splitlines():
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and re.match(r"#\s+\S", ln):
            return ln.strip()
    return None


def split_doc(spec, dry_run=False):
    doc = spec["doc"]
    children = spec["children"]
    if not os.path.isfile(doc):
        raise SystemExit("REFUSED: parent doc not found: %s" % doc)
    original = io.open(doc, encoding="utf-8").read()
    preamble, sections = parse_sections(original)
    if not sections:
        raise SystemExit("REFUSED: %s has no H2 sections to split" % doc)

    heads = [h for h, _ in sections]
    dupes = {h for h in heads if heads.count(h) > 1}
    if dupes:
        raise SystemExit("REFUSED: parent has duplicate H2 headings (ambiguous split): %s"
                         % sorted(dupes))
    body = dict(sections)

    # exhaustive + unique + exists assignment (no drop, no dup, no invent) -- mechanical
    assigned, seen = [], {}
    for c in children:
        for h in c["sections"]:
            if h not in body:
                raise SystemExit("REFUSED: child %r requests heading not in parent: %r"
                                 % (c["file"], h))
            if h in seen:
                raise SystemExit("REFUSED: heading assigned to two children (%r and %r): %r"
                                 % (seen[h], c["file"], h))
            seen[h] = c["file"]
            assigned.append(h)
    missing = [h for h in heads if h not in seen]
    if missing:
        raise SystemExit("REFUSED: %d parent heading(s) assigned to NO child (would be "
                         "dropped): %s" % (len(missing), missing))

    # build child files (title H1 + assigned sections in PARENT order, verbatim)
    outdir = os.path.dirname(doc)
    planned = []
    for c in children:
        title = c["title"].rstrip("\n")
        if not title.startswith("# ") or title.startswith("## "):
            raise SystemExit("REFUSED: child %r title must be one H1 ('# ...'): %r"
                             % (c["file"], title))
        ordered = [h for h in heads if seen.get(h) == c["file"]]
        parts = [title, ""]
        for h in ordered:
            sec = body[h]
            bad = _interior_h1(sec)
            if bad:
                raise SystemExit("REFUSED: section %r carries an interior H1 (%r) - the parent "
                                 "was not single-H1; resolve before splitting" % (h, bad))
            parts.append(sec.rstrip("\n"))
        child_text = "\n\n".join(parts).rstrip("\n") + "\n"
        cpath = os.path.join(outdir, c["file"])
        if os.path.exists(cpath) and os.path.abspath(cpath) != os.path.abspath(doc):
            raise SystemExit("REFUSED: child path already exists: %s" % cpath)
        planned.append((cpath, child_text, title, ordered))

    # parent stub = verbatim preamble + a mechanical child-links list (no model content)
    links = ["## Pages in this document", ""]
    for cpath, _txt, title, _o in planned:
        links.append("- **%s** — see `%s`" % (title[2:].strip(),
                                              os.path.basename(cpath)))
    stub = preamble.rstrip("\n") + "\n\n" + "\n".join(links) + "\n"

    report = {"doc": doc, "sections": len(sections), "children": len(children),
              "child_files": [os.path.basename(p) for p, _, _, _ in planned],
              "assignment": {os.path.basename(p): o for p, _, _, o in planned}}
    if dry_run:
        report["dry_run"] = True
        return report

    # archive the verbatim original FIRST (sha-verified) -- reversible
    os.makedirs(spec["archive_dir"], exist_ok=True)
    arch = os.path.join(spec["archive_dir"], os.path.basename(doc))
    io.open(arch, "w", encoding="utf-8", newline="").write(original)
    if _sha(io.open(arch, encoding="utf-8").read()) != _sha(original):
        raise SystemExit("REFUSED: archive sha mismatch for %s" % arch)

    # write children (atomic), sha-verify each
    for cpath, child_text, _t, _o in planned:
        tmp = cpath + ".tmp"
        io.open(tmp, "w", encoding="utf-8", newline="\n").write(child_text)
        os.replace(tmp, cpath)
        if _sha(io.open(cpath, encoding="utf-8").read()) != _sha(child_text):
            raise SystemExit("REFUSED: child write sha mismatch: %s" % cpath)

    # rewrite parent to the stub (atomic), sha-verify
    tmp = doc + ".tmp"
    io.open(tmp, "w", encoding="utf-8", newline="\n").write(stub)
    os.replace(tmp, doc)
    if _sha(io.open(doc, encoding="utf-8").read()) != _sha(stub):
        raise SystemExit("REFUSED: parent stub write sha mismatch: %s" % doc)

    report["archived"] = arch
    report["archived_sha"] = _sha(original)[:16]
    return report


def parse_h1_blocks(text):
    """Fence-aware split into (preamble, [(h1_line, block_text)]). A `# ` inside a code fence is
    body, never a boundary. Block text INCLUDES its H1 line. Mirrors parse_sections, at H1 level."""
    lines = text.splitlines(keepends=True)
    preamble, blocks = [], []
    cur_head, cur_body, in_fence = None, [], False
    for ln in lines:
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            (cur_body if cur_head is not None else preamble).append(ln)
            continue
        is_h1 = (not in_fence) and re.match(r"#\s+\S", ln) and not ln.startswith("##")
        if is_h1:
            if cur_head is not None:
                blocks.append((cur_head, "".join(cur_body)))
            cur_head = ln.rstrip("\n")
            cur_body = [ln]
        elif cur_head is None:
            preamble.append(ln)
        else:
            cur_body.append(ln)
    if cur_head is not None:
        blocks.append((cur_head, "".join(cur_body)))
    return "".join(preamble), blocks


def split_doc_h1(spec, dry_run=False):
    """Carve a MULTI-H1 parent at its H1 seams. The FIRST H1 block stays as the parent (verbatim
    content + a mechanical 'Pages in this document' links list); every subsequent H1 block is
    assigned to exactly one child file (verbatim, keeping its own single H1). Same safety spine as
    split_doc: archive->sha->atomic->sha, children FLAT, no model, refuse on drop/dup/invent.
    spec: {doc, children:[{file, h1}], archive_dir}."""
    doc = spec["doc"]
    children = spec["children"]
    if not os.path.isfile(doc):
        raise SystemExit("REFUSED: parent doc not found: %s" % doc)
    original = io.open(doc, encoding="utf-8").read()
    preamble, blocks = parse_h1_blocks(original)
    if len(blocks) < 2:
        raise SystemExit("REFUSED: %s is not multi-H1 (found %d H1 block(s)) - nothing to carve"
                         % (doc, len(blocks)))
    heads = [h for h, _ in blocks]
    dupes = {h for h in heads if heads.count(h) > 1}
    if dupes:
        raise SystemExit("REFUSED: parent has duplicate H1 headings (ambiguous carve): %s"
                         % sorted(dupes))
    block = dict(blocks)
    keep_head = heads[0]                       # the first H1 block always stays as the parent core

    # every NON-first H1 block -> exactly one child (no drop, no dup, no invent) -- mechanical
    seen = {}
    for c in children:
        h = c["h1"]
        if h not in block:
            raise SystemExit("REFUSED: child %r requests an H1 not in parent: %r" % (c["file"], h))
        if h == keep_head:
            raise SystemExit("REFUSED: child %r requests the KEPT (first) parent H1: %r"
                             % (c["file"], h))
        if h in seen:
            raise SystemExit("REFUSED: H1 assigned to two children (%r and %r): %r"
                             % (seen[h], c["file"], h))
        seen[h] = c["file"]
    missing = [h for h in heads[1:] if h not in seen]
    if missing:
        raise SystemExit("REFUSED: %d H1 block(s) assigned to NO child (would be dropped): %s"
                         % (len(missing), missing))

    outdir = os.path.dirname(doc)
    planned = []
    for c in children:
        title = c["h1"].rstrip("\n")
        if not title.startswith("# ") or title.startswith("## "):
            raise SystemExit("REFUSED: child %r h1 must be a single H1 ('# ...'): %r"
                             % (c["file"], title))
        child_text = block[c["h1"]].rstrip("\n") + "\n"       # the block verbatim, its own H1 atop
        cpath = os.path.join(outdir, c["file"])
        if os.path.exists(cpath) and os.path.abspath(cpath) != os.path.abspath(doc):
            raise SystemExit("REFUSED: child path already exists: %s" % cpath)
        planned.append((cpath, child_text, title))

    # parent stub = verbatim preamble + the KEPT first-H1 block + a mechanical child-links list
    links = ["## Pages in this document", ""]
    for cpath, _txt, title in planned:
        links.append("- **%s** — see `%s`" % (title[2:].strip(), os.path.basename(cpath)))
    stub = (preamble + block[keep_head]).rstrip("\n") + "\n\n" + "\n".join(links) + "\n"

    report = {"doc": doc, "h1_blocks": len(blocks), "children": len(children),
              "kept": keep_head, "child_files": [os.path.basename(p) for p, _, _ in planned]}
    if dry_run:
        report["dry_run"] = True
        return report

    os.makedirs(spec["archive_dir"], exist_ok=True)
    arch = os.path.join(spec["archive_dir"], os.path.basename(doc))
    io.open(arch, "w", encoding="utf-8", newline="").write(original)
    if _sha(io.open(arch, encoding="utf-8").read()) != _sha(original):
        raise SystemExit("REFUSED: archive sha mismatch for %s" % arch)

    for cpath, child_text, _t in planned:
        tmp = cpath + ".tmp"
        io.open(tmp, "w", encoding="utf-8", newline="\n").write(child_text)
        os.replace(tmp, cpath)
        if _sha(io.open(cpath, encoding="utf-8").read()) != _sha(child_text):
            raise SystemExit("REFUSED: child write sha mismatch: %s" % cpath)

    tmp = doc + ".tmp"
    io.open(tmp, "w", encoding="utf-8", newline="\n").write(stub)
    os.replace(tmp, doc)
    if _sha(io.open(doc, encoding="utf-8").read()) != _sha(stub):
        raise SystemExit("REFUSED: parent stub write sha mismatch: %s" % doc)

    report["archived"] = arch
    report["archived_sha"] = _sha(original)[:16]
    return report


def parse_h3_blocks(section_text):
    """Fence-aware split of ONE H2 section's text into (head_preamble, [(h3_line, h3_text)]).
    head_preamble = the `## ` line plus any text before the first `### `. h3_text INCLUDES its
    `### ` line. A `### ` inside a code fence is body; `#### `+ are body, not boundaries."""
    lines = section_text.splitlines(keepends=True)
    preamble, blocks = [], []
    cur_head, cur_body, in_fence = None, [], False
    for ln in lines:
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            (cur_body if cur_head is not None else preamble).append(ln)
            continue
        is_h3 = (not in_fence) and re.match(r"###\s+\S", ln) and not ln.startswith("####")
        if is_h3:
            if cur_head is not None:
                blocks.append((cur_head, "".join(cur_body)))
            cur_head = ln.rstrip("\n")
            cur_body = [ln]
        elif cur_head is None:
            preamble.append(ln)
        else:
            cur_body.append(ln)
    if cur_head is not None:
        blocks.append((cur_head, "".join(cur_body)))
    return "".join(preamble), blocks


def restructure_doc(spec, dry_run=False):
    """General disposition-aware library restructure -- the RUNBOOK-finale engine, reusable by HAI.
    Deterministic + byte-exact for everything moved verbatim; authored text (intro/quick-steps/
    pointers) is SUPPLIED in the spec, never generated here. Each top-level UNIT (an H2 section, or
    an H3 sub-block of an H2 named in `decompose`, plus a decomposed H2's own heading+preamble) is
    disposed EXACTLY once: pulled verbatim into a child page, retained verbatim in the rewritten
    master, or dropped (content leaves the doc; the verbatim original is archived and the master
    carries an authored pointer). Same safety spine as split_doc: archive->sha->atomic->sha,
    children FLAT, refuse on drop/dup/invent/interior-H1. Units are keyed by their exact heading
    line; an ambiguous heading (same H3 text in two decomposed H2s) refuses rather than guesses.

    spec: {doc, archive_dir,
           decompose: ["## H2", ...],                                  # optional
           children: [{file, title(H1 '# ..'), units: ["## H2"|"### H3", ...]}],
           master:  {title(H1), blocks: [{text}|{retain:"## H2"|"### H3"}|{index:true}]},
           drop: ["## H2"|"### H3", ...]}                              # explicit; never silent
    """
    doc = spec["doc"]
    if not os.path.isfile(doc):
        raise SystemExit("REFUSED: parent doc not found: %s" % doc)
    original = io.open(doc, encoding="utf-8").read()
    preamble, sections = parse_sections(original)
    if not sections:
        raise SystemExit("REFUSED: %s has no H2 sections" % doc)
    h2_heads = [h for h, _ in sections]
    dupes = {h for h in h2_heads if h2_heads.count(h) > 1}
    if dupes:
        raise SystemExit("REFUSED: parent has duplicate H2 headings: %s" % sorted(dupes))
    body = dict(sections)

    decompose = set(spec.get("decompose", []))
    unknown = decompose - set(h2_heads)
    if unknown:
        raise SystemExit("REFUSED: decompose lists H2(s) not in parent: %s" % sorted(unknown))

    # ordered unit map (key=exact heading line -> verbatim text); decomposed H2 -> its
    # heading+preamble unit (keyed by the H2 line) plus one unit per H3
    unit_text, unit_order = {}, []

    def _add(key, text):
        if key in unit_text:
            raise SystemExit("REFUSED: ambiguous heading (same line in two places) -- qualify or "
                             "rename: %r" % key)
        unit_text[key] = text
        unit_order.append(key)

    for h in h2_heads:
        if h in decompose:
            h2_pre, h3_blocks = parse_h3_blocks(body[h])
            _add(h, h2_pre)
            for h3_head, h3_text in h3_blocks:
                _add(h3_head, h3_text)
        else:
            _add(h, body[h])

    children = spec.get("children", [])
    master = spec["master"]
    drop = list(spec.get("drop", []))
    retained = [b["retain"] for b in master.get("blocks", []) if "retain" in b]

    # exhaustive + unique + exists: every unit assigned once across child/retain/drop
    seen = {}

    def _claim(key, who):
        if key not in unit_text:
            raise SystemExit("REFUSED: %s references a unit not in parent: %r" % (who, key))
        if key in seen:
            raise SystemExit("REFUSED: unit assigned twice (%s and %s): %r" % (seen[key], who, key))
        seen[key] = who

    for c in children:
        for u in c["units"]:
            _claim(u, "child %r" % c["file"])
    for u in retained:
        _claim(u, "master-retain")
    for u in drop:
        _claim(u, "drop")
    unassigned = [k for k in unit_order if k not in seen]
    if unassigned:
        raise SystemExit("REFUSED: %d unit(s) unassigned (would be dropped SILENTLY): %s"
                         % (len(unassigned), unassigned))

    outdir = os.path.dirname(doc)
    order_ix = {k: i for i, k in enumerate(unit_order)}
    planned = []
    for c in children:
        title = c["title"].rstrip("\n")
        if not title.startswith("# ") or title.startswith("## "):
            raise SystemExit("REFUSED: child %r title must be one H1 ('# ...'): %r"
                             % (c["file"], title))
        us = sorted(c["units"], key=lambda k: order_ix[k])
        parts = [title, ""]
        for u in us:
            sec = unit_text[u]
            bad = _interior_h1(sec)
            if bad:
                raise SystemExit("REFUSED: unit %r carries an interior H1 (%r)" % (u, bad))
            parts.append(sec.rstrip("\n"))
        child_text = "\n\n".join(parts).rstrip("\n") + "\n"
        cpath = os.path.join(outdir, c["file"])
        if os.path.exists(cpath) and os.path.abspath(cpath) != os.path.abspath(doc):
            raise SystemExit("REFUSED: child path already exists: %s" % cpath)
        planned.append((cpath, child_text, title))

    # master = H1 + ordered blocks (authored text | verbatim retains | auto child index)
    mtitle = master["title"].rstrip("\n")
    if not mtitle.startswith("# ") or mtitle.startswith("## "):
        raise SystemExit("REFUSED: master title must be one H1: %r" % mtitle)
    mparts = [mtitle]
    for b in master.get("blocks", []):
        if "text" in b:
            mparts.append(b["text"].rstrip("\n"))
        elif "retain" in b:
            sec = unit_text[b["retain"]]
            bad = _interior_h1(sec)
            if bad:
                raise SystemExit("REFUSED: retained unit %r carries an interior H1 (%r)"
                                 % (b["retain"], bad))
            mparts.append(sec.rstrip("\n"))
        elif b.get("index"):
            idx = ["## Pages in this document", ""]
            for cpath, _t, title in planned:
                idx.append("- **%s** — see `%s`" % (title[2:].strip(), os.path.basename(cpath)))
            mparts.append("\n".join(idx))
        else:
            raise SystemExit("REFUSED: master block is not text/retain/index: %r" % b)
    master_text = "\n\n".join(p for p in mparts if p != "").rstrip("\n") + "\n"

    report = {"doc": doc, "units": len(unit_order), "children": len(planned),
              "child_files": [os.path.basename(p) for p, _, _ in planned],
              "retained": retained, "dropped": drop}
    if dry_run:
        report["dry_run"] = True
        return report

    os.makedirs(spec["archive_dir"], exist_ok=True)
    arch = os.path.join(spec["archive_dir"], os.path.basename(doc))
    io.open(arch, "w", encoding="utf-8", newline="").write(original)
    if _sha(io.open(arch, encoding="utf-8").read()) != _sha(original):
        raise SystemExit("REFUSED: archive sha mismatch for %s" % arch)
    for cpath, child_text, _t in planned:
        tmp = cpath + ".tmp"
        io.open(tmp, "w", encoding="utf-8", newline="\n").write(child_text)
        os.replace(tmp, cpath)
        if _sha(io.open(cpath, encoding="utf-8").read()) != _sha(child_text):
            raise SystemExit("REFUSED: child write sha mismatch: %s" % cpath)
    tmp = doc + ".tmp"
    io.open(tmp, "w", encoding="utf-8", newline="\n").write(master_text)
    os.replace(tmp, doc)
    if _sha(io.open(doc, encoding="utf-8").read()) != _sha(master_text):
        raise SystemExit("REFUSED: master write sha mismatch: %s" % doc)
    report["archived"] = arch
    report["archived_sha"] = _sha(original)[:16]
    return report


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    spec = json.load(io.open(a.spec, encoding="utf-8"))
    spec.setdefault("archive_dir",
                    os.path.join(os.path.dirname(spec["doc"]) or ".",
                                 "_split-archive-%s"
                                 % datetime.date.today().isoformat()))
    if "master" in spec:
        report = restructure_doc(spec, dry_run=a.dry_run)
    elif spec.get("children") and "h1" in spec["children"][0]:
        report = split_doc_h1(spec, dry_run=a.dry_run)
    else:
        report = split_doc(spec, dry_run=a.dry_run)
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
