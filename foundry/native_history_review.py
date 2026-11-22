"""Grounded 27B review for Native-Intelligence reconnaissance Part 1.

This is a narrow Project 30 model payload: persisted work packets in, structured
evidence-grounded judgments out.  It imports Project 30's measured Ollama residency
and chat plumbing, checkpoints each successful row atomically, refuses blind or
ungrounded output, and gates final assembly on controls and exact completeness.
"""
import argparse
import base64
import hashlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from triage_classify import _assert_resident, _chat  # noqa: E402

VERSION = "native-history-review-v2.1 2026-08-26"
RUBRIC_VERSION = "native-positive-rubric-v4"          # the positive rubric (Part 1)
CORRECTIVE_RUBRIC_VERSION = "native-corrective-rubric-v1"
SIGNALS = ("explicit_praise", "explicit_acceptance", "successful_continuation",
           "mixed_or_corrective", "ambiguous", "not_positive")
PATTERNS = (
    "none",
    "uses_existing_architecture",
    "grounds_claims_in_evidence",
    "effective_delegation",
    "follows_runbook_or_plan",
    "completes_end_to_end",
    "bounded_useful_initiative",
    "clear_concise_handoff",
    "catches_or_prevents_failure",
    "transparent_uncertainty",
    "adapts_to_user_context",
    "effective_technical_explanation",
    "respects_scope_and_authority",
    "recovers_well",
    "other",
)

SCHEMA = {
    "type": "object",
    "properties": {
        "positive": {"type": "boolean"},
        "signal": {"type": "string", "enum": list(SIGNALS)},
        "primary_pattern": {"type": "string", "enum": list(PATTERNS)},
        "pattern_tags": {"type": "array", "items": {"type": "string", "enum": list(PATTERNS)}},
        "assistant_behavior": {"type": "string"},
        "owner_signal": {"type": "string"},
        "assistant_evidence": {"type": "string"},
        "owner_evidence": {"type": "string"},
        "rationale": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "novel_pattern": {"type": "string"},
    },
    "required": ["positive", "signal", "primary_pattern", "pattern_tags",
                 "assistant_behavior", "owner_signal", "assistant_evidence",
                 "owner_evidence", "rationale", "confidence", "novel_pattern"],
}

PROMPT = """You are the evidence-grounded reviewer for ONE historical the owner/assistant interaction.
This is reconnaissance for recurring POSITIVE / SUCCESSFUL collaboration patterns.  Review only
the packet below; do not use outside knowledge and do not assume an action succeeded merely because
the assistant claimed it did.

Decide positive=true only when the owner's CURRENT reaction genuinely rewards, accepts, approves, or
green-lights the preceding assistant response.  A positive-looking phrase does NOT count when it is
negated, quoted as an example/past statement, praise for the owner's own idea, or followed by a correction
that rejects the response.  A simple green-light can be successful_continuation when it clearly
accepts the proposed/resulting work.  Mixed praise whose operative content is correction/rejection is
not positive for this Part-1 pass.

When positive=true, identify the specific behavior the evidence most plausibly shows the owner accepting
or rewarding.  Do not label generic competence when the actual signal is narrower.  Pick one
primary_pattern and up to three pattern_tags from this controlled list; pattern_tags MUST include
the primary_pattern itself (it counts toward the three):
  uses_existing_architecture; grounds_claims_in_evidence; effective_delegation;
  follows_runbook_or_plan; completes_end_to_end; bounded_useful_initiative;
  clear_concise_handoff; catches_or_prevents_failure; transparent_uncertainty;
  adapts_to_user_context; effective_technical_explanation; respects_scope_and_authority;
  recovers_well; other.
Use primary_pattern=none and pattern_tags=[] when positive=false.  Use novel_pattern only for a
specific grounded success behavior the controlled list genuinely misses; otherwise return "".

assistant_evidence and owner_evidence MUST each be a short, exact, contiguous quote copied from the
respective packet field (never paraphrase or reword; prefer ONE short contiguous span -- a single
sentence is enough; if you must skip text, join exact spans with " ... "; do not add surrounding
quotation marks of your own).  assistant_behavior, owner_signal, and rationale
are concise semantic judgments grounded by those quotes.  Confidence reflects evidence sufficiency,
not model self-esteem.

PACKET:
%(packet)s"""


CPATTERNS = (
    "none",
    "rederived_or_replaced_plan",
    "ignored_architecture_or_docs",
    "blind_review",
    "exceeded_scope_or_authority",
    "unnecessary_redesign_or_generalization",
    "premature_implementation",
    "insufficient_initiative",
    "excessive_initiative",
    "unverified_claims",
    "excessive_repetition_or_verbosity",
    "insufficient_explanation",
    "missed_actual_objective",
    "lost_context_constraints",
    "correction_treated_as_license",
    "continued_bad_path",
    "other",
)
RECOVERIES = ("no_observable_recovery", "successful_recovery", "partial_recovery",
              "ignored_or_repeated", "reveals_persistent_preference", "unjudgeable")

SCHEMA_CORRECTIVE = {
    "type": "object",
    "properties": {
        "corrective": {"type": "boolean"},
        "primary_pattern": {"type": "string", "enum": list(CPATTERNS)},
        "pattern_tags": {"type": "array", "items": {"type": "string", "enum": list(CPATTERNS)}},
        "recovery": {"type": "string", "enum": list(RECOVERIES)},
        "assistant_behavior": {"type": "string"},
        "correction_summary": {"type": "string"},
        "assistant_evidence": {"type": "string"},
        "correction_evidence": {"type": "string"},
        "recovery_evidence": {"type": "string"},
        "followup_evidence": {"type": "string"},
        "rationale": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "novel_pattern": {"type": "string"},
    },
    "required": ["corrective", "primary_pattern", "pattern_tags", "recovery",
                 "assistant_behavior", "correction_summary", "assistant_evidence",
                 "correction_evidence", "recovery_evidence", "followup_evidence",
                 "rationale", "confidence", "novel_pattern"],
}

PROMPT_CORRECTIVE = """You are the evidence-grounded reviewer for ONE historical the owner/assistant \
interaction, with its follow-on turn when the transcript has one.  This is reconnaissance for what \
causes the owner to CORRECT, redirect, reject, constrain, or take work back from an assistant -- \
collaboration behavior, not sentiment.  Review only the packet below; do not use outside knowledge \
and do not assume an action succeeded merely because the assistant claimed it did.

Decide corrective=true only when the owner's CURRENT reaction genuinely corrects, redirects, rejects,
constrains, or takes work back from the preceding assistant response.  A clarifying question, a
neutral new instruction, banter, or frustration aimed at something OTHER than the assistant's work
is NOT corrective.  Corrective-looking phrases do not count when negated, quoted from the past, or
aimed elsewhere.

When corrective=true, identify what the evidence most plausibly shows the owner correcting.  Pick one
primary_pattern and up to three pattern_tags from this controlled list; pattern_tags MUST include
the primary_pattern itself (it counts toward the three):
  rederived_or_replaced_plan (re-derived or replaced an established plan instead of working in it);
  ignored_architecture_or_docs (ignored existing architecture, documentation, decisions, or tools);
  blind_review (asserted or reviewed without reading evidence that was available);
  exceeded_scope_or_authority; unnecessary_redesign_or_generalization; premature_implementation;
  insufficient_initiative (excessive passivity); excessive_initiative (overreach/interference);
  unverified_claims (failed to verify claims against available evidence);
  excessive_repetition_or_verbosity; insufficient_explanation (too terse when context was needed);
  missed_actual_objective (satisfied the literal request but missed the owner's actual objective);
  lost_context_constraints (lost an established constraint across context or compaction);
  correction_treated_as_license (treated a correction as permission to redesign unrelated work);
  continued_bad_path (continued down a corrected path); other.
Use primary_pattern=none and pattern_tags=[] when corrective=false.  Use novel_pattern only for a
specific grounded corrective pattern the controlled list genuinely misses; otherwise return "".

recovery classifies the TRAJECTORY using assistant_recovery (the assistant's next response) and
owner_followup (the owner's subsequent reaction).  Exactly one of: no_observable_recovery,
successful_recovery, partial_recovery, ignored_or_repeated, reveals_persistent_preference,
unjudgeable.  Rules, applied literally: when corrective=false, recovery MUST be "unjudgeable";
when corrective=true and assistant_recovery is empty, recovery MUST be "no_observable_recovery";
never manufacture a judgment -- when the follow-on turns are missing or unrelated to the
correction, use "unjudgeable".

Evidence fields MUST each be a short, exact, contiguous quote copied from the named packet field
(never paraphrase or reword; never stitch together text that is not contiguous in the source; prefer
ONE short contiguous span -- a single sentence is enough; if you must skip text, join exact spans
with " ... "; do not add surrounding quotation marks of your own).  The packet holds TWO assistant
fields and they are NOT interchangeable: assistant_evidence must be copied from assistant_response
(the behavior being corrected), NEVER from assistant_recovery.  When corrective=true,
correction_evidence (from owner_reaction) and assistant_evidence are both REQUIRED; when
corrective=false, return "" for both.  recovery_evidence comes from assistant_recovery -- required
exactly when recovery is
successful_recovery, partial_recovery, ignored_or_repeated, or reveals_persistent_preference, and
"" otherwise; followup_evidence from owner_followup when owner_followup is non-empty AND it informed
your recovery judgment, and "" otherwise.  assistant_behavior and rationale are always concise
semantic judgments grounded by those quotes; correction_summary summarizes what the owner corrected when
corrective=true and is "" when corrective=false.  Confidence reflects evidence sufficiency.

PACKET:
%(packet)s"""

ACTIVE_RECOVERIES = ("successful_recovery", "partial_recovery",
                     "ignored_or_repeated", "reveals_persistent_preference")


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, indent=1, ensure_ascii=False, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp), str(path))


def _canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _safe_id(value):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))[:120]


def _norm(text):
    return " ".join(str(text or "").split()).strip()


def _defmt(text):
    # formatting layer only, applied to BOTH sides so word-level content must still match exactly:
    # markdown emphasis/code marks are presentation, models emit literal \n/\t escape sequences
    # where the source has real whitespace, and curly quote characters (voice dictation) are
    # presentation variants of straight ones.
    text = text.replace("\\n", " ").replace("\\t", " ")
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"')):
        text = text.replace(a, b)
    return _norm(text.replace("*", "").replace("`", ""))


def _quote_is_grounded(quote, source, max_len=400):
    q = _norm(quote)
    # models present quotes AS quotes; one symmetric surrounding pair is tolerated.
    if len(q) >= 2 and q[0] in "\"'“‘" and q[-1] in "\"'”’":
        q = q[1:-1].strip()
    # the cap is an anti-dump heuristic, not part of the grounding contract: an exact contiguous span
    # is fully grounded at any length, so a rubric with larger packets may raise it without weakening
    # exactness. 400 stays the default so the positive rubric is unchanged.
    if not q or len(q) > max_len:
        return False
    qf, sf = _defmt(q), _defmt(source)
    # an ellipsis-joined quote is several EXACT spans, each independently grounded in the source;
    # any paraphrased segment still refuses.
    segments = [seg.strip() for seg in re.split(r"\.\.\.+|…", qf) if seg.strip()]
    if not segments or any(seg not in sf for seg in segments):
        return False
    # quoting the WHOLE (normalised) source is maximal grounding at any length -- reactions like
    # "hit it" are shorter than any anti-trivial floor; the floor applies only to partial quotes.
    return qf == sf or sum(len(seg) for seg in segments) >= 8


def _decode_packet(item, required=("previous_user_request", "assistant_response",
                                   "assistant_tool_names", "owner_reaction")):
    raw = base64.b64decode(item.get("packet_b64") or "", validate=True)
    packet = json.loads(raw.decode("utf-8", "strict"))
    if not isinstance(packet, dict) or any(k not in packet for k in required):
        raise ValueError("packet is missing required evidence fields")
    return packet


def _validate(draft, packet):
    if not isinstance(draft, dict):
        raise ValueError("review is not an object")
    missing = [k for k in SCHEMA["required"] if k not in draft]
    if missing:
        raise ValueError("missing fields: %s" % missing)
    if not isinstance(draft["positive"], bool):
        raise ValueError("positive is not boolean")
    if draft["signal"] not in SIGNALS or draft["primary_pattern"] not in PATTERNS:
        raise ValueError("unknown signal or primary pattern")
    tags = draft.get("pattern_tags")
    if not isinstance(tags, list) or len(tags) > 3 or any(t not in PATTERNS or t == "none" for t in tags):
        raise ValueError("pattern_tags are malformed")
    if draft["positive"]:
        if draft["primary_pattern"] == "none" or draft["primary_pattern"] not in tags:
            raise ValueError("positive review must put its non-none primary pattern in pattern_tags")
        if draft["signal"] in ("not_positive", "ambiguous", "mixed_or_corrective"):
            raise ValueError("positive review has a non-positive signal")
    else:
        if draft["primary_pattern"] != "none" or tags:
            raise ValueError("non-positive review must have primary_pattern=none and no tags")
    if draft.get("confidence") not in ("high", "medium", "low"):
        raise ValueError("invalid confidence")
    if not _quote_is_grounded(draft.get("assistant_evidence"), packet["assistant_response"]):
        raise ValueError("assistant_evidence is not an exact bounded quote from assistant_response")
    if not _quote_is_grounded(draft.get("owner_evidence"), packet["owner_reaction"]):
        raise ValueError("owner_evidence is not an exact bounded quote from owner_reaction")
    for field in ("assistant_behavior", "owner_signal", "rationale"):
        if not _norm(draft.get(field)) or len(str(draft[field])) > 1200:
            raise ValueError("%s is empty or unbounded" % field)
    if len(str(draft.get("novel_pattern") or "")) > 500:
        raise ValueError("novel_pattern is unbounded")


def _validate_corrective(draft, packet):
    if not isinstance(draft, dict):
        raise ValueError("review is not an object")
    missing = [k for k in SCHEMA_CORRECTIVE["required"] if k not in draft]
    if missing:
        raise ValueError("missing fields: %s" % missing)
    if not isinstance(draft["corrective"], bool):
        raise ValueError("corrective is not boolean")
    if draft["primary_pattern"] not in CPATTERNS:
        raise ValueError("unknown primary pattern")
    if draft["recovery"] not in RECOVERIES:
        raise ValueError("unknown recovery value")
    tags = draft.get("pattern_tags")
    if not isinstance(tags, list) or len(tags) > 3 or any(t not in CPATTERNS or t == "none" for t in tags):
        raise ValueError("pattern_tags are malformed")
    recovery = draft["recovery"]
    if draft["corrective"]:
        if draft["primary_pattern"] == "none" or draft["primary_pattern"] not in tags:
            raise ValueError("corrective review must put its non-none primary pattern in pattern_tags")
        if not _norm(packet.get("assistant_recovery")) and recovery != "no_observable_recovery":
            raise ValueError("no assistant_recovery in packet but recovery is not no_observable_recovery")
    else:
        if draft["primary_pattern"] != "none" or tags:
            raise ValueError("non-corrective review must have primary_pattern=none and no tags")
        if recovery != "unjudgeable":
            raise ValueError("non-corrective review must have recovery=unjudgeable")
    if draft.get("confidence") not in ("high", "medium", "low"):
        raise ValueError("invalid confidence")
    # A non-corrective row is the filter rejecting a candidate, not a finding: there is no correction
    # for the evidence fields to evidence, so empty is correct there. Anything supplied must still be
    # an exact quote -- the grounding contract never loosens, only its applicability.
    for field, src in (("assistant_evidence", "assistant_response"),
                       ("correction_evidence", "owner_reaction")):
        value = draft.get(field)
        if not draft["corrective"] and not _norm(value):
            continue
        if not _quote_is_grounded(value, packet[src], max_len=600):
            raise ValueError("%s is not an exact bounded quote from %s" % (field, src))
    if recovery in ACTIVE_RECOVERIES:
        if not _quote_is_grounded(draft.get("recovery_evidence"), packet.get("assistant_recovery"),
                                  max_len=600):
            raise ValueError("recovery_evidence is not an exact bounded quote from assistant_recovery")
    else:
        if _norm(draft.get("recovery_evidence")):
            raise ValueError("recovery_evidence must be empty when recovery has no active outcome")
    if _norm(draft.get("followup_evidence")):
        if not _quote_is_grounded(draft.get("followup_evidence"), packet.get("owner_followup"),
                                  max_len=600):
            raise ValueError("followup_evidence is not an exact bounded quote from owner_followup")
    for field in ("assistant_behavior", "rationale"):
        if not _norm(draft.get(field)) or len(str(draft[field])) > 1200:
            raise ValueError("%s is empty or unbounded" % field)
    # correction_summary describes what was corrected -- required for a corrective row, but a
    # non-corrective interaction (a question, a quoted phrase) has no correction to summarize,
    # so "" is correct there and must not be refused.
    cs = draft.get("correction_summary")
    if len(str(cs or "")) > 1200:
        raise ValueError("correction_summary is unbounded")
    if draft["corrective"] and not _norm(cs):
        raise ValueError("correction_summary is empty for a corrective row")
    if len(str(draft.get("novel_pattern") or "")) > 500:
        raise ValueError("novel_pattern is unbounded")


def _rubric_for(mode):
    """Bundle the mode-specific pieces so main() stays one code path. Part-1 work files have no
    'mode' key -> default 'positive', so the shipped pass is byte-for-byte unaffected."""
    if mode == "corrective":
        return {"mode": "corrective", "version": CORRECTIVE_RUBRIC_VERSION,
                "schema": SCHEMA_CORRECTIVE, "prompt": PROMPT_CORRECTIVE,
                "validate": _validate_corrective, "decision_key": "corrective",
                "control_key": "expected_corrective",
                "packet_fields": ("previous_user_request", "assistant_response",
                                  "assistant_tool_names", "owner_reaction",
                                  "assistant_recovery", "owner_followup")}
    if mode == "positive":
        return {"mode": "positive", "version": RUBRIC_VERSION,
                "schema": SCHEMA, "prompt": PROMPT, "validate": _validate,
                "decision_key": "positive", "control_key": "expected_positive",
                "packet_fields": ("previous_user_request", "assistant_response",
                                  "assistant_tool_names", "owner_reaction")}
    raise SystemExit("REFUSED (rc 2): unknown review mode %r" % mode)


def _read_rows(row_dir, wanted):
    rows = {}
    if not row_dir.exists():
        return rows
    for path in row_dir.glob("*.json"):
        with io.open(path, encoding="utf-8") as f:
            row = json.load(f)
        rid = row.get("id")
        if not rid or rid not in wanted or rid in rows:
            raise SystemExit("REFUSED (rc 2): checkpoint row is unknown, missing-ID, or duplicate: %s" % path)
        rows[rid] = row
    return rows


def _merge_shares(work_path, out, work):
    """Recombine <out>.share-*.json into the full review. Refuses an incomplete or overlapping deal:
    every non-control work item must appear in exactly one share (a merge that tolerated a hole would
    ship a corpus missing rows). Controls never ride shares, but the residual work file must carry
    the parent's controls: the escalation runs as a PLAIN review, whose final assembly refuses a
    controls-free work file."""
    mode = work.get("mode", "positive")
    rubric = _rubric_for(mode)
    dkey = rubric["decision_key"]
    real_ids = {it["id"] for it in (work.get("items") or []) if not it.get("control")}
    outp = Path(out)
    base = out[:-5] if out.endswith(".json") else out
    shares = sorted(outp.parent.glob(Path(base).name + ".share-*.json"))
    if not shares:
        raise SystemExit("REFUSED (rc 2): no share files match %s.share-*.json" % base)
    rows, seen = [], {}
    for sp in shares:
        with io.open(sp, encoding="utf-8") as f:
            sd = json.load(f)
        for row in sd.get("results") or []:
            rid = row.get("id")
            if rid in seen:
                raise SystemExit("REFUSED (rc 2): id %s is in two shares (%s, %s) - overlapping deal"
                                 % (rid, seen[rid], sp.name))
            seen[rid] = sp.name
            rows.append(row)
    covered = set(seen)
    extra = covered - real_ids
    if extra:
        raise SystemExit("REFUSED (rc 2): merge has %d ids absent from the work (%s...)"
                         % (len(extra), sorted(extra)[:3]))
    # the residual = work items no share could ground (small-tier grounding failures + any dead
    # tier's whole share). Write it as an escalation work file for a bigger tier rather than
    # refusing; the caller submits it and re-merges. This is the tier cascade's last rung.
    residual = real_ids - covered
    if residual:
        by_id = {it["id"]: it for it in (work.get("items") or [])}
        res_reals = [by_id[i] for i in sorted(residual) if i in by_id]
        res_controls = [it for it in (work.get("items") or []) if it.get("control")]
        res_items = res_controls + res_reals
        res = dict(work, items=res_items, work_sha256=_canonical_hash(res_items),
                   purpose="ESCALATION residual from " + Path(out).name,
                   candidate_count=len(res_reals), control_count=len(res_controls))
        res_path = (out[:-5] if out.endswith(".json") else out) + ".residual.work.json"
        _atomic_json(Path(res_path), res)
        if not res_controls:
            print("[merge] WARNING: residual carries 0 controls - a plain-run escalation of it will "
                  "refuse at the controls gate; add controls to the work before submitting", flush=True)
        print("[merge] INCOMPLETE: %d of %d reviewed, %d residual (+%d controls) -> escalate %s to a bigger tier"
              % (len(covered), len(real_ids), len(residual), len(res_controls), res_path), flush=True)
        return 2
    errored = [r for r in rows if r.get("error")]
    decision_true = sum(1 for r in rows if r.get(dkey))
    final = {"version": VERSION, "rubric_version": rubric["version"], "mode": mode,
             "work_sha256": work.get("work_sha256"), "status": "COMPLETE",
             "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "merged_shares": [s.name for s in shares],
             "expected": len(real_ids), "completed": len(rows), "errored": len(errored),
             "decision_key": dkey, "decision_true": decision_true,
             "decision_false": len(rows) - decision_true - len(errored), "results": rows}
    _atomic_json(outp, final)
    print("[merge] %d shares -> %s: %d rows (%d %s, %d errored)" % (
        len(shares), out, len(rows), decision_true, dkey, len(errored)), flush=True)
    return 0


def _ship_checkpoint(out, work):
    """Assemble a SHARE_PARTIAL from an existing share checkpoint.

    The factory's stop contract is "a stopped order ships what exists" - but a stopped compute
    order is taskkilled before this payload's end-of-run assembly, so grounded rows survive only
    in the checkpoint, which no other order can resume. This ships them. Refuses to clobber a
    share the run itself assembled. Model/num_ctx/version are deliberately unvalidated: a
    checkpoint ships whichever box and payload generation produced it; mode, rubric and work
    identity must still match."""
    mode = work.get("mode", "positive")
    rubric = _rubric_for(mode)
    items = work.get("items") or []
    ids = {it.get("id") for it in items}
    outp = Path(out)
    if outp.exists():
        raise SystemExit("REFUSED (rc 2): %s already exists - the run shipped it; nothing to recover"
                         % out)
    checkpoint = Path(str(outp) + ".checkpoint")
    meta_path = checkpoint / "meta.json"
    if not meta_path.exists():
        raise SystemExit("REFUSED (rc 2): no checkpoint at %s" % checkpoint)
    with io.open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    for k, v in (("mode", mode), ("rubric_version", rubric["version"]),
                 ("work_sha256", _canonical_hash(items))):
        if meta.get(k) != v:
            raise SystemExit("REFUSED (rc 2): checkpoint %s=%r does not match this work (%r)"
                             % (k, meta.get(k), v))
    rows = _read_rows(checkpoint / "rows", ids)
    if not rows:
        raise SystemExit("REFUSED (rc 2): checkpoint holds zero rows - nothing to ship")
    ordered = [rows[i] for i in sorted(rows)]
    name = outp.name
    share_name = (name[name.index(".share-") + len(".share-"):-len(".json")]
                  if ".share-" in name and name.endswith(".json") else name)
    total = None
    try:
        with io.open(checkpoint / "progress" / "_total.json", encoding="utf-8") as f:
            total = json.load(f).get("total")
    except Exception:
        total = None
    _atomic_json(outp, {"version": VERSION, "rubric_version": rubric["version"], "mode": mode,
                        "work_sha256": meta.get("work_sha256"), "model": meta.get("model"),
                        "num_ctx": meta.get("num_ctx"), "status": "SHARE_PARTIAL",
                        "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "share": share_name,
                        "shipped_from_checkpoint": True, "expected": total or len(ordered),
                        "completed": len(ordered), "failed_ids": [],
                        "decision_key": rubric["decision_key"], "results": ordered})
    print("[ship] %d checkpoint row(s) -> %s (SHARE_PARTIAL)" % (len(ordered), out), flush=True)
    return 0


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("out")
    # Model/context defaults are per-host (loaded from triage_local.json), not order-specific, to allow the same order to run on different box sizes. CLI args override.
    ap.add_argument("--model", default=None)
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--num-ctx", type=int, default=None)
    ap.add_argument("--breaker", default=3, type=int)
    ap.add_argument("--attempts", default=2, type=int)
    ap.add_argument("--items-file", default=None,
                    help="parallel share: review ONLY the item ids in this JSON list; output goes to "
                         "a per-share file that --merge recombines. The desk deals these size-aware.")
    ap.add_argument("--merge", action="store_true",
                    help="combine <out>.share-*.json into the full review, gating on completeness")
    ap.add_argument("--ship-checkpoint", action="store_true",
                    help="assemble a SHARE_PARTIAL from an existing share checkpoint (a stopped "
                         "compute order is taskkilled before its own assembly step; this ships "
                         "what it grounded, honoring the factory's stop contract)")
    ap.add_argument("--exclude", action="append", default=[],
                    help="item id to exclude from the completeness gate (repeatable); a reviewer's "
                         "explicit data-quality disposition, recorded in the output with its reason")
    ap.add_argument("--exclude-reason", default="")
    args, _extra = ap.parse_known_args(argv)

    with io.open(args.work, encoding="utf-8") as f:
        work = json.load(f)

    if args.merge:
        return _merge_shares(args.work, args.out, work)

    if args.ship_checkpoint:
        return _ship_checkpoint(args.out, work)

    if args.model is None or args.num_ctx is None:
        import socket
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "triage_local.json")
        box = socket.gethostname().lower()
        try:
            with io.open(cfg_path, encoding="utf-8") as f:
                row = json.load(f).get(box) or {}
        except Exception as e:                                          # noqa: BLE001
            raise SystemExit("REFUSED (rc 2): no --model given and %s unreadable (%s) - this factory "
                             "has no stated triage customization" % (cfg_path, e))
        if args.model is None:
            args.model = row.get("model")
        if args.num_ctx is None:
            args.num_ctx = int(row.get("num_ctx") or 0)
        if not args.model or not args.num_ctx:
            raise SystemExit("REFUSED (rc 2): triage_local.json has no row for %r - add the box's "
                             "customization or state --model/--num-ctx in the order" % box)
        print("factory customization [%s]: model=%s num_ctx=%d" % (box, args.model, args.num_ctx),
              flush=True)
    mode = work.get("mode", "positive")
    rubric = _rubric_for(mode)
    tag = "%s-review" % mode
    dkey = rubric["decision_key"]
    items = work.get("items") or []
    if not items:
        raise SystemExit("REFUSED (rc 2): work has zero items")
    ids = [it.get("id") for it in items]
    if any(not rid for rid in ids) or len(ids) != len(set(ids)):
        raise SystemExit("REFUSED (rc 2): work item IDs are missing or non-unique")
    computed_sha = _canonical_hash(items)
    if computed_sha != work.get("work_sha256"):
        raise SystemExit("REFUSED (rc 2): work SHA does not match its items")
    excluded = {x.strip() for arg in args.exclude for x in arg.split(",") if x.strip()}
    if excluded:
        if not args.exclude_reason.strip():
            raise SystemExit("REFUSED (rc 2): --exclude requires --exclude-reason")
        unknown = excluded - set(ids)
        if unknown:
            raise SystemExit("REFUSED (rc 2): --exclude names unknown item ids: %s" % sorted(unknown))
        control_ids = {it["id"] for it in items if it.get("control")}
        if excluded & control_ids:
            raise SystemExit("REFUSED (rc 2): a CONTROL can never be excluded: %s"
                             % sorted(excluded & control_ids))

    # PARALLEL SHARE: the desk deals item ids size-aware across the fleet and the foundry passes this
    # box's slice as --items-file. Review ONLY that slice; controls ride separate calibration orders,
    # never a production share, so they are dropped here. --merge recombines the shares.
    share_mode = bool(args.items_file)
    if share_mode:
        with io.open(args.items_file, encoding="utf-8") as f:
            share = set(json.load(f))
        # the desk deals the b64 PACKETS (self-describing size, so it can size-aware the deal) and
        # the foundry writes this box's slice here; match on packet_b64, exactly as triage_classify
        # matches its `item`. Controls never ride a production share.
        before = len(items)
        items = [it for it in items if not it.get("control") and it.get("packet_b64") in share]
        # A share matching 0 items is indistinguishable from a correct empty result; fail closed since every dealt ID must land somewhere.
        if not items:
            raise SystemExit("REFUSED (rc 2): items-file matched 0 of %d work items - the share and "
                             "the work disagree; refusing to ship an empty share" % before)
        ids = [it["id"] for it in items]
        base = args.out[:-5] if args.out.endswith(".json") else args.out
        share_name = os.path.splitext(os.path.basename(args.items_file))[0].replace(".items", "")
        args.out = "%s.share-%s.json" % (base, share_name)
        print("share %s: %d of %d work items -> %s" % (share_name, len(items), before, args.out),
              flush=True)

    out = Path(args.out)
    checkpoint = Path(str(out) + ".checkpoint")
    row_dir, error_dir = checkpoint / "rows", checkpoint / "errors"
    progress_dir = checkpoint / "progress"
    meta_path = checkpoint / "meta.json"
    expected_meta = {"version": VERSION, "rubric_version": rubric["version"], "mode": mode,
                     "work_sha256": computed_sha, "model": args.model, "num_ctx": args.num_ctx}
    if meta_path.exists():
        with io.open(meta_path, encoding="utf-8") as f:
            prior_meta = json.load(f)
        if any(prior_meta.get(k) != v for k, v in expected_meta.items()):
            raise SystemExit("REFUSED (rc 2): checkpoint metadata differs from this work/model/rubric")
    else:
        checkpoint.mkdir(parents=True, exist_ok=True)
        _atomic_json(meta_path, dict(expected_meta, created=time.strftime("%Y-%m-%dT%H:%M:%S")))
    row_dir.mkdir(parents=True, exist_ok=True)
    error_dir.mkdir(parents=True, exist_ok=True)
    progress_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(progress_dir / "_total.json", {"total": len(items)})

    completed = _read_rows(row_dir, set(ids))
    for rid in completed:
        Path(progress_dir / (_safe_id(rid) + ".done")).touch(exist_ok=True)
    print("[%s] %s | %d items, %d resumed | model=%s ctx=%d" % (
        tag, VERSION, len(items), len(completed), args.model, args.num_ctx), flush=True)
    _assert_resident(args.host, args.model, args.num_ctx)

    streak = 0
    for index, item in enumerate(items, 1):
        rid = item["id"]
        if rid in completed:
            print("[%s] %d/%d %s RESUME" % (tag, index, len(items), rid), flush=True)
            continue
        if rid in excluded:
            print("[%s] %d/%d %s EXCLUDED (%s)" % (tag, index, len(items), rid,
                  args.exclude_reason), flush=True)
            continue
        try:
            packet = _decode_packet(item, rubric["packet_fields"])
        except Exception as e:
            raise SystemExit("REFUSED (rc 2): invalid persisted packet %s: %r" % (rid, e))
        review_packet = {k: packet[k] for k in rubric["packet_fields"]}
        prompt = rubric["prompt"] % {"packet": json.dumps(review_packet, ensure_ascii=False, indent=1)}
        estimate = int(len(prompt) / 3.5) + 700
        if estimate > int(args.num_ctx * 0.9):
            # the desk deals size-aware so a correct parallel run never lands an oversized item here;
            # this belt ships it errored (never kills the share, never trips the breaker) so a bigger
            # tier / re-deal can pick it up. A size skip is a property of the item, not the machinery.
            row = {"id": rid, "source": item.get("source"), "locator": item.get("locator"),
                   "timestamp": item.get("timestamp"), "control": bool(item.get("control")),
                   "error": "packet too large for this factory (~%d tok vs 0.9*%d) - reroute to a "
                            "bigger-context box" % (estimate, args.num_ctx)}
            _atomic_json(row_dir / (_safe_id(rid) + ".json"), row)
            completed[rid] = row
            Path(progress_dir / (_safe_id(rid) + ".done")).touch(exist_ok=True)
            print("[%s] %d/%d %s SKIPPED (too large: ~%d tok vs ctx %d)" % (
                tag, index, len(items), rid, estimate, args.num_ctx), flush=True)
            continue
        last_error = None
        for attempt in range(1, max(1, args.attempts) + 1):
            draft = None
            try:
                started = time.time()
                resp = _chat(args.host, args.model, prompt, args.num_ctx, timeout=420,
                             schema=rubric["schema"])
                serving_model = resp.get("model")
                if serving_model != args.model:
                    raise ValueError("serving model %r differs from requested %r" % (serving_model, args.model))
                message = resp.get("message") or {}
                draft = json.loads(message.get("content") or message.get("thinking") or "")
                rubric["validate"](draft, packet)
                row = dict(draft, id=rid, source=item.get("source"), locator=item.get("locator"),
                           timestamp=item.get("timestamp"), signal_terms=item.get("signal_terms") or [],
                           control=bool(item.get("control")), model=serving_model,
                           eval_count=resp.get("eval_count"), seconds=round(time.time() - started, 1),
                           rubric_version=rubric["version"])
                _atomic_json(row_dir / (_safe_id(rid) + ".json"), row)
                completed[rid] = row
                Path(progress_dir / (_safe_id(rid) + ".done")).touch(exist_ok=True)
                streak = 0
                extra = (" recovery=%s" % row.get("recovery")) if mode == "corrective" else ""
                print("[%s] %d/%d %s %s=%s pattern=%s%s in %.1fs" % (
                    tag, index, len(items), rid, dkey, row.get(dkey), row["primary_pattern"],
                    extra, row["seconds"]), flush=True)
                break
            except Exception as e:  # noqa: BLE001 - each failure is persisted and bounded
                last_error = repr(e)[:1000]
                _atomic_json(error_dir / ("%s.attempt-%d.json" % (_safe_id(rid), attempt)),
                             {"id": rid, "attempt": attempt, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                              "error": last_error, "draft": draft})
                print("[%s] %d/%d %s attempt %d ERROR %s" % (
                    tag, index, len(items), rid, attempt, last_error), flush=True)
        if rid not in completed:
            streak += 1
            print("[%s] %s exhausted attempts (consecutive failed rows=%d)" % (
                tag, rid, streak), flush=True)
            if streak >= args.breaker:
                break

    missing = [rid for rid in ids if rid not in completed and rid not in excluded]

    if share_mode:
        # A SHARE SHIPS WHAT IT REVIEWED and records what it could not, so the recombine can escalate
        # the residual to a bigger tier (the "things that still can't be figured out go to the 27B"
        # cascade). A grounding failure on a small tier must NOT throw away that tier's good rows, and
        # a whole share failing (e.g. the model is down) becomes residual rather than lost work.
        ordered = [completed[rid] for rid in ids if rid in completed]
        real = [r for r in ordered if not r.get("control")]
        _atomic_json(out, dict(expected_meta,
                               status="SHARE_COMPLETE" if not missing else "SHARE_PARTIAL",
                               created=time.strftime("%Y-%m-%dT%H:%M:%S"), share=share_name,
                               expected=len(ids), completed=len(ordered), failed_ids=missing,
                               decision_key=dkey, results=ordered))
        print("[%s] SHARE %s %s: %d rows, %d residual -> %s" % (
            tag, share_name, "COMPLETE" if not missing else "PARTIAL", len(real), len(missing), out),
            flush=True)
        return 0

    if missing:
        refusal = dict(expected_meta, status="INCOMPLETE", expected=len(ids), completed=len(completed),
                       missing=missing, failed_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        _atomic_json(checkpoint / "refusal.json", refusal)
        print("[%s] REFUSED incomplete: %d/%d complete" % (tag, len(completed), len(ids)), flush=True)
        return 2

    ordered = [completed[rid] for rid in ids if rid not in excluded]

    controls = []
    for item in items:
        if not item.get("control"):
            continue
        row = completed[item["id"]]
        expected = bool(item[rubric["control_key"]])
        actual = bool(row.get(dkey))
        passed = actual == expected
        rec = {"id": item["id"], "expected": expected, "actual": actual}
        if mode == "corrective" and "expected_recovery" in item:
            rec["expected_recovery"] = item["expected_recovery"]
            rec["actual_recovery"] = row.get("recovery")
            passed = passed and rec["actual_recovery"] == rec["expected_recovery"]
        rec["passed"] = passed
        controls.append(rec)
    if not controls or any(not c["passed"] for c in controls):
        refusal = dict(expected_meta, status="CONTROL_FAILED", controls=controls,
                       failed_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        _atomic_json(checkpoint / "refusal.json", refusal)
        print("[%s] REFUSED controls: %s" % (tag, controls), flush=True)
        return 2

    real = [r for r in ordered if not r.get("control")]
    decision_true = sum(1 for r in real if r.get(dkey))
    final = dict(expected_meta, status="COMPLETE", created=time.strftime("%Y-%m-%dT%H:%M:%S"),
                 expected=len(ids), completed=len(ordered), controls=controls,
                 decision_key=dkey, decision_true=decision_true, decision_false=len(real) - decision_true,
                 excluded_rows=[{"id": rid, "reason": args.exclude_reason} for rid in sorted(excluded)],
                 results=ordered)
    _atomic_json(out, final)
    print("[%s] COMPLETE: %d real rows, %d %s, %d not; controls %d/%d" % (
        tag, len(real), decision_true, dkey, len(real) - decision_true,
        sum(1 for c in controls if c["passed"]), len(controls)), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    sys.exit(main(sys.argv[1:]))
