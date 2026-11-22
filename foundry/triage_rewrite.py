"""triage_rewrite -- the library REWRITE lane's payload.

`python triage_rewrite.py <work.json> <out.json> [--model M] [--host URL] [--num-ctx N]
[--breaker N]`   (the compute runner appends the order name; ignored)

Drafts charter-conformant rewrites of library sections with the LOCAL ollama model:
charter + triage context + section text in, JSON schema `{rewrite, cuts, notes}` out.
Every cut names its destination (journal / archive / component-manual) because the charter
forbids silent drops: superseded content moves, never vanishes.

DRAFTS ONLY. Output goes to the share; the order's reviewer applies accepted drafts by
hand. Nothing in this payload touches the live library.

Charter: `triage_charter.md` beside this script -- byte-identical to
`Runbook\\LIBRARY-CHARTER.md` at deploy time; the capability version (`triage.charter.v1`)
is bumped when the charter changes, so every draft is attributable to the charter that
produced it.

Model + context resolve exactly like triage_classify (triage_local.json row for this box;
order args override). A model requirement rides the ORDER (`requires: ["model.<name>"]`),
and a misrouted order refuses rather than downgrades -- a draft served by the wrong tier is
a different experiment, not a noisy one. The SERVING model is recorded per row from the
response, never assumed from the request.

Residency + chat plumbing are imported from triage_classify -- one derivation, never a
second copy that drifts. Work file rows: {"id", "doc", "lines", "heading", "text_b64",
"triage": {...}} (b64 so section bytes never transit shells).
"""
import argparse
import base64
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

VERSION = "rewrite-v1 2026-08-22"

SCHEMA = {
    "type": "object",
    "properties": {
        "rewrite": {"type": "string",
                    "description": "the full replacement section text, charter-conformant"},
        "cuts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string",
                         "description": "content removed from the section, verbatim or "
                                        "tightly summarized"},
                "destination": {"type": "string",
                                "enum": ["journal", "archive", "component-manual"]},
            },
            "required": ["text", "destination"],
        }},
        "notes": {"type": "string",
                  "description": "anything the reviewer must know: ambiguities, claims the "
                                 "model could not ground in the source"},
    },
    "required": ["rewrite", "cuts", "notes"],
}

PROMPT = """You are rewriting ONE section of a homelab documentation library to conform to \
the library's charter. The charter is authoritative; apply it exactly.

%(charter)s

---
Triage context for this section (an earlier classification pass):
%(triage)s

---
Rewrite the section below. Rules of engagement:
* The rewrite states what IS -- no narrative, no discovery stories, no "previously said X",
  no dates on state, no first-person. Design facts over current-state observations.
* EVERY fact in your rewrite must come from the section itself OR from the "Verified current
  state" block at the end of this prompt (if one is present). Do not add facts, numbers,
  names, paths or claims from anywhere else. If the source is ambiguous and the verified
  block does not settle it, keep the ambiguity and flag it in `notes`.
* Content that does not belong in a manual goes in `cuts`, each with its destination:
  `journal` (narrative/why/discovery), `archive` (superseded statements), or
  `component-manual` (belongs in a different component's doc). Never silently drop content.
* Keep searchable section titles: preserve or sharpen headings so the obvious search term
  lands on them.
* Use standard Markdown only -- no wikilinks (`[[...]]`) and no YAML frontmatter. Write any
  cross-reference to another document as `see <FILE> §<heading>` so a later pass can
  convert it mechanically.
* If a "Verified current state" block appears at the END of this prompt, it is the LIVE
  ground truth and OVERRIDES the section wherever they conflict: state what it says, and never
  describe a system it says is retired as if it were live. Move each statement the verified
  block makes false into `cuts` with destination `archive` (superseded content moves, never
  vanishes) -- do not merely silence it.
* If the section needs NO change, return it unchanged in `rewrite` with empty `cuts` and say
  so in `notes`.

Section from `%(doc)s` (lines %(lines)s), heading: %(heading)s
<<<
%(text)s
>>>"""


def _vs(sec):
    """The section's verified_state block (current-truth anchor + live verdicts), appended to
    the prompt so the rewrite matches live state and overrides stale source text. Empty when the
    work file carries none; the pipeline's verify_gate guarantees a real rewrite carries one."""
    vs = (sec.get("verified_state") or "").strip()
    return ("\n\n" + vs) if vs else ""


def merge_shares(work_path, out):
    """Rebuild the full drafts file from per-share outputs. Fails on an incomplete set:
    every section in the work file must appear in exactly one share (a merge that quietly
    tolerated a missing share would apply a doc with holes in it)."""
    import glob as _glob
    with open(work_path, encoding="utf-8") as f:
        work = json.load(f)
    want = {s.get("id") for s in work.get("sections") or []}
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
        raise SystemExit("REFUSED (rc 2): merge is INCOMPLETE - %d sections in no share "
                         "(%d shares found: %s)" % (len(missing), len(shares),
                                                    [os.path.basename(s) for s in shares]))
    outd = {"version": VERSION, "charter": "per-share", "work": work_path,
            "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sections": len(want), "shares": [os.path.basename(s) for s in shares],
            "drafted": sum(1 for r in results if r.get("rewrite") is not None),
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
    ap.add_argument("--model", default=None)
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--num-ctx", type=int, default=None)
    ap.add_argument("--charter",
                    default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "triage_charter.md"))
    ap.add_argument("--breaker", type=int, default=3)
    ap.add_argument("--items-file", default=None,
                    help="parallel share: rewrite ONLY sections whose id is in this JSON "
                         "list of {id,tier,text_b64} items; output goes to a per-share "
                         "file the --merge mode combines")
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
            # a num_ctx is calibrated to a model; borrowing the row's window for a
            # different model would run one model in another's context size, silently.
            # An order that pins a model the row does not match must also state its
            # window -- refuse, never guess.
            if a.model != row_model:
                raise SystemExit("REFUSED (rc 2): order pins model %r but this box's "
                                 "triage row is calibrated for %r - cannot borrow its "
                                 "num_ctx; state --num-ctx in the order or route to a box "
                                 "whose row matches" % (a.model, row_model))
            a.num_ctx = int(row.get("num_ctx") or 0)
        if not a.model or not a.num_ctx:
            raise SystemExit("REFUSED (rc 2): triage_local.json has no row for %r" % box)
        print("factory customization [%s]: model=%s num_ctx=%d" % (box, a.model, a.num_ctx))

    with open(a.charter, encoding="utf-8") as f:
        charter = f.read()

    work = json.load(io.open(a.work, encoding="utf-8"))
    sections = work.get("sections") or []
    if not sections:
        raise SystemExit("REFUSED (rc 2): work file has zero sections - a zero-match input "
                         "is a broken join, not a clean sweep")
    if a.items_file:
        with open(a.items_file, encoding="utf-8") as f:
            share_ids = {it.get("id") for it in json.load(f) if isinstance(it, dict)}
        before = len(sections)
        sections = [s for s in sections if s.get("id") in share_ids]
        # a filter that silently matches NOTHING is indistinguishable from one that matched
        # correctly -- and every share the desk cut must land somewhere
        if not sections:
            raise SystemExit("REFUSED (rc 2): items-file matched 0 of %d sections - the "
                             "share and the work file disagree; refusing to ship an empty "
                             "share" % before)
        share_name = os.path.splitext(os.path.basename(a.items_file))[0].replace(".items", "")
        a.out = "%s.share-%s.json" % (a.out[:-5] if a.out.endswith(".json") else a.out,
                                      share_name)
        print("share %s: %d of %d sections -> %s"
              % (share_name, len(sections), before, a.out))
    print("[rewrite] %s | %d sections | model=%s ctx=%d"
          % (VERSION, len(sections), a.model, a.num_ctx), flush=True)

    _assert_resident(a.host, a.model, a.num_ctx)

    # one marker file per attempted section: the order declares progress_glob over these
    # (with progress_total = section count) so the factory tile shows Progress/Rate/ETA
    # for a payload the runner cannot introspect. Byproduct -- removed after the ship, and
    # cleared at start so a crashed run's leftovers cannot inflate the next run's count.
    prog_dir = a.out + ".progress"
    shutil.rmtree(prog_dir, ignore_errors=True)
    os.makedirs(prog_dir, exist_ok=True)
    # `_total` gives the runner's convention-based progress a denominator (RUNBOOK
    # "Compute payload authoring rules"); underscore-named so *.done never matches it
    io.open(os.path.join(prog_dir, "_total"), "w", encoding="utf-8").write(
        str(len(sections)))

    results, streak = [], 0
    for i, sec in enumerate(sections, 1):
        rid = sec.get("id") or ("sec-%d" % i)
        try:
            text = base64.b64decode(sec.get("text_b64") or "").decode("utf-8", "replace")
            prompt = PROMPT % {
                "charter": charter,
                "triage": json.dumps(sec.get("triage") or {}, ensure_ascii=False),
                "doc": sec.get("doc"), "lines": sec.get("lines"),
                "heading": sec.get("heading"), "text": text,
            } + _vs(sec)
            # context guard: prompt AND the rewrite (roughly section-sized) must both fit
            est = int(len(prompt) / 3.5) + int(len(text) / 3.5) + 400
            if est > 0.9 * a.num_ctx:
                results.append({"id": rid, "error": "section too large: est %d tokens "
                                                    "vs 0.9*%d ctx" % (est, a.num_ctx)})
                print("[rewrite] %s SKIP too-large (est %d)" % (rid, est), flush=True)
                continue
            t0 = time.time()
            resp = _chat(a.host, a.model, prompt, a.num_ctx, timeout=900, schema=SCHEMA)
            msg = resp.get("message") or {}
            raw = msg.get("content") or msg.get("thinking") or ""
            draft = json.loads(raw)
            results.append({"id": rid, "doc": sec.get("doc"), "lines": sec.get("lines"),
                            "heading": sec.get("heading"),
                            # the SERVING model, read back from the response -- routing
                            # is a request; only the response says who answered
                            "model": resp.get("model"),
                            "eval_count": resp.get("eval_count"),
                            "seconds": round(time.time() - t0, 1),
                            "rewrite": draft.get("rewrite"),
                            "cuts": draft.get("cuts"),
                            "notes": draft.get("notes")})
            streak = 0
            print("[rewrite] %d/%d %s ok in %.0fs (%d cuts)"
                  % (i, len(sections), rid, time.time() - t0,
                     len(draft.get("cuts") or [])), flush=True)
        except Exception as e:                                       # noqa: BLE001
            results.append({"id": rid, "error": repr(e)[:300]})
            streak += 1
            print("[rewrite] %d/%d %s ERROR %r (streak %d)"
                  % (i, len(sections), rid, e, streak), flush=True)
            if streak >= a.breaker:
                print("[rewrite] breaker: %d consecutive failures - the MACHINERY is "
                      "misbehaving, not the sections; stopping so a human looks"
                      % streak, flush=True)
                break
        finally:
            open(os.path.join(prog_dir, "%03d.done" % i), "w").close()

    out = {"version": VERSION, "charter": os.path.basename(a.charter),
           "work": a.work, "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "sections": len(sections),
           "drafted": sum(1 for r in results if r.get("rewrite") is not None),
           "errors": sum(1 for r in results if r.get("error")),
           "results": results}
    tmp = a.out + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    os.replace(tmp, a.out)
    shutil.rmtree(prog_dir, ignore_errors=True)
    print("[rewrite] shipped %s: %d drafted, %d errors"
          % (a.out, out["drafted"], out["errors"]), flush=True)
    return 0 if out["drafted"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
