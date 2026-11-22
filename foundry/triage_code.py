"""triage_code -- the #30 CODE-cleanup lane's payload (the sibling of triage_rewrite, for code).

`python triage_code.py <work.json> <out.json> [--model M] [--host URL] [--num-ctx N]
[--breaker N] [--items-file F] [--merge]`   (the compute runner appends the order name; ignored)

Drafts a two-bar disposition for each narrative comment/docstring candidate with the LOCAL ollama
model: the candidate text + a window of the code it annotates go in, a JSON schema
`{disposition, new_text, note}` comes out. STRUCTURE only -- the payload never touches the code
files; the reviewer/gated apply tool splices new_text by the detector's byte offsets.

DRAFTS ONLY. Output goes to the share; the order's reviewer applies accepted drafts through the
gated apply tool. Nothing here writes code.

The two-bar test (identical to 30e / the docs charter's rule 1): a comment survives only if it is
(1) a true constraint the adjacent code cannot show for itself AND (2) dense enough that a
normal-practices skilled developer would bother writing it here. Fail either -> CUT. A fact also
being filed in a manual is NOT grounds to cut (files-twice keeps load-bearing constraints in both
places); MOVE is only for a pure lab-wide fact with no bearing on how to change this code safely.

Model + context resolve exactly like triage_rewrite (triage_local.json row for this box; order args
override). A model requirement rides the ORDER (`requires: ["model.<name>"]`); a misrouted order
refuses rather than downgrades. The SERVING model is recorded per row from the response, never
assumed from the request. Residency + chat plumbing import from triage_classify -- one derivation,
never a second copy that drifts. Work rows: {"id", "kind", "file", "lines", "text_b64",
"context_b64"} (b64 so code bytes never transit shells).
"""
import argparse
import base64
import io
import json
import os
import shutil
import sys
import time

# An embedded-python interpreter (._pth beside the exe) builds sys.path without the script dir, so
# sibling imports need this insert; PYTHONPATH is ignored in that mode.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from triage_classify import _assert_resident            # noqa: E402
import infer_ollama  # noqa: E402  the factory's shared call: the row's thinking and temperature, ended on silence

VERSION = "code-v4"  # v2: MOVE narrowed (strip first, relocate only if nothing binds); v3: the weighed row's settings; v4: every draft kept on disk as it lands, a stopped run resumes

SCHEMA = {
    "type": "object",
    "properties": {
        "disposition": {"type": "string", "enum": ["KEEP-CLEAN", "CUT", "MOVE"]},
        "new_text": {"type": "string",
                     "description": "for KEEP-CLEAN: the full replacement comment/docstring INCLUDING "
                                    "the leading # or the triple quotes, terse, no narrative. Empty "
                                    "string for CUT and MOVE."},
        "note": {"type": "string",
                 "description": "for MOVE: the lab-wide fact + which manual it belongs in. Otherwise "
                                "any ambiguity the reviewer must know. May be empty."},
    },
    "required": ["disposition", "new_text", "note"],
}

PROMPT = """You are cleaning narrative commentary out of ONE code comment (or docstring) so the file \
reads the way a normal-practices, skilled human developer writes it -- NOT comment-city, no \
war-stories. Decide a disposition for the candidate and, for KEEP-CLEAN, a constraint-only rewrite.

THE TWO-BAR TEST -- a comment survives only if it passes BOTH bars (an AND, not a choice):
  BAR 1 (true constraint): it states a constraint the adjacent code cannot show for itself
    (e.g. "strength stays 0.5 -- higher denoise melts the subject").
  BAR 2 (density): a normal skilled dev would actually bother writing it HERE. A true-but-obvious or
    marginal rationale -- a magic number a good constant name already explains -- FAILS bar 2 and is
    cut even though it is accurate.
Fail EITHER bar -> CUT. But do NOT cut a genuine load-bearing constraint just to look lean:
safety/concurrency invariants, real compatibility gotchas, and don't-bump tuning limits STAY (terse).

STRIP always: dates, "the owner said" / pasted quotes, incident retellings, doctrine-row citations,
before/after war stories, warning-symbol block theatrics.

Default to KEEP-CLEAN. A fact ALSO being filed in a manual is NOT by itself grounds to CUT
(files-twice deliberately keeps a load-bearing constraint in BOTH the manual and a terse form at the
code site). Mark CUT only when it fails bar 1 or bar 2 outright.

MOVE IS NARROW, AND LENGTH IS NOT WHAT DECIDES IT. A long, story-heavy, date-stamped comment is
still KEEP-CLEAN whenever a constraint survives the strip -- being narrative is what you REWRITE, not
what you relocate. Work in this order: (1) strip the story, the dates and the quotes; (2) ask what is
LEFT that binds THIS code. Only if NOTHING is left may you mark MOVE, and then name the manual in
`note`. Concretely, if you can write one terse sentence that would stop a future developer from
re-introducing a bug, undoing a deliberate removal, or breaking an invariant AT THIS SITE, the answer
is KEEP-CLEAN with that sentence as new_text -- never MOVE. A comment saying "X was removed on
purpose and must not come back", or naming an external data format, a fail-closed behaviour, a
fixture that must not touch live state, or which of two similar things to assert, always leaves a
surviving constraint.

new_text (KEEP-CLEAN) is the FULL replacement text INCLUDING the leading # (comments) or the triple
quotes (docstrings), terse, human-normal prose, preserving multi-line structure. "" for CUT/MOVE.

Candidate (%(kind)s) from `%(file)s` lines %(lines)s:
<<<CANDIDATE
%(text)s
>>>CANDIDATE

The code it sits in (context only -- do not rewrite the code):
<<<CONTEXT
%(context)s
>>>CONTEXT"""


def merge_shares(work_path, out):
    """Rebuild the full drafts file from per-share outputs. Fails on an incomplete set: every item in
    the work file must appear in exactly one share (a merge that tolerated a missing share would apply
    a file with un-dispositioned comments)."""
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
        raise SystemExit("REFUSED (rc 2): merge is INCOMPLETE - %d items in no share (%d shares: %s)"
                         % (len(missing), len(shares), [os.path.basename(s) for s in shares]))
    outd = {"version": VERSION, "work": work_path,
            "finished": time.strftime("%Y-%m-%dT%H:%M:%S"), "items": len(want),
            "shares": [os.path.basename(s) for s in shares],
            "drafted": sum(1 for r in results if r.get("disposition")),
            "errors": sum(1 for r in results if r.get("error")), "results": results}
    tmp = out + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(outd, f, indent=1, ensure_ascii=False)
    os.replace(tmp, out)
    print("MERGED %d shares -> %s: %d drafted, %d errors"
          % (len(shares), out, outd["drafted"], outd["errors"]))
    return 0


def _resolve_model(a):
    if a.model is not None and a.num_ctx is not None:
        return
    import socket
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "triage_local.json")
    box = socket.gethostname().lower()
    try:
        with open(cfg_path, encoding="utf-8") as f:
            row = json.load(f).get(box) or {}
    except Exception as e:                                            # noqa: BLE001
        raise SystemExit("REFUSED (rc 2): no --model given and %s unreadable (%s)" % (cfg_path, e))
    row_model = row.get("model")
    if a.model is None:
        a.model = row_model
    if a.num_ctx is None:
        # a num_ctx is calibrated to a model; borrowing the row's window for a different model would
        # run one model in another's context size silently. Refuse, never guess.
        if a.model != row_model:
            raise SystemExit("REFUSED (rc 2): order pins model %r but this box's triage row is "
                             "calibrated for %r - state --num-ctx or route to a matching box"
                             % (a.model, row_model))
        a.num_ctx = int(row.get("num_ctx") or 0)
    if not a.model or not a.num_ctx:
        raise SystemExit("REFUSED (rc 2): triage_local.json has no row for %r" % box)
    print("factory customization [%s]: model=%s num_ctx=%d" % (box, a.model, a.num_ctx))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("out")
    ap.add_argument("--model", default=None)
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--num-ctx", type=int, default=None)
    ap.add_argument("--breaker", type=int, default=3)
    # the weighed row's own settings (the desk's {POLICY_THINK_ARGS} / {POLICY_TEMPERATURE_ARGS}); none = off / default
    ap.add_argument("--think", action="store_true")
    ap.add_argument("--think-level", choices=infer_ollama.THINK_LEVELS, default=None)
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--items-file", default=None,
                    help="parallel share: draft ONLY items whose id is in this JSON list; output goes "
                         "to a per-share file the --merge mode combines")
    ap.add_argument("--merge", action="store_true", help="combine <out>.share-*.json into full drafts")
    a, _extra = ap.parse_known_args(argv)     # the runner appends the order name; ignore it

    if a.merge:
        return merge_shares(a.work, a.out)
    _resolve_model(a)

    work = json.load(io.open(a.work, encoding="utf-8"))
    items = work.get("items") or []
    if not items:
        raise SystemExit("REFUSED (rc 2): work file has zero items - a zero-match input is a broken "
                         "detector join, not a clean file")
    if a.items_file:
        with open(a.items_file, encoding="utf-8") as f:
            share_ids = {it if not isinstance(it, dict) else it.get("id") for it in json.load(f)}
        before = len(items)
        items = [it for it in items if it.get("id") in share_ids]
        if not items:
            raise SystemExit("REFUSED (rc 2): items-file matched 0 of %d items - share and work file "
                             "disagree" % before)
        share_name = os.path.splitext(os.path.basename(a.items_file))[0].replace(".items", "")
        a.out = "%s.share-%s.json" % (a.out[:-5] if a.out.endswith(".json") else a.out, share_name)
        print("share %s: %d of %d items -> %s" % (share_name, len(items), before, a.out))
    print("[code] %s | %d items | model=%s ctx=%d" % (VERSION, len(items), a.model, a.num_ctx),
          flush=True)

    _assert_resident(a.host, a.model, a.num_ctx)

    # one marker file per attempted item -> the order's progress_glob gives the tile Progress/Rate/ETA.
    prog_dir = a.out + ".progress"
    shutil.rmtree(prog_dir, ignore_errors=True)
    os.makedirs(prog_dir, exist_ok=True)
    io.open(os.path.join(prog_dir, "_total"), "w", encoding="utf-8").write(str(len(items)))

    # Every draft lands on disk the moment it is made (one JSON line per item beside the output), so a run that is
    # stopped keeps its work: the next run of the same order reads the lines back and drafts only what is missing.
    # The sidecar is removed once the whole drafts file is written.
    partial = a.out + ".partial.jsonl"
    kept = {}
    if os.path.exists(partial):
        with io.open(partial, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    kept[row["id"]] = row
                except (ValueError, KeyError, TypeError):
                    pass
        print("[code] resuming: %d of %d drafts already on disk" % (len(kept), len(items)), flush=True)

    def keep(row):
        with io.open(partial, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()

    results, streak, stopped = [], 0, False
    for i, it in enumerate(items, 1):
        rid = it.get("id") or ("cand-%d" % i)
        if rid in kept:
            results.append(kept[rid])
            open(os.path.join(prog_dir, "%03d.done" % i), "w").close()
            continue
        try:
            text = base64.b64decode(it.get("text_b64") or "").decode("utf-8", "replace")
            context = base64.b64decode(it.get("context_b64") or "").decode("utf-8", "replace")
            prompt = PROMPT % {"kind": it.get("kind"), "file": it.get("file"),
                               "lines": it.get("lines"), "text": text, "context": context}
            est = int((len(prompt) + len(text)) / 3.5) + 400
            if est > 0.9 * a.num_ctx:
                results.append({"id": rid, "error": "candidate too large: est %d vs 0.9*%d ctx"
                                % (est, a.num_ctx)})
                keep(results[-1])
                print("[code] %s SKIP too-large (est %d)" % (rid, est), flush=True)
                continue
            t0 = time.time()
            options = {"num_ctx": int(a.num_ctx), "num_predict": int(a.num_ctx)}
            if a.temperature is not None:
                options["temperature"] = a.temperature
            resp = infer_ollama._chat(a.host, a.model, [{"role": "user", "content": prompt}], options, fmt=SCHEMA,
                                      think=a.think_level or a.think)
            msg = resp.get("message") or {}
            draft = json.loads(msg.get("content") or msg.get("thinking") or "")
            results.append({"id": rid, "file": it.get("file"), "lines": it.get("lines"),
                            "kind": it.get("kind"),
                            "model": resp.get("model"), "eval_count": resp.get("eval_count"),
                            "seconds": round(time.time() - t0, 1),
                            "disposition": draft.get("disposition"),
                            "new_text": draft.get("new_text"), "note": draft.get("note")})
            keep(results[-1])
            streak = 0
            print("[code] %d/%d %s %s in %.0fs" % (i, len(items), rid,
                  draft.get("disposition"), time.time() - t0), flush=True)
        except Exception as e:                                       # noqa: BLE001
            results.append({"id": rid, "error": repr(e)[:300]})
            keep(results[-1])
            streak += 1
            print("[code] %d/%d %s ERROR %r (streak %d)" % (i, len(items), rid, e, streak), flush=True)
            if streak >= a.breaker:
                print("[code] breaker: %d consecutive failures - the MACHINERY is misbehaving, not the "
                      "candidates; stopping so a human looks" % streak, flush=True)
                stopped = True
                break
        finally:
            open(os.path.join(prog_dir, "%03d.done" % i), "w").close()

    if stopped:
        # the run did not reach every item: the sidecar keeps what was made, nothing is shipped as whole, and a rerun
        # of the same order resumes from it (rc 3 so the desk marks the order failed, never done)
        print("[code] stopped early: %d of %d drafts kept in %s; rerun the order to resume" % (len(results), len(items), partial),
              flush=True)
        raise SystemExit(3)
    out = {"version": VERSION, "work": a.work, "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "items": len(items), "drafted": sum(1 for r in results if r.get("disposition")),
           "errors": sum(1 for r in results if r.get("error")), "results": results}
    tmp = a.out + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    os.replace(tmp, a.out)
    shutil.rmtree(prog_dir, ignore_errors=True)
    if os.path.exists(partial):
        os.remove(partial)                      # the whole drafts file now holds every row the sidecar kept
    print("[code] shipped %s: %d drafted, %d errors" % (a.out, out["drafted"], out["errors"]),
          flush=True)
    return 0 if out["drafted"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
