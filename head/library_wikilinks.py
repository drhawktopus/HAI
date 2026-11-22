"""Deterministic [[wikilink]] conversion for library docs.

Converts cross-references into Obsidian-style wikilinks using two passes:
  pass 1: `FILE(.md) §tail` -> `[[FILE#heading]]` (or `[[FILE]] §tail` if no exact heading)
  pass 2: markdown links `[t](path/FILE.md)` and bare `FILE.md` mentions -> `[[FILE]]`

Safety constraints:
  - Idempotent: re-running on identical input yields 0 edits.
  - Skips fenced code, headings, self-references, and non-vault paths (see REF_DIRS/LINK_DIRS).
  - Reverts lines that would result in unbalanced backticks.
  - Byte-safe: only matched spans are modified.
"""
import glob
import io
import os
import re
import time

REF_DIRS = ("Reference", "LLM-Project", "Runbook")            # sources we rewrite (rag.py's glob)
LINK_DIRS = ("Reference", "LLM-Project", "Runbook", "Notes", "Journal")  # valid link TARGETS
TRACKERS = {"CURRENT-PROJECTS.md", "PROJECT-BACKLOG.md"}
DENY = {"readme"}
ALLOWED_DIRS = {"reference", "llm-project", "runbook", "notes", "journal", "documentation"}
# curated lab-shorthand aliases -> real doc basename; only unambiguous vocabulary, each must exist.
ALIAS = {
    "elevation": "RUNBOOK-Elevation-and-Headless-Tasks",
    "charter": "LIBRARY-CHARTER",
    "internals": "HAI-Orchestrator-Internals",
    "host-onboarding": "HOST-ONBOARDING-Basement-Desktops",
    "tracker": "CURRENT-PROJECTS",
}

_PATH = r"(?:[A-Za-z]:[\\/])?(?:[\w.\-]+[\\/])*"
_FILE = r"(?P<file>[A-Za-z0-9][A-Za-z0-9][A-Za-z0-9._-]{2,})"
_HEAD = r'(?P<q>"[^"\n]{1,90}"|`[^`\n]{1,90}`)|(?P<b>[^\s.,;:)\]}`][^\n.,;:)\]}`]{0,89})'
_REF = re.compile(_PATH + _FILE + r"(?P<fb>`?)\s*§\s*(?:" + _HEAD + r")")
_MDLINK = re.compile(r"\[([^\]\n]*)\]\(([^)\n]+?)\)")
_BARE = re.compile(r"(?P<bt>`?)" + _PATH + r"(?P<file>[A-Za-z0-9][A-Za-z0-9][A-Za-z0-9._-]{2,})\.md(?P=bt)")
_WL = re.compile(r"\[\[([^\]\[|#]+)(?:#([^\]\[|]+))?(?:\|[^\]\[]+)?\]\]")


def _index(docs_dir):
    exists, lc, doc_headings = set(), {}, {}
    for d in LINK_DIRS:
        for f in glob.glob(os.path.join(docs_dir, d, "*.md")):
            base = os.path.basename(f)[:-3]
            exists.add(base)
            lc[base.lower()] = base
            hs, infence = [], False
            for ln in io.open(f, encoding="utf-8").read().split("\n"):
                s = ln.rstrip("\n")
                if s.lstrip().startswith("```"):
                    infence = not infence
                    continue
                if not infence and re.match(r"#{1,6} ", s):
                    hs.append(s.lstrip("#").strip())
            doc_headings[base] = hs
    children = {}
    for b in exists:
        for c in exists:
            if c != b and c.startswith(b + "-"):
                children.setdefault(b, []).append(c)
    return exists, lc, doc_headings, children


def find_doc(tok, lc):
    b = tok[:-3] if tok.lower().endswith(".md") else tok
    bl = b.lower()
    if bl in DENY:
        return None
    r = lc.get(bl) or ALIAS.get(bl)
    if r:
        return r
    pref = [v for k, v in lc.items() if re.match(r"\d+-" + re.escape(bl) + r"$", k)]
    return pref[0] if len(pref) == 1 else None


def _exact_heading(base, hl, doc_headings):
    for x in doc_headings.get(base, []):
        if x.lower() == hl.lower():
            return x
    return None


def _clean(raw):
    return re.sub(r"^see\s+", "", raw.strip()).strip().strip("`").strip()


def _path_ok(raw):
    parts = re.split(r"[\\/]", _clean(raw))
    if len(parts) == 1:
        return True
    parent = parts[-2]
    return parent.lower() in ALLOWED_DIRS or bool(re.match(r"^[A-Za-z]:$", parent))


def _pass1_line(line, cur_base, lc, doc_headings, children, conv, left, rel, i):
    """§-ref pass. Returns newline (byte-safe piece reconstruction) or the original on no change."""
    pieces, cur = [], 0
    line_convs = []
    for m in _REF.finditer(line):
        base = find_doc(m.group("file"), lc)
        ms, me = m.start(), m.end()
        if base is None:
            left.append((rel, i, line[ms:me].strip(), "file-not-a-doc"))
            continue
        if base == cur_base:
            left.append((rel, i, line[ms:me].strip(), "self-ref"))
            continue
        tail_raw = m.group("q") or m.group("b")
        hl = tail_raw.strip().strip('"').strip("`").strip("'").strip()
        anchor, target = _exact_heading(base, hl, doc_headings), base
        if not anchor:
            for c in children.get(base, []):
                a2 = _exact_heading(c, hl, doc_headings)
                if a2:
                    anchor, target = a2, c
                    break
        s, e = ms, me
        lead = s > 0 and line[s - 1] == "`"
        fadj = bool(m.group("fb"))
        if lead:
            s -= 1
        if lead and not fadj and e < len(line) and line[e] == "`":
            e += 1
        if anchor:
            repl = "[[%s#%s]]" % (target, anchor)
            kind = "anchor" if target == base else "anchor-child"
        else:
            tail_clean = tail_raw.replace("`", "").strip()
            repl = "[[%s]] §%s" % (base, tail_clean)
            kind = "page+pointer"
        pieces.append(line[cur:s])
        pieces.append(repl)
        cur = e
        line_convs.append((rel, i, line[ms:me].strip(), repl, kind))
    if not line_convs:
        return line, []
    pieces.append(line[cur:])
    newline = "".join(pieces)
    if line.count("`") % 2 == 0 and newline.count("`") % 2 == 1:
        return line, [("CONFLICT", rel, i, line)]           # would unbalance backticks: revert
    conv.extend(line_convs)
    return newline, []


def _pass2_line(line, cur, lc, conv, rel, i):
    """Bare-mention + markdown-link pass. Returns newline or original."""
    n = [0]

    def md_sub(m):
        if not _path_ok(m.group(2)):
            return m.group(0)
        base = find_doc(re.split(r"[\\/]", _clean(m.group(2)))[-1], lc)
        if base and base != cur:
            conv.append((rel, i, m.group(0), "[[%s]]" % base, "mdlink"))
            n[0] += 1
            return "[[%s]]" % base
        return m.group(0)

    def bare_sub(m):
        if not _path_ok(m.group(0)):
            return m.group(0)
        base = find_doc(m.group("file"), lc)
        if base and base != cur:
            conv.append((rel, i, m.group(0), "[[%s]]" % base, "bare"))
            n[0] += 1
            return "[[%s]]" % base
        return m.group(0)

    new = _MDLINK.sub(md_sub, line)
    new = _BARE.sub(bare_sub, new)
    if n[0] == 0:
        return line, []
    if line.count("`") % 2 == 0 and new.count("`") % 2 == 1:
        return line, [("CONFLICT", rel, i, line)]
    return new, []


def _convert_doc(text, cur_base, rel, lc, doc_headings, children):
    conv, left, conflicts = [], [], []
    # pass 1 (only lines with §)
    out, infence = [], False
    for i, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith("```"):
            infence = not infence
            out.append(line)
            continue
        if infence or "§" not in line:
            out.append(line)
            continue
        newline, confs = _pass1_line(line, cur_base, lc, doc_headings, children, conv, left, rel, i)
        conflicts.extend(confs)
        out.append(newline)
    # pass 2 (only lines with .md, not headings), on pass-1 output
    text2 = "\n".join(out)
    out2, infence = [], False
    for i, line in enumerate(text2.split("\n"), 1):
        if line.lstrip().startswith("```"):
            infence = not infence
            out2.append(line)
            continue
        if infence or ".md" not in line or line.lstrip().startswith("#"):
            out2.append(line)
            continue
        newline, confs = _pass2_line(line, cur_base, lc, conv, rel, i)
        conflicts.extend(confs)
        out2.append(newline)
    return "\n".join(out2), conv, left, conflicts


def plan_wikilinks(docs_dir):
    """Compute every deterministic conversion across REF_DIRS source docs (writes nothing)."""
    exists, lc, doc_headings, children = _index(docs_dir)
    edits, files, left, conflicts, scanned = [], {}, [], [], 0
    for d in REF_DIRS:
        for f in sorted(glob.glob(os.path.join(docs_dir, d, "*.md"))):
            bn = os.path.basename(f)
            if bn in TRACKERS:
                continue
            rel = "%s/%s" % (d, bn)
            text = io.open(f, encoding="utf-8").read()
            scanned += 1
            new, conv, lft, confs = _convert_doc(text, bn[:-3], rel, lc, doc_headings, children)
            edits.extend(conv)
            left.extend(lft)
            conflicts.extend(confs)
            if new != text:
                files[rel] = new
    return {"edits": edits, "files": files, "left": left, "conflicts": conflicts,
            "targets": len(exists), "scanned": scanned}


def verify_wikilinks(docs_dir):
    """Scan every [[wikilink]] in REF_DIRS; report targets with no vault file or unresolved #anchor,
    plus any [[-bearing line with an odd backtick count. This is the acceptance gate."""
    exists, lc, doc_headings, _ = _index(docs_dir)
    missing_file, missing_anchor, oddbt, total = [], [], [], 0
    for d in REF_DIRS:
        for f in sorted(glob.glob(os.path.join(docs_dir, d, "*.md"))):
            rel = "%s/%s" % (d, os.path.basename(f))
            infence = False
            for i, line in enumerate(io.open(f, encoding="utf-8").read().split("\n"), 1):
                if line.lstrip().startswith("```"):
                    infence = not infence
                    continue
                if infence:
                    continue
                probe = re.sub(r"`[^`]*`", "", line)
                for m in _WL.finditer(probe):
                    total += 1
                    tgt, anc = m.group(1).strip(), m.group(2)
                    if tgt not in exists:
                        missing_file.append((rel, i, m.group(0)))
                    elif anc and not _exact_heading(tgt, anc.strip(), doc_headings):
                        missing_anchor.append((rel, i, m.group(0)))
                if "[[" in line and line.count("`") % 2 == 1:
                    oddbt.append((rel, i, line.strip()))
    return {"scanned": total, "missing_file": missing_file,
            "missing_anchor": missing_anchor, "odd_backtick": oddbt}


def apply_wikilinks(docs_dir, dry_run=False, archive_dir=None):
    """Plan, then (unless dry_run) archive each touched file and write byte-safe. Post-write asserts:
    re-plan yields 0 edits (idempotent) AND verify yields 0 missing files/anchors (byte-safe)."""
    plan = plan_wikilinks(docs_dir)
    plan["applied"] = False
    if dry_run or not plan["files"]:
        plan["verify"] = verify_wikilinks(docs_dir)
        return plan
    if archive_dir is None:
        archive_dir = os.path.join(docs_dir, "Archive", "obsidian-wikilinks-%s"
                                   % time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(archive_dir, exist_ok=True)
    for rel in plan["files"]:
        src = os.path.join(docs_dir, rel.replace("/", os.sep))
        io.open(os.path.join(archive_dir, os.path.basename(rel)), "w",
                encoding="utf-8", newline="\n").write(io.open(src, encoding="utf-8").read())
        io.open(src, "w", encoding="utf-8", newline="\n").write(plan["files"][rel])
    after = plan_wikilinks(docs_dir)
    if after["files"]:
        raise RuntimeError("wikilink apply not idempotent: %d files still convert after write"
                           % len(after["files"]))
    v = verify_wikilinks(docs_dir)
    if v["missing_file"] or v["missing_anchor"]:
        raise RuntimeError("wikilink apply introduced %d broken links / %d broken anchors"
                           % (len(v["missing_file"]), len(v["missing_anchor"])))
    plan["applied"] = True
    plan["archive_dir"] = archive_dir
    plan["verify"] = v
    return plan
