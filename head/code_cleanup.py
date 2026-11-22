"""code_cleanup -- the engine behind HAI's #30 CODE-cleanup operator tools (the code half of the
library restructure). Sibling of library_restructure, for source files instead of docs.

Same order pipeline the docs sweep uses, at the same granularity: `code_propose_cleanup(mirror)`
sweeps every .py in a code mirror into ONE order (like `library_propose_split(domain)` over a domain's
docs); the 27B drafts a two-bar disposition per candidate on-box; `code_apply_cleanup(mirror)` applies
the whole set in one gated action (like `library_apply_split`). A single `file` narrows either end to
one file for a pilot.

Path model (runs in the orchestrator container):
  * code mirrors  -> CODE_ROOT/<mirror>   (/code-mirror/*, RW)     -- read the files, write the clean ones
  * detector/apply-> LAB_BIN              (/downloads/Lab/bin, ro) -- imported per call, one copy each
  * fleet staging -> STAGE_DIR            (/library-triage, RW)    -- work files + drafts, shared with
                     the factories via STAGE_UNC (the UNC the order args carry).

Detection and the byte-cut are deterministic code (code_narrative_scan + code_apply_cleanup, imported
from the mount so there is one copy of each), never a model; the model only proposes a disposition +
terse rewrite. Nothing writes a code file except the gated apply, archive-first.
"""
import ast
import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import textwrap
import time

# Same env vars as library_restructure -> same values, so the two lanes cannot drift onto different
# staging paths; read here directly to keep this module importable without the orchestrator runtime.
# An empty LIBRARY_STAGE_UNC means no staging share exists: orders that need it are refused.
STAGE_DIR = os.environ.get("LIBRARY_STAGE_DIR", "/library-triage")
STAGE_UNC = os.environ.get("LIBRARY_STAGE_UNC", "")
CODE_ROOT = os.environ.get("CODE_MIRROR_ROOT") or ("/code-mirror" if os.path.isdir("/code-mirror") else
                                                   os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # a checkout: the tree itself
CODE_PAYLOAD = "{FOUNDRY_ROOT}\\bin\\triage_code.py"

# logical mirror name -> its directory under CODE_ROOT. The folders are each lab's own: the setting CODE_MIRRORS (JSON,
# {"<mirror>": "<folder>"}) names them, and a bad value stops the head; the default is the public tree's layout.
CODE_MIRRORS = json.loads(os.environ.get("CODE_MIRRORS", "") or "{}") or {
    "orchestrator": "head", "foundry": "foundry", "tolkien": "tablet-foundry", "lab": "tools", "menu": "menu",
    "engine": "engine"}
# the detector and applier: CODE_LAB_BIN, else the lab-tools mirror's folder
LAB_BIN = os.environ.get("CODE_LAB_BIN", os.path.join(CODE_ROOT, CODE_MIRRORS.get("lab", "tools")))


def _lab_import(name, lab_bin=None):
    """Import a detector/applier module from the mounted Lab/bin (one copy, not a baked-in duplicate).
    code_apply_cleanup resolves its own code_narrative_scan from the same dir, so both stay in step."""
    path = os.path.join(lab_bin or LAB_BIN, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _mirror_root(mirror, code_root=None):
    if mirror not in CODE_MIRRORS:
        raise ValueError("unknown code mirror %r; known: %s" % (mirror, ", ".join(sorted(CODE_MIRRORS))))
    return os.path.abspath(os.path.join(code_root or CODE_ROOT, CODE_MIRRORS[mirror]))


def _code_path(mirror, rel, code_root=None):
    """Absolute path of a mirror-relative file, refusing any rel that escapes the mirror root."""
    root = _mirror_root(mirror, code_root)
    p = os.path.abspath(os.path.join(root, rel.replace("/", os.sep)))
    if os.path.commonpath([p, root]) != root:
        raise ValueError("path escapes the %r mirror root: %r" % (mirror, rel))
    return p


def _code_name(mirror, rel=None):
    """Staging name for an order: the whole mirror, or one file within it."""
    if not rel:
        return "s30-code-%s" % mirror
    slug = re.sub(r"[^A-Za-z0-9]+", "-", rel).strip("-").lower()
    return "s30-code-%s-%s" % (mirror, slug)


def _b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _context(src, c, before=6, after=8):
    """A window of the code around the candidate so the model sees what the comment annotates."""
    lines = src.splitlines()
    a = max(0, c["line_start"] - 1 - before)
    b = min(len(lines), c["line_end"] + after)
    return "\n".join(lines[a:b])


def _normalize_new_text(kind, text):
    """Return a replacement that can only be commentary, never copied context code.

    The model occasionally includes adjacent source after a valid first comment/docstring. Normalise
    formatting conservatively and fail closed if no commentary-only value can be isolated.
    """
    clean = textwrap.dedent(text or "").strip()
    if kind == "comment":
        kept = []
        for line in clean.splitlines():
            stripped = line.lstrip()
            if not stripped:
                if kept:
                    kept.append("")
            elif stripped.startswith("#"):
                kept.append(stripped)
            else:
                break
        while kept and not kept[-1]:
            kept.pop()
        if not kept:
            raise ValueError("KEEP-CLEAN comment has no comment-only replacement")
        return "\n".join(kept)
    if kind != "docstring":
        raise ValueError("unknown candidate kind %r" % kind)

    def _one_string(src):
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return None
        if (len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr)
                and isinstance(tree.body[0].value, ast.Constant)
                and isinstance(tree.body[0].value.value, str)):
            return ast.get_source_segment(src, tree.body[0]) or repr(tree.body[0].value.value)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                return repr(node.value.value)
        return None

    literal = _one_string(clean)
    if literal:
        return literal
    # Recover one well-formed triple-quoted literal even if copied context after it broke the parse.
    for quote in ('"""', "'''"):
        start = clean.find(quote)
        if start < 0:
            continue
        end = clean.find(quote, start + 3)
        if end >= 0:
            literal = _one_string(clean[start:end + 3])
            if literal:
                return literal
    # Some rows contain plain docstring prose (or comment-prefixed prose) with no quotes. Wrapping is
    # formatting only; executable-looking output remains a refusal.
    lines = clean.splitlines()
    if lines and all((not line.strip()) or line.lstrip().startswith("#") for line in lines):
        clean = "\n".join(line.lstrip()[1:].lstrip() if line.strip() else "" for line in lines).strip()
    if re.search(r"(?m)^\s*(?:def|class|from|import|return|raise|if\b|elif\b|else:|for\b|while\b|try:|except\b|with\b|@|[A-Za-z_]\w*\s*=)", clean):
        raise ValueError("KEEP-CLEAN docstring contains executable-looking text")
    if not clean:
        raise ValueError("KEEP-CLEAN docstring is empty")
    return repr(clean)


def _semantic_ast(src):
    """AST fingerprint with docstring values erased; comments/docstring prose may be the only delta."""
    tree = ast.parse(src[1:] if src.startswith("\ufeff") else src)

    class _EraseDocstrings(ast.NodeTransformer):
        @staticmethod
        def _erase(node):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                del body[0]
            return node

        def visit_Module(self, node):
            self.generic_visit(node)
            return self._erase(node)

        def visit_FunctionDef(self, node):
            self.generic_visit(node)
            return self._erase(node)

        def visit_AsyncFunctionDef(self, node):
            self.generic_visit(node)
            return self._erase(node)

        def visit_ClassDef(self, node):
            self.generic_visit(node)
            return self._erase(node)

    return ast.dump(_EraseDocstrings().visit(tree), include_attributes=False)

def _resolve_candidates(src, frel, staged, det):
    """Re-scan before planning and borrow only fresh byte spans from an identical candidate set.

    This both preserves the original drift refusal and lets a corrected detector repair old staged
    offsets (notably Python AST's UTF-8 byte columns) without changing the model's dispositions.
    """
    live = det.scan_source(src, frel)
    if len(live) != len(staged):
        raise ValueError("%s candidate count drifted (%d staged, %d live)" %
                         (frel, len(staged), len(live)))
    out = []
    for old, now in zip(staged, live):
        old_key = (old.get("kind"), old.get("line_start"), old.get("line_end"), old.get("text"))
        now_key = (now.get("kind"), now.get("line_start"), now.get("line_end"), now.get("text"))
        if old_key != now_key:
            raise ValueError("%s candidate set drifted at %s" % (frel, old.get("id")))
        out.append(dict(old, off_start=now["off_start"], off_end=now["off_end"], raw=now["raw"]))
    return out

def _sweep_files(root, det):
    """Every .py under the mirror root, skipping the detector's build/vendor/backup dirs and .bak."""
    skip = getattr(det, "SKIP_DIRS", set())
    # the detector's own rule (named folders, a virtual environment under any name, restore snapshots); the inline
    # check is the fallback for a detector without it. Pre-sync snapshots are restore evidence, not source to rewrite.
    skip_dir = getattr(det, "skip_dir", None) or (lambda d: d in skip or d.startswith("_pre-sync-"))
    out = []
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not skip_dir(d)]
        for nm in names:
            if nm.endswith(".py") and ".bak" not in nm:
                out.append(os.path.relpath(os.path.join(dirpath, nm), root).replace(os.sep, "/"))
    return sorted(out)


def _write_work(stage_dir, name, work):
    os.makedirs(stage_dir, exist_ok=True)
    p = os.path.join(stage_dir, name + ".work.json")
    tmp = p + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(work, indent=1, ensure_ascii=False))
    # A rerun reuses the staging name. Retire the prior result before publishing new work so the
    # apply gate cannot join fresh candidates to stale dispositions while the replacement runs. The prior work goes
    # with it under the same stamp: drafts are only readable beside the candidates they were made from.
    stamp = time.time_ns()
    drafts = os.path.join(stage_dir, name + ".drafts.json")
    if os.path.exists(drafts):
        os.replace(drafts, drafts + ".superseded-%d" % stamp)
    if os.path.exists(p):
        os.replace(p, p + ".superseded-%d" % stamp)
    os.replace(tmp, p)


def build_code_work(mirror, rel=None, stage_dir=None, code_root=None, lab_bin=None):
    """Scan a code mirror for narrative candidates and write the payload's work file. With `rel` it is
    one file; without, it SWEEPS every .py in the mirror (the docs-sweep shape -- one order for the
    whole codebase). Carries the full detector candidates (offsets + raw + file) under `candidates` for
    the gated apply's byte-splice; the payload reads only `items`. Candidate ids are globally unique so
    the apply can join drafts across files. Raises on a clean target -- nothing to clean is not an order."""
    stage_dir = stage_dir or STAGE_DIR
    det = _lab_import("code_narrative_scan", lab_bin)
    files = [rel] if rel else _sweep_files(_mirror_root(mirror, code_root), det)
    items, candidates, n = [], [], 0
    for frel in files:
        src = io.open(_code_path(mirror, frel, code_root), encoding="utf-8").read()
        for c in det.scan_source(src, "%s/%s" % (mirror, frel)):
            n += 1
            cid = "%04d" % n
            items.append({"id": cid, "kind": c["kind"], "file": frel,
                          "lines": "%s-%s" % (c["line_start"], c["line_end"]),
                          "text_b64": _b64(c["text"]), "context_b64": _b64(_context(src, c))})
            candidates.append(dict(c, id=cid, file=frel))
    if not candidates:
        where = "%s/%s" % (mirror, rel) if rel else "mirror %r" % mirror
        raise ValueError("%s has no narrative candidates - nothing to clean" % where)
    name = _code_name(mirror, rel)
    work = {"version": "code-work-v2", "task": "code", "mirror": mirror, "file": rel,
            "files": files, "items": items, "candidates": candidates}
    _write_work(stage_dir, name, work)
    return {"name": name, "candidates": len(candidates), "files": len(files),
            "work_unc": "%s\\%s.work.json" % (STAGE_UNC, name),
            "drafts_unc": "%s\\%s.drafts.json" % (STAGE_UNC, name)}


# Judgment work states its need and the desk weighs the menu (the routing checklist's legacy row "Orders that name a
# model"): the work family and the lead tier's quality band; every row in that band is weighed on quality, time and
# energy, and the chosen row fills the model and its window. Background work yields to live work (kind "review").
FAMILY, TIER, KIND = "work-w1", "lead", "review"


def _code_order_spec(name):
    """A compute order for the judgment work; the model and window come from the weighed row."""
    return {"name": name, "mode": "compute", "load": "gpu", "payload": CODE_PAYLOAD,
            "args": ["%s\\%s.work.json" % (STAGE_UNC, name),
                     "%s\\%s.drafts.json" % (STAGE_UNC, name),
                     "--model", "{POLICY_MODEL}", "--num-ctx", "{POLICY_NUM_CTX}",
                     "{POLICY_THINK_ARGS}", "{POLICY_TEMPERATURE_ARGS}"],
            "notify": "failures"}


def code_order_body(name):
    """The desk's order body: the spec, its needs, and no model name."""
    return {"spec": _code_order_spec(name), "requires": ["order.compute"], "kind": KIND, "family": FAMILY,
            "tier": TIER, "urgency": "background"}


def order_in_flight(name, jobs=None):
    """Returns the active order for the given staging name, or None.

    The order name format is `<name>_<host>`. This check prevents concurrent execution, as a second order would alter the staged inputs of the first and cause double-spending of resources."""
    if jobs is None:
        import p20_queue
        jobs = p20_queue.all_jobs()
    for j in jobs:
        order = str(j.get("order_name") or "")
        if (order == name or order.startswith(name + "_")) and j.get("status") in ("pending", "held", "claimed", "running"):
            return j
    return None


def submit_code_order(name, submit_fn=None, desk=None):
    """Submit the code-cleanup order at the desk (the review order's front door): Spock weighs the work family's tier
    and fills the model from the chosen row; a held order waits at the desk for a box."""
    body = code_order_body(name)
    if submit_fn:
        return submit_fn(body["spec"], body["requires"])
    if desk is None:
        import app as desk
    reply = desk.p20_order(desk.FactoryOrderReq(**body))
    if reply.get("ok") and reply.get("held"):
        placed = [{"host": "held at the desk", "id": reply.get("id"), "order": name}]
    else:
        placed = [{"host": s.get("host"), "id": s.get("id"), "order": name}
                  for s in (reply.get("steps") or []) if isinstance(s, dict) and s.get("id")]
    if not reply.get("ok") or not placed:
        raise RuntimeError("the desk refused the code-cleanup order: %s" % (
            reply.get("error") or "; ".join(reply.get("why") or []) or str(reply)[:200]))
    return {"placed": placed, "why": reply.get("why", []), "drafts_unc": "%s\\%s.drafts.json" % (STAGE_UNC, name)}


def read_code_work(name, stage_dir=None):
    p = os.path.join(stage_dir or STAGE_DIR, name + ".work.json")
    return json.load(io.open(p, encoding="utf-8")) if os.path.exists(p) else None


def read_code_drafts(name, stage_dir=None):
    p = os.path.join(stage_dir or STAGE_DIR, name + ".drafts.json")
    return json.load(io.open(p, encoding="utf-8")) if os.path.exists(p) else None


def _file_sha256(path):
    """sha256 of a file's bytes; None when it cannot be read. A missing file counts as changed."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _apply_fingerprint(name, stage_dir, targets, mirror, code_root=None):
    """Hash of every input a plan is built from: the work file, the drafts file, and each target
    source. A gated apply must reproduce these bytes exactly, otherwise the text written is not the
    text the approver was shown."""
    stage = stage_dir or STAGE_DIR
    return {"version": "code-apply-fingerprint-v1", "name": name,
            "work_sha256": _file_sha256(os.path.join(stage, name + ".work.json")),
            "drafts_sha256": _file_sha256(os.path.join(stage, name + ".drafts.json")),
            "sources": {frel: _file_sha256(_code_path(mirror, frel, code_root)) for frel in targets}}


def _fingerprint_mismatch(expected, actual):
    """Every input whose bytes differ between preview and confirmation; empty list means identical.
    A preview that carried no usable fingerprint is itself a mismatch: nothing may be written on an
    unanchored approval."""
    if not isinstance(expected, dict) or not expected.get("work_sha256") \
            or not expected.get("drafts_sha256") or not expected.get("sources"):
        return ["the preview carried no fingerprint to anchor the apply to"]
    bad = []
    for key, label in (("work_sha256", "work file"), ("drafts_sha256", "drafts file")):
        if expected.get(key) != actual.get(key):
            bad.append("%s changed after the preview" % label)
    exp = expected.get("sources") or {}
    got = actual.get("sources") or {}
    for frel in sorted(set(exp) | set(got)):
        if exp.get(frel) != got.get(frel):
            bad.append("source %s changed after the preview" % frel)
    return bad


def code_apply(name, rel=None, stage_dir=None, code_root=None, lab_bin=None, dry_run=True, stamp="manual",
               expect_fingerprint=None):
    """Apply the drafted dispositions from a sweep's work + drafts. Without `rel`, every file in the
    work; with `rel`, only that file. Plans ALL target files first (drift / partial-draft / parse
    checks via code_apply_cleanup.plan) and only then writes any -- so one bad file aborts the whole
    apply before touching disk. Archive-first per file. dry_run returns the plan without writing.

    `expect_fingerprint` is the fingerprint a dry-run preview returned. It is checked before any
    planning, archiving or writing: if the work file, the drafts or a target source moved since the
    preview, the apply refuses and nothing on disk changes. What the approver previewed is exactly
    what lands.
    """
    work = read_code_work(name, stage_dir)
    if not work:
        raise ValueError("no work file for %r - run build_code_work first" % name)
    drafts = read_code_drafts(name, stage_dir)
    if not drafts:
        raise ValueError("no drafts for %r yet - the code order has not finished" % name)
    mirror = work["mirror"]
    by_id = {r.get("id"): r for r in drafts.get("results", [])}
    by_file = {}
    for c in work["candidates"]:
        by_file.setdefault(c["file"], []).append(c)
    if rel is not None and rel not in by_file:
        raise ValueError("file %r has no candidates in work %r" % (rel, name))
    targets = [rel] if rel is not None else sorted(by_file)

    fingerprint = _apply_fingerprint(name, stage_dir, targets, mirror, code_root)
    if expect_fingerprint is not None:
        bad = _fingerprint_mismatch(expect_fingerprint, fingerprint)
        if bad:
            raise ValueError("refusing to apply: " + "; ".join(bad)
                             + " - run code_apply_cleanup again to see the current plan")

    applier = _lab_import("code_apply_cleanup", lab_bin)
    det = _lab_import("code_narrative_scan", lab_bin)

    plans = []
    for frel in targets:
        fc = by_file[frel]
        path = _code_path(mirror, frel, code_root)
        src = io.open(path, encoding="utf-8").read()
        fc = _resolve_candidates(src, frel, fc, det)
        disp, shape_notes = {}, []
        for i, c in enumerate(fc, 1):        # plan() keys disp by 1-based position within THIS file
            r = by_id.get(c["id"])
            if r is None or r.get("error") or not r.get("disposition"):
                raise ValueError("candidate %s (%s) has no clean draft - refusing partial apply"
                                 % (c["id"], frel))
            new_text = r.get("new_text") or ""
            if r["disposition"] == "KEEP-CLEAN":
                normal = _normalize_new_text(c["kind"], new_text)
                if normal != new_text:
                    shape_notes.append("shape-normalized %s in %s" % (c["id"], frel))
                new_text = normal
            disp[i] = {"disposition": r["disposition"], "new_text": new_text}
        new_src, summary, notes = applier.plan(src, fc, disp)   # raises on a drifted span or coverage gap
        ast.parse(new_src[1:] if new_src.startswith("\ufeff") else new_src)  # preserve BOM, parse content
        if _semantic_ast(src) != _semantic_ast(new_src):
            raise ValueError("cleanup changed executable AST in %s - refusing non-comment diff" % frel)
        remaining = det.scan_source(new_src, frel)
        plans.append({"frel": frel, "path": path, "src": src, "new_src": new_src,
                      "summary": summary, "notes": notes + shape_notes,
                      "remaining": len(remaining)})

    totals = {"KEEP-CLEAN": 0, "CUT": 0, "MOVE": 0}
    notes_all = []
    for p in plans:
        for k in totals:
            totals[k] += p["summary"].get(k, 0)
        notes_all.extend(p["notes"])
    files_out = [{"file": p["frel"], "summary": p["summary"], "remaining": p["remaining"],
                  "lines_before": p["src"].count("\n") + 1, "lines_after": p["new_src"].count("\n") + 1}
                 for p in plans]
    result = {"name": name, "mirror": mirror, "n_files": len(plans), "totals": totals,
              "notes": notes_all, "remaining": sum(p["remaining"] for p in plans),
              "files": files_out, "fingerprint": fingerprint}
    if dry_run:
        result["dry_run"] = True
        return result
    # Archive every original before changing any source file. Refuse to overwrite an existing
    # archive: a repeated confirmation must never erase the first recoverable pre-clean copy.
    archived = []
    for p in plans:
        bak = "%s.preclean-%s" % (p["path"], stamp)
        with io.open(bak, "x", encoding="utf-8", newline="") as f:
            f.write(p["src"])
        archived.append(os.path.relpath(bak, _mirror_root(mirror, code_root)).replace(os.sep, "/"))

    # Replace through sibling temp files. If a later file cannot be written, restore every
    # already-replaced source from the in-memory originals before surfacing the failure.
    written = []
    try:
        for p in plans:
            tmp = p["path"] + ".code-cleanup.tmp"
            with io.open(tmp, "w", encoding="utf-8", newline="") as f:
                f.write(p["new_src"])
            os.replace(tmp, p["path"])
            written.append(p)
    except Exception:
        for p in reversed(written):
            tmp = p["path"] + ".code-cleanup.rollback.tmp"
            with io.open(tmp, "w", encoding="utf-8", newline="") as f:
                f.write(p["src"])
            os.replace(tmp, p["path"])
        raise
    result["applied"] = True
    result["archived"] = archived
    return result


def _self_test():
    """Positive control end-to-end with a temp mirror of TWO files + simulated drafts: sweep -> apply
    both -> re-scan. Detector/applier come from the REAL Lab/bin; only the files + staging are temp."""
    import tempfile
    real_lab = os.environ.get("CODE_LAB_BIN_TEST") or LAB_BIN
    root = tempfile.mkdtemp(prefix="codeclean-")
    stage = os.path.join(root, "stage")
    files = {
        "bin/a.py": ('import os\n'
                     'x = 3  # trailing note 2026-01-01, war story\n'
                     '\n'
                     '# a dated block 2026-02-02\n'
                     '# second line, Alex said so\n'
                     'def f():\n'
                     '    """Docstring ' + "2026-03-03" + ': fixed after the outage."""\n'
                     '    return x\n'),
        "bin/b.py": ('# per doctrine class 8, keep it\n'
                     'y = 1\n'),
    }
    for rel, src in files.items():
        p = os.path.join(root, "Lab", rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        io.open(p, "w", encoding="utf-8", newline="").write(src)

    info = build_code_work("lab", stage_dir=stage, code_root=root, lab_bin=real_lab)  # SWEEP
    assert info["files"] == 2 and info["candidates"] == 4, info    # 3 in a.py + 1 in b.py
    work = read_code_work(info["name"], stage)
    results = []
    for it in work["items"]:
        if it["kind"] == "docstring":
            results.append({"id": it["id"], "disposition": "KEEP-CLEAN", "new_text": '"""Return x."""'})
        else:
            results.append({"id": it["id"], "disposition": "CUT", "new_text": ""})
    io.open(os.path.join(stage, info["name"] + ".drafts.json"), "w", encoding="utf-8",
            newline="\n").write(json.dumps({"results": results}, ensure_ascii=False))

    prev = code_apply(info["name"], stage_dir=stage, code_root=root, lab_bin=real_lab, dry_run=True)
    assert prev["n_files"] == 2 and prev["totals"] == {"KEEP-CLEAN": 1, "CUT": 3, "MOVE": 0}, prev
    done = code_apply(info["name"], stage_dir=stage, code_root=root, lab_bin=real_lab,
                      dry_run=False, stamp="selftest")
    assert done.get("applied") and len(done["archived"]) == 2, done
    a = io.open(os.path.join(root, "Lab", "bin", "a.py"), encoding="utf-8").read()
    assert "x = 3\n" in a and "return x" in a and '"""Return x."""' in a, a
    for gone in ("war story", "outage", "Alex said", "dated block"):
        assert gone not in a, "narrative survived in a.py: %r" % gone
    # narrow apply of just one file also works from the same sweep
    root2 = tempfile.mkdtemp(prefix="codeclean2-")
    stage2 = os.path.join(root2, "stage")
    for rel, src in files.items():
        p = os.path.join(root2, "Lab", rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        io.open(p, "w", encoding="utf-8", newline="").write(src)
    info2 = build_code_work("lab", stage_dir=stage2, code_root=root2, lab_bin=real_lab)
    io.open(os.path.join(stage2, info2["name"] + ".drafts.json"), "w", encoding="utf-8",
            newline="\n").write(json.dumps({"results": results}, ensure_ascii=False))
    one = code_apply(info2["name"], rel="bin/a.py", stage_dir=stage2, code_root=root2, lab_bin=real_lab,
                     dry_run=True)
    assert one["n_files"] == 1 and one["files"][0]["file"] == "bin/a.py", one
    print("code_cleanup self-test OK: swept 2 files (4 cands), applied both (1 keep, 3 cut), narrow "
          "single-file apply works, re-scan clean.")


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        _self_test()
    else:
        print(__doc__)
