"""triage_synthesize -- the library RESTRUCTURE lane's payload (Obsidian wiki, #30).

`python triage_synthesize.py <work.json> <out.json> --task split-map|index|restructure-map
[--model M] [--host URL] [--num-ctx N] [--breaker N]`   (the compute runner appends the order name)

Proposes STRUCTURE, never content. Three tasks, one per order:
* `split-map`: given ONE oversized page's section list, propose how to group its sections into
  single-topic child pages (title + which section headings + rationale) and a parent-stub intro.
* `index`: given ONE domain's docs, draft a front-door INDEX page (intro, common questions ->
  the page that answers each, a page map).
* `restructure-map`: given ONE mixed "omnibus" page, classify each section's DISPOSITION for a
  thin-master + child-pages restructure (child / retain-in-master / drop-to-wiki / decompose). The
  model classifies STRUCTURE only; the master's connective prose is authored later by a human
  (the "lean propose" contract), so this task never writes master text.

DRAFTS ONLY. Output goes to the share; a deterministic apply (`library_split.py`) does the
byte-exact cut a reviewer has approved -- this payload NEVER cuts or rewrites content. An LLM in
the cutting path would paraphrase RAG-indexed bytes; here it only decides grouping and titles.

Model + context, residency, chat plumbing, share/merge and progress-by-convention are identical
to triage_rewrite -- `_assert_resident`/`_chat` are imported from triage_classify (one
derivation, never a second copy that drifts). Work file: {"task", "items": [{"id", ...}]} where
an item is a DOC (split-map) or a DOMAIN (index); one model call per item. The SERVING model is
recorded per row from the response, never assumed from the request.
"""
import argparse
import io
import json
import os
import shutil
import sys
import time

# An embedded-python interpreter (._pth beside the exe) builds sys.path without the script
# dir, so sibling imports need this insert; PYTHONPATH is ignored in that mode.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from triage_classify import _assert_resident, _chat            # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VERSION = "synthesize-v1 2026-08-22"

SPLIT_SCHEMA = {
    "type": "object",
    "properties": {
        "child_pages": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string",
                          "description": "one sharp H1 a person would search for; "
                                         "domain-and-topic form, e.g. "
                                         "'Fellowship USB networking - RNDIS setup'"},
                "section_headings": {"type": "array", "items": {"type": "string"},
                                     "description": "the EXACT existing headings assigned "
                                                    "to this child page"},
                "rationale": {"type": "string"},
            },
            "required": ["title", "section_headings", "rationale"],
        }},
        "parent_stub_intro": {"type": "string",
                              "description": "2-4 sentences for the parent page, which "
                                             "becomes a stub linking to the children"},
        "notes": {"type": "string"},
    },
    "required": ["child_pages", "parent_stub_intro", "notes"],
}

INDEX_SCHEMA = {
    "type": "object",
    "properties": {
        "index_markdown": {"type": "string",
                           "description": "the full front-door page: one H1, intro, "
                                          "Common questions with `see FILE §heading` links, "
                                          "and a Pages-in-this-domain map"},
        "notes": {"type": "string"},
    },
    "required": ["index_markdown", "notes"],
}

SPLIT_PROMPT = """You are proposing how to SPLIT one oversized homelab documentation page into \
several single-topic wiki pages, for a knowledge-base restructure. You do NOT rewrite content -- \
you decide only the GROUPING and the page TITLES.

Rules of engagement:
* Each proposed child page is ONE coherent topic. Assign EVERY existing section heading below to \
exactly one child page -- never drop a heading, never invent one. Use the headings verbatim.
* Each child page needs ONE sharp H1 title a person would actually search for -- the title is the \
retrieval key. Prefer the domain-and-topic form (e.g. "Fellowship USB networking - RNDIS setup").
* Propose a short `parent_stub_intro` (2-4 sentences): what this page's domain is, and that the \
detail now lives on the linked child pages.
* Aim for 2-6 child pages. If the page is genuinely single-topic and should NOT split, return one \
child page holding all sections and say so in `notes`.
* If a "Verified current state" block appears at the END of this prompt, it is the LIVE ground \
truth and OVERRIDES anything in the sections above -- never propose a title, page or intro that \
assumes a system it says is gone.

Page: `%(doc)s`, current title: %(title)s
Sections (heading -- approx chars -- first line):
%(sections)s"""

INDEX_PROMPT = """You are writing a front-door INDEX page for one domain of a homelab knowledge \
base -- the "start here" page a person or the local RAG model lands on first.

Rules of engagement:
* One H1: the domain title. Standard Markdown only -- no wikilinks (`[[...]]`), no YAML frontmatter.
* Open with a 2-3 sentence intro: what this domain is and does.
* A "## Common questions" section: 4-8 real questions someone asks about this domain, each with \
the page that answers it, written as `see <FILE> §<heading>` (a later pass converts to wikilinks).
* A "## Pages in this domain" map: each doc with a one-line description of what it covers.
* Every page you reference MUST be one of the domain docs listed below -- do not invent pages or \
facts. If you are unsure what a doc covers, say so in `notes` rather than guessing.
* If a "Verified current state" block appears at the END of this prompt, it is the LIVE ground \
truth: the intro, the questions, and the page map MUST match it -- never describe or ask about a \
system it says is retired.

Domain: %(domain)s
Docs in this domain (title -- path -- first line):
%(docs)s"""


RESTRUCTURE_SCHEMA = {
    "type": "object",
    "properties": {
        "dispositions": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "heading": {"type": "string",
                            "description": "the EXACT existing section heading, verbatim"},
                "disposition": {"type": "string",
                                "enum": ["child", "retain", "drop", "decompose"]},
                "child_title": {"type": "string",
                                "description": "if disposition=child: one sharp H1 a person would "
                                               "search for. Sections that belong on the SAME child "
                                               "page MUST share this title verbatim."},
                "drop_target": {"type": "string",
                                "description": "if disposition=drop: the existing wiki page whose "
                                               "path is listed below that its depth belongs to, or "
                                               "\"\" if none fits"},
                "rationale": {"type": "string"},
            },
            "required": ["heading", "disposition", "rationale"],
        }},
        "master_outline": {"type": "array", "items": {"type": "string"},
                           "description": "ADVISORY short bullets suggesting the thin master's "
                                          "quick-steps and pointers -- notes for the human author, "
                                          "NOT finished prose"},
        "notes": {"type": "string"},
    },
    "required": ["dispositions", "notes"],
}

RESTRUCTURE_PROMPT = """You are proposing how to RESTRUCTURE one large "omnibus" homelab \
documentation page into a THIN operator master plus single-topic wiki child pages. You do NOT \
rewrite or author content -- you decide only the DISPOSITION of each existing section.

This lab uses a HYBRID model: the master keeps quick, every-session operator content; deep \
per-domain material moves out to its domain wiki; operator-unique procedures with no wiki home \
become their own child pages. Give EVERY section below exactly one disposition:
* child -- an operator procedure that should be its own single-topic page. Give a sharp \
`child_title` (the H1 someone would search for); sections that share a child page share that title.
* retain -- quick, every-session reference that stays IN the master verbatim (machine lists, IPs, \
DNS, mounts, common commands). Use SPARINGLY: only genuinely high-frequency operator glances.
* drop -- the section's depth belongs in an existing DOMAIN WIKI; the master keeps only a pointer. \
Name that page in `drop_target` (choose from the wiki list below; use "" if none fits).
* decompose -- the section is a grab-bag of several unrelated topics under one heading and must be \
split by its sub-topics. Flag it; a human routes its sub-blocks.

Rules of engagement:
* Use section headings VERBATIM. Assign EVERY section exactly once. Never invent or drop a heading.
* Prefer `child` or `drop` for anything domain-specific; `retain` only for quick operator glances.
* Do NOT write the master's prose -- that is authored later. `master_outline` may hold short \
advisory bullets, never finished text.
* If a "Verified current state" block appears at the END of this prompt, it is the LIVE ground \
truth and overrides the sections above when they conflict.

Page: `%(doc)s`, current title: %(title)s
Sections (heading -- approx chars -- first line):
%(sections)s

Existing domain wiki pages you may DROP toward (title -- path):
%(wikis)s"""


def _fmt_sections(secs):
    out = []
    for s in secs:
        lead = (s.get("lead") or "").replace("\n", " ").strip()
        out.append("- %s -- ~%s chars -- %s"
                   % (s.get("heading"), s.get("chars"), lead[:160]))
    return "\n".join(out)


def _fmt_docs(docs):
    out = []
    for d in docs:
        lead = (d.get("lead") or "").replace("\n", " ").strip()
        out.append("- %s -- `%s` -- %s"
                   % (d.get("title"), d.get("path"), lead[:200]))
    return "\n".join(out)


def _fmt_wikis(wikis):
    out = []
    for w in wikis:
        out.append("- %s -- `%s`" % (w.get("title"), w.get("path")))
    return "\n".join(out) if out else "(none listed)"


def _vs(it):
    """The item's verified_state block (current-truth anchor + live verdicts), appended to the
    prompt so the model drafts to match live state. Empty when the work file carries none; the
    pipeline's verify_gate is what guarantees a real restructure carries one."""
    vs = (it.get("verified_state") or "").strip()
    return ("\n\n" + vs) if vs else ""


def merge_shares(work_path, out):
    """Rebuild the full drafts file from per-share outputs. Fails on an incomplete set:
    every item in the work file must appear in exactly one share."""
    import glob as _glob
    with open(work_path, encoding="utf-8") as f:
        work = json.load(f)
    want = {it.get("id") for it in work.get("items") or []}
    base = out[:-5] if out.endswith(".json") else out
    shares = sorted(_glob.glob(base + ".share-*.json"))
    if not shares:
        raise SystemExit("REFUSED (rc 2): no share files match %s.share-*.json" % base)
    results, seen = [], set()
    for sp in shares:
        with open(sp, encoding="utf-8") as f:
            sd = json.load(f)
        for row in sd.get("results", []):
            results.append(row)
            seen.add(row.get("id"))
    missing = want - seen
    if missing:
        raise SystemExit("REFUSED (rc 2): merge is INCOMPLETE - %d items in no share "
                         "(%d shares found: %s)" % (len(missing), len(shares),
                                                    [os.path.basename(s) for s in shares]))
    outd = {"version": VERSION, "task": work.get("task"), "work": work_path,
            "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "items": len(want), "shares": [os.path.basename(s) for s in shares],
            "drafted": sum(1 for r in results if not r.get("error")),
            "errors": sum(1 for r in results if r.get("error")),
            "results": results}
    tmp = out + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(outd, f, indent=1, ensure_ascii=False)
    os.replace(tmp, out)
    print("MERGED %d shares -> %s: %d drafted, %d errors"
          % (len(shares), out, outd["drafted"], outd["errors"]))
    return 0


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("out")
    ap.add_argument("--task", choices=["split-map", "index", "restructure-map"], default=None,
                    help="split-map: propose child pages for a doc; index: draft a domain "
                         "front-door page; restructure-map: classify each section's disposition "
                         "for a thin-master restructure. Defaults to the work file's `task` field.")
    ap.add_argument("--model", default=None)
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--num-ctx", type=int, default=None)
    ap.add_argument("--breaker", type=int, default=3)
    ap.add_argument("--items-file", default=None,
                    help="parallel share: process ONLY items whose id is in this JSON list "
                         "of {id,...} entries; output goes to a per-share file --merge combines")
    ap.add_argument("--merge", action="store_true",
                    help="combine <out>.share-*.json into the full drafts file")
    a, _extra = ap.parse_known_args(argv)   # the runner appends the order name; ignore it

    if a.merge:
        return merge_shares(a.work, a.out)

    if a.model is None or a.num_ctx is None:
        import socket
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "triage_local.json")
        box = socket.gethostname().lower()
        try:
            with open(cfg_path, encoding="utf-8") as f:
                row = json.load(f).get(box) or {}
        except Exception as e:                                       # noqa: BLE001
            raise SystemExit("REFUSED (rc 2): no --model given and %s unreadable (%s)"
                             % (cfg_path, e))
        row_model = row.get("model")
        if a.model is None:
            a.model = row_model
        if a.num_ctx is None:
            if a.model != row_model:
                raise SystemExit("REFUSED (rc 2): order pins model %r but this box's triage "
                                 "row is calibrated for %r - cannot borrow its num_ctx; state "
                                 "--num-ctx in the order or route to a matching box"
                                 % (a.model, row_model))
            a.num_ctx = int(row.get("num_ctx") or 0)
        if not a.model or not a.num_ctx:
            raise SystemExit("REFUSED (rc 2): triage_local.json has no row for %r" % box)
        print("factory customization [%s]: model=%s num_ctx=%d" % (box, a.model, a.num_ctx))

    work = json.load(io.open(a.work, encoding="utf-8"))
    task = a.task or work.get("task")
    if task not in ("split-map", "index", "restructure-map"):
        raise SystemExit("REFUSED (rc 2): no task - pass --task or set work['task'] to "
                         "split-map|index|restructure-map")
    items = work.get("items") or []
    if not items:
        raise SystemExit("REFUSED (rc 2): work file has zero items - a zero-match input is a "
                         "broken join, not a clean sweep")
    if a.items_file:
        with open(a.items_file, encoding="utf-8") as f:
            share_ids = {it.get("id") for it in json.load(f) if isinstance(it, dict)}
        before = len(items)
        items = [it for it in items if it.get("id") in share_ids]
        if not items:
            raise SystemExit("REFUSED (rc 2): items-file matched 0 of %d items - the share and "
                             "the work file disagree; refusing to ship an empty share" % before)
        share_name = os.path.splitext(os.path.basename(a.items_file))[0].replace(".items", "")
        a.out = "%s.share-%s.json" % (a.out[:-5] if a.out.endswith(".json") else a.out,
                                      share_name)
        print("share %s: %d of %d items -> %s" % (share_name, len(items), before, a.out))
    print("[synth:%s] %s | %d items | model=%s ctx=%d"
          % (task, VERSION, len(items), a.model, a.num_ctx), flush=True)

    schema = {"split-map": SPLIT_SCHEMA, "index": INDEX_SCHEMA,
              "restructure-map": RESTRUCTURE_SCHEMA}[task]
    _assert_resident(a.host, a.model, a.num_ctx)

    prog_dir = a.out + ".progress"
    shutil.rmtree(prog_dir, ignore_errors=True)
    os.makedirs(prog_dir, exist_ok=True)
    io.open(os.path.join(prog_dir, "_total"), "w", encoding="utf-8").write(str(len(items)))

    results, streak = [], 0
    for i, it in enumerate(items, 1):
        rid = it.get("id") or ("item-%d" % i)
        try:
            if task == "split-map":
                prompt = (SPLIT_PROMPT % {"doc": it.get("doc"), "title": it.get("title"),
                                          "sections": _fmt_sections(it.get("sections") or [])}
                          + _vs(it))
            elif task == "restructure-map":
                prompt = (RESTRUCTURE_PROMPT % {"doc": it.get("doc"), "title": it.get("title"),
                                                "sections": _fmt_sections(it.get("sections") or []),
                                                "wikis": _fmt_wikis(it.get("wikis") or [])}
                          + _vs(it))
            else:
                prompt = (INDEX_PROMPT % {"domain": it.get("domain"),
                                          "docs": _fmt_docs(it.get("docs") or [])}
                          + _vs(it))
            est = int(len(prompt) / 3.5) + 1200      # room for the proposed structure back
            if est > 0.9 * a.num_ctx:
                results.append({"id": rid, "error": "item too large: est %d tokens vs 0.9*%d "
                                                    "ctx" % (est, a.num_ctx)})
                print("[synth] %s SKIP too-large (est %d)" % (rid, est), flush=True)
                continue
            t0 = time.time()
            resp = _chat(a.host, a.model, prompt, a.num_ctx, timeout=900, schema=schema)
            msg = resp.get("message") or {}
            raw = msg.get("content") or msg.get("thinking") or ""
            draft = json.loads(raw)
            row = {"id": rid, "model": resp.get("model"),
                   "eval_count": resp.get("eval_count"),
                   "seconds": round(time.time() - t0, 1), "notes": draft.get("notes")}
            if task == "split-map":
                row.update({"doc": it.get("doc"), "title": it.get("title"),
                            "child_pages": draft.get("child_pages"),
                            "parent_stub_intro": draft.get("parent_stub_intro")})
            elif task == "restructure-map":
                row.update({"doc": it.get("doc"), "title": it.get("title"),
                            "dispositions": draft.get("dispositions"),
                            "master_outline": draft.get("master_outline")})
            else:
                row.update({"domain": it.get("domain"),
                            "index_markdown": draft.get("index_markdown")})
            results.append(row)
            streak = 0
            if task == "split-map":
                n, unit = len(draft.get("child_pages") or []), "child pages"
            elif task == "restructure-map":
                n, unit = len(draft.get("dispositions") or []), "dispositions"
            else:
                n, unit = 1, "index"
            print("[synth] %d/%d %s ok in %.0fs (%d %s)"
                  % (i, len(items), rid, time.time() - t0, n, unit), flush=True)
        except Exception as e:                                       # noqa: BLE001
            results.append({"id": rid, "error": repr(e)[:300]})
            streak += 1
            print("[synth] %d/%d %s ERROR %r (streak %d)"
                  % (i, len(items), rid, e, streak), flush=True)
            if streak >= a.breaker:
                print("[synth] breaker: %d consecutive failures - the MACHINERY is "
                      "misbehaving, not the items; stopping so a human looks" % streak,
                      flush=True)
                break
        finally:
            open(os.path.join(prog_dir, "%03d.done" % i), "w").close()

    out = {"version": VERSION, "task": task, "work": a.work,
           "finished": time.strftime("%Y-%m-%dT%H:%M:%S"), "items": len(items),
           "drafted": sum(1 for r in results if not r.get("error")),
           "errors": sum(1 for r in results if r.get("error")), "results": results}
    tmp = a.out + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    os.replace(tmp, a.out)
    shutil.rmtree(prog_dir, ignore_errors=True)
    print("[synth] shipped %s: %d drafted, %d errors"
          % (a.out, out["drafted"], out["errors"]), flush=True)
    return 0 if out["drafted"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
