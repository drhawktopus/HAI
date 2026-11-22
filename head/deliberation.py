"""Bounded multi-pass deliberation and compact external-agent handoffs."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any


def strip_think(text: str) -> str:
    """Drop <think>...</think> reasoning blocks (and a dangling unclosed <think> left when a response
    is cut off) that thinking models (qwen3, deepseek-r1) wrap around their actual answer."""
    text = re.sub(r"(?is)<think\b[^>]*>.*?</think\s*>", " ", text or "")
    text = re.sub(r"(?is)<think\b[^>]*>.*$", " ", text)   # unclosed: drop everything after it
    return text.strip()


def extract_json(content: str):
    """Best-effort parse of a model response into a JSON value, tolerant of reasoning-model output:
    strip <think> blocks, unwrap a ```json fence (whole-text OR embedded in prose), else scan for the
    first balanced JSON object/array anywhere in the text. THE canonical robust extractor — both the
    critique parser and the decomposition-plan parser use this so a thinking model's wrapping never
    silently discards an otherwise-good answer. Raises ValueError when nothing parses."""
    text = strip_think((content or "").strip())
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)                                  # whole text is JSON
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if m:                                                        # a fenced block somewhere in prose
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):                               # first balanced {...}/[...] in prose
        if ch in "{[":
            try:
                value, _end = decoder.raw_decode(text, i)
                return value
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON value found in model response")


EFFORT_MODES = {
    "normal": {
        "label": "Normal",
        "description": "One lead pass with risk-based verification and review.",
    },
    "think_hard": {
        "label": "Think Really Hard",
        "description": "Bounded local first pass, independent critique, and final refinement.",
    },
    "absurd": {
        "label": "Absurd",
        "description": "The think-hard engine with a much larger async budget — iterate to convergence "
                       "over up to hours, run detached, notify on completion.",
    },
}

# Absurd runs use the SAME convergence engine as think-hard, just with a far bigger ceiling. These cap
# a pathological/non-terminating loop; the caller's chosen time budget is the real bound and the loop
# still stops early on converge/stall. (Kept in deliberation so EffortState stays self-contained/testable.)
ABSURD_MAX_BUDGET_SECONDS = 36_000   # 10h hard ceiling (covers an "all-night" pick)
ABSURD_MAX_PASSES = 200

EXTERNAL_USES = {
    "direct": {
        "label": "Direct",
        "description": "Send the original task with minimal local preparation.",
    },
    "interactive": {
        "label": "Normal interaction",
        "description": "Use the external agent as the primary interactive worker.",
    },
    "background_review": {
        "label": "Background review",
        "description": "Local 27B leads; the external agent reviews a compact evidence packet.",
    },
    "dress_to_impress": {
        "label": "Dress to Impress",
        "description": "Run local preparation, verification, review, and refinement before handoff.",
    },
}

CRITIQUE_SYSTEM = (
    "You are an independent critic reviewing another model's completed response. "
    "Do not redo the whole task. Find concrete factual, technical, security, policy, or verification "
    "problems. Treat as a problem (verdict 'changes_requested') any current/literal/time-sensitive claim "
    "— command, model tag, package name, version, price, law, spec, or live system/device state — that is "
    "asserted confidently WITHOUT evidence from a tool, web_search, or doc lookup, and any claim that an "
    "action was performed without a tool result proving it. Reward verified, evidence-cited answers; do "
    "not reward confident-sounding but unverified ones. Avoid style-only churn. Return JSON with verdict "
    "('pass' or 'changes_requested'), summary, and findings. Each finding has severity, explanation, and "
    "recommended_action."
)


# Kept separate from CRITIQUE_SYSTEM: consumers like DIFF review and plan critic
# are already evidence-grounded; widening the shared constant causes regressions.
CHAT_CRITIQUE_SYSTEM = (
    CRITIQUE_SYSTEM +
    "\n\nYou are given the EVIDENCE TRAIL: the tools the responder actually called this turn and what "
    "they returned. USE IT. A claim supported by a trail entry IS verified — say so and do not flag "
    "it. This is a PRIVATE homelab: hostnames, drive paths, filenames, and internal architecture are "
    "expected to be unfamiliar to you, and your own inability to recognise them is NOT evidence "
    "against them. Never flag a claim merely because you personally cannot confirm it or because it "
    "sounds specific — flag it when the trail does not support it, when the trail CONTRADICTS it, or "
    "when the responder describes an action or a search that has no corresponding trail entry. "
    "BEFORE claiming 'the evidence trail does not contain [a tool call / evidence for X]', scan every "
    "entry in the rendered trail text yourself: check each tool name, its arguments, and its result. "
    "A status check returning usable=True with no VOID flag IS evidence of successful completion; "
    "coverage=N/N with measured rows=N IS evidence the data was produced. Do not require the trail "
    "entry to use the exact phrasing from the answer — match on substance (same tool, same target, "
    "supporting outcome). Only after you have checked every entry and found no match may you claim "
    "the tool call is absent. If the header states some calls were omitted for length, treat those "
    "omitted calls as having happened: do not flag their absence. "
    "If the trail is empty, unverified lab-specific claims are fair findings. "
    "Do NOT reward vagueness: an answer made safe by hedging away verifiable detail is WORSE than a "
    "specific answer backed by the trail, and generic statements about how AI assistants work are not "
    "a substitute for what this system's own tools reported."
)


# The critic must see the full work trail. `budget_chars` is set by the caller based on the critic's
# token window to prevent truncation. It fills with whole steps (most relevant first), not fragments.
# `budget_chars=None` means no limit (caller verified the trail fits).

# Tool results that must NEVER be replayed into another model call. `get_credential` returns a
# credential VALUE; the locker is the only home for those and an extra inference is an extra copy.
# (This keeps the G2b widening from becoming a new path for a secret. Pinned by tests/test_credentials_exclusion.py.)
_EV_REDACT_TOOLS = {"get_credential"}


def reference_block(relevant, budget_chars=None) -> str:
    """The library excerpts the responder was HANDED before answering (the RAG seed), for the critic. An answer that
    rests on them is supported even with no tool call - without this block the critic flags it as unsupported (10-16:
    "all three hosts on 0.40.5 [RUNBOOK-Ollama-Host-Operations]" was correct and drew 3 "unsupported" findings).
    Whole excerpts, in retrieval order, as many as `budget_chars` holds (None = all)."""
    chunks = [c for c in (relevant or []) if isinstance(c, dict)]
    if not chunks:
        return "REFERENCE NOTES: none were given to the responder this turn."
    lines, total = [], 0
    for c in chunks:
        entry = "- [%s]\n    %s" % (str(c.get("source") or "?"), str(c.get("text") or ""))
        if budget_chars is not None and total + len(entry) > budget_chars:
            continue                                  # a smaller excerpt may still fit
        lines.append(entry)
        total += len(entry)
    header = ("REFERENCE NOTES the responder was given before answering - %d of %d library excerpt(s); a claim that "
              "matches one of these IS supported by it:" % (len(lines), len(chunks)))
    if len(lines) < len(chunks):
        header += (" (%d further excerpt(s) did not fit the critic's window - their absence here is NOT evidence "
                   "they were not given)" % (len(chunks) - len(lines)))
    return header + "\n" + "\n".join(lines)


_EV_WORK_TOOLS = {"coding_patch", "coding_create", "coding_file", "coding_test", "coding_git", "coding_checkpoint"}
_EV_TOKEN = re.compile(r"[A-Za-z0-9_.:/-]{5,}")


def _full(s, key):
    """A trail field at full length: the in-memory trail carries `<key>_full`; a compact copy only `<key>`."""
    return str(s.get(key + "_full", s.get(key)) or "")


def _ev_rank(steps, answer):
    """The ORDER in which steps earn a place in the critic's window when not all fit (charter WORK item 6: "the steps
    that bear on the claims" - the BLIND REVIEWER class). Score: the work itself (edits, test runs) first, then the end
    of the trail (where it stands), then steps whose result shares distinctive tokens (paths, names, numbers) with the
    answer being judged. Every step is ranked; nothing is dropped here."""
    said = set(t.lower() for t in _EV_TOKEN.findall(answer or ""))
    n = len(steps)

    def score(i):
        s = steps[i]
        sc = 0
        if str(s.get("tool") or "") in _EV_WORK_TOOLS:
            sc += 100
        if i >= n - 3:
            sc += 50
        if said:
            shared = said & set(t.lower() for t in _EV_TOKEN.findall(_full(s, "result")))
            sc += 10 * min(len(shared), 4)
        return (sc, i)   # ties: the later step

    return sorted(range(n), key=score, reverse=True)


def _ev_pick(steps, answer, limit):
    """The `limit` highest-ranked steps, in trail order (kept for callers that ask for a count)."""
    return sorted(_ev_rank(steps, answer)[:limit])


def _ev_entry(s, i):
    name = str(s.get("tool") or "?")
    result = ("[REDACTED — credential value, not replayed into a review]" if name in _EV_REDACT_TOOLS
              else _full(s, "result"))
    return "- [step %s] %s(%s) ->\n    %s" % (s.get("step", i + 1), name, _full(s, "args"), result)


def evidence_block(transcript, answer=None, budget_chars=None) -> str:
    """Render a first pass's TOOL TRAIL as evidence for the critic: WHOLE steps (full arguments and results), every
    one that fits `budget_chars` - sized by the caller from the critic's own window (None = all of them). When the
    trail is longer than the window, the steps that bear on the answer's claims come first (_ev_rank).

    Unlike the THINKING trail (which is display-only and never re-fed to models),
    this TOOL trail is fed to the CRITIC model so it can verify lab-specific facts.
    Without it, the critic defaults to generic LLM priors, incorrectly flagging valid
    orchestrator paths as hallucinations or passing unsupported claims as fact.

    Distinguishes 'no tools were called' (answer is unevidenced; critic should be strict)
    from 'no trail was captured' (instrument failure; verdict is unjustified)."""
    if transcript is None:
        return ("EVIDENCE TRAIL: UNAVAILABLE — this turn's tool trail was not captured, so absence of "
                "evidence here proves nothing either way. Judge the response on its own terms and do "
                "not treat missing trail entries as proof a claim is unsupported.")
    steps = list(transcript)
    if not steps:
        return ("EVIDENCE TRAIL: EMPTY — the responder called NO tools this turn. Any lab-specific or "
                "current/literal claim in the response is therefore unverified, and describing a "
                "search, lookup, or action that produced this empty trail is a fabrication.")

    chosen, total = [], 0
    for i in _ev_rank(steps, answer):
        entry = _ev_entry(steps[i], i)
        if budget_chars is not None and total + len(entry) > budget_chars:
            continue                                  # a smaller step may still fit
        chosen.append((i, entry))
        total += len(entry)
    lines = [e for _, e in sorted(chosen)]            # shown in trail order
    shown = len(lines)

    header = f"EVIDENCE TRAIL — {shown} of {len(steps)} tool call(s) the responder actually made:"
    if shown < len(steps):
        header += (f"\n({len(steps) - shown} further call(s) did not fit the critic's window — their absence here "
                   "is a window limit, NOT evidence they did not happen.)")
    return header + "\n" + "\n".join(lines)


# A clinical second opinion run by a medical model (MedGemma) on nursing/EBP answers, IN ADDITION to the
# general critic above. Completion-only (no tools) — it judges the answer text for clinical safety and
# citation integrity. Returns the SAME JSON contract as CRITIQUE_SYSTEM so parse_critique handles both.
NURSING_CRITIQUE_SYSTEM = (
    "You are an independent CLINICAL reviewer providing a medical second opinion on another model's "
    "completed answer to a nursing or evidence-based-practice question. Do NOT redo the task and do NOT use "
    "any tools — judge only the answer text in front of you. Review it for CLINICAL SAFETY and ACCURACY. "
    "Treat as a problem (verdict 'changes_requested'): any incorrect or unsafe drug name, class, dose, "
    "route, frequency, indication, contraindication, or interaction; any wrong lab value, normal range, or "
    "assessment finding; advice that is backwards or could harm a patient; scope-of-practice or "
    "clinical-judgment errors; certainty that overstates the cited evidence; and ESPECIALLY any citation "
    "that appears fabricated, mismatched to the claim it supports, or not actually backed by the stated "
    "source. Do not invent new sources. Reward clinically sound, evidence-grounded, appropriately-hedged "
    "answers; do not reward confident but unsupported clinical claims. Avoid style-only churn. Return JSON "
    "with verdict ('pass' or 'changes_requested'), summary, and findings. Each finding has severity, "
    "explanation, and recommended_action."
)


# Author disputes reviewer findings; critic adjudicates.
# Only severities in config.ADVERSARY_DISPUTE_SEVERITIES are disputable.
# Caller enforces stricter quorum for high/critical.
# Both prompts receive the diff to ground arguments in code.
# Output: Robust JSON.
DISPUTE_AUTHOR_SYSTEM = (
    "You are the AUTHOR of a code change that is under adversarial review. You are shown the diff of "
    "your change and a list of reviewer findings. For EACH finding decide 'fix' or 'dispute'. Choose "
    "'dispute' ONLY when the finding is genuinely non-material, already handled by the code, or factually "
    "wrong -- and give a CONCRETE, specific rationale grounded in the actual code shown (quote the exact "
    "lines that disprove the finding; not 'it is fine' or 'this is minor'). For a finding that claims a "
    "BEHAVIORAL REGRESSION, your rationale must walk the relevant control flow in the diff and show the "
    "behavior is preserved. A dispute with a vague or hand-wavy rationale will be rejected, so when in "
    "doubt choose 'fix'. Never dispute something that reflects a real improvement, however small. Return "
    "JSON: an array with one object per finding IN THE ORDER GIVEN, each "
    '{"index": <int>, "action": "fix"|"dispute", "rationale": <string>}.'
)

DISPUTE_ADJUDICATE_SYSTEM = (
    "You are an INDEPENDENT reviewer adjudicating a DISPUTE. The author of a code change disputes a "
    "single review finding, arguing it should not require a change. You are given the diff of the "
    "change, the finding, and the author's rationale. Decide whether the dispute is VALID -- i.e., the "
    "finding really is non-material, already handled, or incorrect. VERIFY the rationale against the "
    "diff yourself: check that the lines the author cites actually exist and behave as claimed. Be "
    "skeptical: uphold the dispute ONLY if the rationale is concrete, specific, and correct against the "
    "code shown; reject vague, generic, or hand-wavy rationales, and reject any dispute of something "
    "that is a genuine improvement. For a HIGH or CRITICAL finding, uphold ONLY if the diff itself "
    "DEMONSTRATES the finding is false (e.g. the claimed regression's control flow is visibly preserved); "
    "otherwise reject. When uncertain, REJECT (the finding then simply stands). Return JSON: "
    '{"valid": true|false, "reason": <string>}.'
)


def merge_critiques(primary: dict, secondary: dict, second_label: str = "medical") -> dict:
    """Combine two critiques into one for the refinement pass.

    verdict = 'changes_requested' if EITHER asks for changes; else 'pass' if either passed; else
    'unavailable'. Findings are concatenated, with the secondary critique's findings tagged
    `source=second_label` so the refinement (and deep trace) can tell the medical notes apart.
    Summaries are joined."""
    findings = list(primary.get("findings") or [])
    for finding in (secondary.get("findings") or []):
        finding = dict(finding)
        finding.setdefault("source", second_label)
        findings.append(finding)
    verdicts = {primary.get("verdict"), secondary.get("verdict")}
    if "changes_requested" in verdicts:
        verdict = "changes_requested"
    elif "pass" in verdicts:
        verdict = "pass"
    else:
        verdict = "unavailable"
    summaries = [s for s in (primary.get("summary"), secondary.get("summary")) if s]
    return {"verdict": verdict, "summary": " | ".join(summaries), "findings": findings}


class DeliberationError(RuntimeError):
    pass


_STUB_PREFIXES = (
    "let me look", "let me check", "let me see", "let me pull", "let me grab", "let me fetch",
    "let me inspect", "let me read", "i'll look", "i'll check", "i will look", "i will check",
    "one moment", "hold on", "give me a moment",
)


# These probes MUST be woven into the prompt BEFORE CRITIQUE_SYSTEM to preserve JSON output contract;
# appending them after breaks parsing.
CRITIQUE_OWNER_PROBES = (
    "\nAdditional adversarial probes (apply to every claim in the draft):"
    "\n- INVERSION: a result contradicting a known baseline or ordering indicts the instrument or"
    " configuration first — flag the surprise, do not celebrate it."
    "\n- MAGNITUDE: a number wildly off its plausible scale is a finding, not a detail."
    "\n- CONVENIENT CONCLUSION: apply EXTRA scrutiny to any claim that excuses effort, dismisses"
    " an alert, or concludes 'no problem here' — convenience is a red flag, never evidence."
    "\n- FORM OF VERIFICATION: the check must match the claim's shape — an absence claim needs a"
    " search of the right scope; an 'applied/deployed/fixed' claim needs the live object measured,"
    " not a setting read back; a 'cannot be done' claim needs the hard path actually probed."
    "\n- QUOTE OR INVENTED: a requirement, preference, or instruction attributed to the user must"
    " be quotable from the evidence trail; otherwise flag it as invented."
    "\n- ENTITY PRECISION: flag names doing double duty and swapped referents (host/model/file"
    " names that do not match the trail)."
    "\n- PROMISED VS SHOWN: state flatly where the draft promises more than the trail shows."
)

CRITIQUE_OWNER_PROBES_V2 = (
    "Apply these additional probes to every claim: a result contradicting a known baseline or "
    "ordering indicts the instrument or configuration before it earns celebration, and an "
    "implausible magnitude is itself a finding. Give extra scrutiny to any claim that excuses "
    "effort, dismisses an alert, or concludes 'no problem here' — convenience is a red flag, not "
    "evidence. Check that each verification matches its claim's shape: an absence claim needs a "
    "search of the right scope; an applied/deployed/fixed claim needs the live object measured, "
    "not a setting read back; anything attributed to the user must be quotable from the evidence "
    "or flagged as invented. Flag names doing double duty and referents that do not match the "
    "evidence. "
)

TH_REFINE_RULES = (
    "\n\nTHINK REALLY HARD - FINAL REFINEMENT: Produce the COMPLETE final answer to the user's "
    "ORIGINAL request (restated below). Treat the independent critique as SILENT internal QA notes "
    "to incorporate — do NOT reply to the critique, address it conversationally, or mention its "
    "existence; the user never sees it. For any claim the critique flags as unverified you have "
    "exactly three legal moves, in preference order: (1) GATHER the missing evidence with the "
    "appropriate tool (catch_up/web_search/search_docs/get_document/check_*/read_config/fetch_url) "
    "THIS turn and cite it; (2) if the evidence cannot be gathered this turn, REMOVE the claim; "
    "(3) or restate it as explicit self-report ('I have no record of X and no way to check'). An "
    "honest inability statement passes review; an unverified specific claim never does — NEVER "
    "present a status, count, or reading that no tool produced this turn. Reject a critique "
    "finding ONLY when the evidence trail directly contradicts it. Do NOT narrate that you will "
    "look something up later. Return the finished answer to the original request now, citing "
    "evidence; never expose private chain-of-thought."
)
# Retract/self-report moves are required to allow convergence when evidence is unreachable;
# rejecting unsupported findings requires a contradictory evidence trail (TRAIL) to prevent
# the fabricator from overruling the reviewer without proof.


def _finding_line(f, *, max_len=160):
    """Format one finding dict or bare string as a dash-prefixed summary line."""
    txt = str(f.get("explanation") or f.get("summary") or f) if isinstance(f, dict) else str(f)
    return "- " + " ".join(txt.split())[:max_len]


def _finding_severity(f):
    """Return a finding's severity as a lowercase string. Handles dicts with or without the key."""
    if isinstance(f, dict):
        return str(f.get("severity", "")).lower()
    return ""


def unresolved_verdict(converge_result, final_critique, cap=3):
    """Return (action, text) for an unresolved-review gate.

    action is one of:
      "ship"   — converged, or nothing unresolved.
      "warn"   — unresolved findings, none critical; text is the banner string.
      "refuse" — at least one unresolved finding has severity "critical".
                 The answer must not ship as-is; text is a refusal message.

    An answer only reaches "warn"/"refuse" when convergence stopped due to stall,
    regression, or budget exhaustion (NOT reviewer sign-off), so fabricated status
    reports are flagged or refused rather than silently shipped. This is the ONE
    derivation of 'is this review unresolved'; `unresolved_note` delegates here.
    Severity comparison is case-insensitive and does not rely on parse_critique
    having normalized the findings."""
    if not converge_result or converge_result.get("converged"):
        return ("ship", None)
    if (final_critique or {}).get("verdict") != "changes_requested":
        return ("ship", None)
    findings = (final_critique or {}).get("findings") or []
    if not findings:
        return ("ship", None)

    has_critical = any(_finding_severity(f) == "critical" for f in findings)

    if has_critical:
        critical_findings = [f for f in findings if _finding_severity(f) == "critical"]
        other_findings = [f for f in findings if _finding_severity(f) != "critical"]
        ordered = [_finding_line(f) for f in critical_findings + other_findings]
        shown = ordered[:cap]
        extra = len(ordered) - len(shown)
        more = "\n(+%d more in the stored trail)" % extra if extra > 0 else ""
        message = ("I can't stand behind this answer: internal review flagged %d unresolved "
                   "CRITICAL finding(s) that did not converge within budget:\n%s%s\n"
                   "The draft answer is preserved in the stored trail."
                   % (len(critical_findings), "\n".join(shown), more))
        return ("refuse", message)

    lines = [_finding_line(f) for f in findings[:cap]]
    extra = len(findings) - len(lines)
    more = "\n(+%d more in the stored trail)" % extra if extra > 0 else ""
    banner = ("⚠ Internal review flagged %d unresolved finding(s) that did not converge within "
              "budget — weigh the affected claims accordingly:\n%s%s"
              % (len(findings), "\n".join(lines), more))
    return ("warn", banner)


def unresolved_note(converge_result, final_critique, cap=3):
    """Return the unresolved-review text (banner or refusal message) or None.

    Thin wrapper over `unresolved_verdict` for callers that expect only the text half."""
    _, text = unresolved_verdict(converge_result, final_critique, cap=cap)
    return text


def refinement_regressed(refined, draft):
    """True when a think-hard refinement looks worse than the first-pass draft, so the caller
    should keep the draft. Catches the collapse where the model narrates a tool action it won't
    finish (e.g. 'Let me look at the services.yaml...') or returns a stunted fragment."""
    r = (refined or "").strip()
    if len(r) < 2:
        return True
    low = r.lower()
    # Narrated-action stub that promises a lookup instead of answering.
    if any(low.startswith(p) for p in _STUB_PREFIXES) and len(r) < 400:
        return True
    # A hard collapse relative to a solid draft (short in absolute + relative terms).
    if len(r) < 120 and len(r) < len(draft or "") * 0.5:
        return True
    return False


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated at {limit} characters]"


def clip_for_review(text: str, limit: int) -> str:
    """Clip a diff/artifact for an adversarial-review prompt. If (and only if) it's actually clipped,
    append an EXPLICIT note so the critic does NOT mistake OUR length-truncation for the author's
    incomplete work -- a real failure mode: an 8000-char cap made critics flag "diff incomplete / test
    file truncated / duplication remains" on a COMPLETE change whose tail got cut. Pure/testable."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    return (text[:limit]
            + f"\n\n[REVIEW NOTE: the diff above was truncated to {limit} chars for review length. The "
              "actual change is COMPLETE; do NOT report it as incomplete, truncated, cut-off, or as leaving "
              "duplicated code -- those would be artifacts of this truncation, not real findings.]")


# --- False-absence validator ---
# The critic (an LLM following CHAT_CRITIQUE_SYSTEM instructions) has a recurring failure mode:
# asserting "the trail does not contain X" when the trail plainly contains X. Class-6 hole: a
# diagnosed defect in a guard, fixed prompt-side above AND code-side here. This validator is a
# programmatic sanity check on the critic's absence claims; when it can HIGH-CONFIDENCE prove the
# trail contains what the finding claims is missing, the finding is dropped.
#
# High-precision by design: over-matching would silently discard REAL findings, which is worse
# than letting a few false-absence claims through. When in doubt, keep the finding.

_ABSENCE_PATTERNS = (
    "trail does not contain",
    "trail contains no",
    "trail does not include",
    "no tool call in",
    "no tool calls in",
    "no tool calls",
    "no corresponding trail entry",
    "no such tool call",
    "not present in the trail",
    "does not appear in the trail",
    "there is no tool call",
    "have no tool call",
    "no entry for",
    "no evidence of",
    "no evidence for",
)


# Pending-status phrases the tablet_map / worker tools emit while an order is still
# in flight. When the critic quotes one of these and reports failure without scanning
# for the LATER status entry showing the same order landed, that is the CHERRY-PICK
# shape below (as opposed to a bare absence claim).
_PENDING_STATUS_MARKERS = (
    "nothing in the letter slot",
    "no shipped results",
    "not yet accepted",
    "in the letter slot",
    "still pending",
    "still running",
)


# Success markers the factory / tablet tools emit when work lands cleanly. Presence of
# any of these ALONGSIDE the same order id in one trail step contradicts a finding
# that claims the order failed based on an earlier pending observation.
#
# Every entry MUST be worded so it cannot appear as a substring of a pending phrase: a member
# like "shipped results for" sits inside the pending "no shipped results for" and would fire on
# the SAME text the pending markers catch. The list holds unambiguous positive tokens only.
_SUCCESS_MARKERS = (
    "usable=true",
    "results.json:",
    "control=true",
    "control_passed=true",
    "coverage=",
    "nodes healthy",
)


# Order-id shape: at least one letter and one digit, permits hyphens, underscores,
# dots. Matches "phase5-warmup-c01-b", "phase5-warmup-c01-b.20260905-081703".
# No trailing `[a-z0-9]`: it would force ONE extra alphanumeric AFTER the last group
# iteration and strip the final segment ("-b") off short-tailed ids.
_ORDER_ID_RE = re.compile(r"\b[a-z][a-z0-9]*(?:[_.-][a-z0-9]+)+\b", re.IGNORECASE)


def _finding_asserts_absence(explanation: str) -> bool:
    """True if the finding's explanation claims a tool call / trail entry is missing."""
    if not explanation:
        return False
    low = explanation.lower()
    return any(p in low for p in _ABSENCE_PATTERNS)


def _finding_asserts_bare_tool_absence(explanation: str, trail_tools) -> tuple[bool, str]:
    """Detects bare tool-name absence claims that _ABSENCE_PATTERNS misses because they
    lack the 'tool call' scaffold — 'there is no `fleet_status` or equivalent',
    'no tablet_map', 'without any fleet_status invocation', 'missing tablet_map entry'.

    Fires only when the finding names a tool that IS in the trail; otherwise fail-open.
    Returns (True, tool_name) on match, (False, '') otherwise."""
    if not explanation or not trail_tools:
        return (False, "")
    low = explanation.lower()
    for tool in trail_tools:
        t = tool.lower().strip()
        if not t:
            continue
        t_esc = re.escape(t)
        patterns = (
            rf"\bno\s+`{t_esc}`",
            rf"\bthere\s+is\s+no\s+`{t_esc}`",
            rf"\bthere\s+is\s+no\s+{t_esc}\b",
            rf"\bno\s+{t_esc}\s+(?:call|invocation|entry|check|request|action)",
            rf"\bno\s+{t_esc}\s+or\s+equivalent",
            rf"\bno\s+`?{t_esc}`?\s+or\s+equivalent",
            rf"\bwithout\s+(?:any\s+)?{t_esc}\b",
            rf"\bmissing\s+{t_esc}\b(?:\s+(?:call|entry|invocation))?",
            rf"\bcannot\s+find\s+(?:any\s+)?{t_esc}\b",
            rf"\bfails?\s+to\s+(?:call|invoke)\s+{t_esc}\b",
        )
        for pat in patterns:
            if re.search(pat, low):
                return (True, tool)
    return (False, "")


def _finding_cherry_picks_early_status(finding: dict, transcript) -> tuple[bool, str]:
    """Detects cherry-picking of an early pending status while ignoring later success.

    Fires only when the finding quotes a pending status AND names an order id AND the
    trail contains a later success for that id. Fails open otherwise to preserve real
    failures."""
    if not transcript:
        return (False, "")
    if not isinstance(finding, dict):
        return (False, "")
    explanation = str(finding.get("explanation") or "")
    if not explanation:
        return (False, "")
    low = explanation.lower()
    if not any(m in low for m in _PENDING_STATUS_MARKERS):
        return (False, "")
    ids = set()
    for m in _ORDER_ID_RE.finditer(explanation):
        s = m.group(0).lower()
        if len(s) >= 6 and any(c.isalpha() for c in s) and any(c.isdigit() for c in s):
            ids.add(s)
    if not ids:
        return (False, "")
    for step in transcript:
        try:
            args = str(step.get("args") or "").lower()
            result = str(step.get("result") or "").lower()
        except AttributeError:
            continue
        combined = args + " " + result
        for subj in ids:
            if subj in combined and any(mk in combined for mk in _SUCCESS_MARKERS):
                return (True,
                        f"finding quotes pending status for '{subj}' but trail contains a success "
                        f"entry for the same order id")
    return (False, "")


def _extract_tool_names_from_trail(transcript) -> set:
    """Distinct tool names actually called in this turn's trail."""
    if not transcript:
        return set()
    names = set()
    for step in transcript:
        try:
            tool = step.get("tool")
        except AttributeError:
            continue
        if isinstance(tool, str) and tool.strip():
            names.add(tool.strip())
    return names


def _trail_mentions(transcript, needle: str) -> bool:
    """True if any tool call's args or result contains `needle` (case-insensitive substring)."""
    if not transcript or not needle:
        return False
    n = needle.lower()
    for step in transcript:
        try:
            args = str(step.get("args") or "").lower()
            result = str(step.get("result") or "").lower()
        except AttributeError:
            continue
        if n in args or n in result:
            return True
    return False


def _finding_contradicted_by_trail(finding: dict, transcript) -> tuple[bool, str]:
    """(contradicted, reason) — True when a critic finding is refuted by trail evidence.

    Four escalating checks, in precision-first order:
      1a. BARE tool-name absence: finding says 'no `fleet_status` or equivalent' and the trail
          contains a fleet_status call. Fires without needing an _ABSENCE_PATTERNS scaffold.
      1b. Absence-phrase + tool-name-in-finding: legacy _ABSENCE_PATTERNS gate plus tool-name
          mention in the finding matching a trail tool.
      2.  Quoted-output match: an absence-phrase finding quotes a specific outcome (contains
          =, :, or /) that appears verbatim in the trail's args or results.
      3.  Cherry-picked early status: finding quotes an early pending phrase for an order id
          AND the trail contains a later step where the same id appears with a success marker.

    Returns (False, "") when the trail is empty (an absence claim about an empty trail is
    legitimate) or when no check fires."""
    if not transcript:
        return (False, "")
    explanation = finding.get("explanation") or ""
    if not explanation:
        return (False, "")
    trail_tools = _extract_tool_names_from_trail(transcript)
    # (1a) BARE tool-name absence — dynamic patterns from actual trail tool names.
    bare_hit, tool = _finding_asserts_bare_tool_absence(explanation, trail_tools)
    if bare_hit:
        return (True, f"finding claims '{tool}' is absent but trail contains a {tool} call")
    # (1b) Legacy absence-phrase + tool-name mention.
    if _finding_asserts_absence(explanation):
        low = explanation.lower()
        for t in trail_tools:
            if t.lower() in low:
                return (True, f"finding claims absence but trail contains tool call '{t}'")
        # (2) Quoted-output match — extract quoted strings from the finding, check the trail for them.
        # Only quotes that look like tool outputs (contain =, :, or /) to avoid matching generic prose.
        quoted = re.findall(r"['\"]([^'\"]{6,120})['\"]", explanation)
        for q in quoted:
            if not any(c in q for c in "=:/"):
                continue
            if _trail_mentions(transcript, q):
                snippet = q[:60] + ("..." if len(q) > 60 else "")
                return (True, f"finding claims absence of '{snippet}' but trail contains it verbatim")
    # (3) Cherry-picked early status — pending phrase quoted for an order id whose trail
    # step later carries a success marker.
    cherry_hit, cherry_reason = _finding_cherry_picks_early_status(finding, transcript)
    if cherry_hit:
        return (True, cherry_reason)
    return (False, "")


def filter_false_absence_findings(findings: list, transcript) -> tuple[list, list]:
    """(kept, dropped) — remove findings whose false-absence claims are refuted by the trail.

    Dropped findings carry `_dropped_reason` so callers can log why. Fail-open on the validator:
    if transcript is None or empty, nothing is dropped."""
    kept, dropped = [], []
    for f in (findings or []):
        contradicted, reason = _finding_contradicted_by_trail(f, transcript)
        if contradicted:
            annotated = dict(f)
            annotated["_dropped_reason"] = reason
            dropped.append(annotated)
        else:
            kept.append(f)
    return (kept, dropped)


def parse_critique(content: str, transcript=None) -> dict[str, Any]:
    try:
        value = extract_json(content)   # robust to <think>/prose wrapping (reasoning-model critics)
    except ValueError as exc:
        raise DeliberationError("critic returned invalid JSON") from exc
    if isinstance(value, list):
        # some models return a BARE findings array instead of the {verdict, findings} object --
        # treat a non-empty array as changes_requested findings, an empty one as a pass.
        value = {"verdict": "changes_requested" if value else "pass", "findings": value}
    if not isinstance(value, dict):
        raise DeliberationError("critic returned a non-object JSON")
    verdict = str(value.get("verdict", "")).strip().lower().replace(" ", "_")
    if verdict in {"approve", "approved"}:
        verdict = "pass"
    if verdict in {"fail", "reject", "rejected"}:
        verdict = "changes_requested"
    findings = value.get("findings", [])
    if verdict not in {"pass", "changes_requested"} or not isinstance(findings, list):
        raise DeliberationError("critic returned an invalid review structure")
    normalized = []
    for finding in findings[:12]:
        if not isinstance(finding, dict):
            continue
        entry = {
            "severity": str(finding.get("severity", "medium")).lower()[:20],
            "explanation": _clip(
                finding.get("explanation") or finding.get("description") or finding.get("message"),
                2_000,
            ),
            "recommended_action": _clip(
                finding.get("recommended_action")
                or finding.get("recommendation")
                or finding.get("suggestion"),
                1_000,
            ),
        }
        # optional evidence quotes (the adversarial ladder's anchoring gate verifies these verbatim
        # against the diff the critic saw). Normalized to a list of non-empty strings; absent if none.
        evidence = finding.get("evidence") or finding.get("quote")
        if isinstance(evidence, str):
            evidence = [evidence]
        if isinstance(evidence, list):
            evidence = [_clip(q, 500) for q in evidence if isinstance(q, str) and q.strip()][:4]
            if evidence:
                entry["evidence"] = evidence
        grounding = finding.get("violated") or finding.get("criterion") or finding.get("requirement")
        if isinstance(grounding, str) and grounding.strip():
            entry["violated"] = _clip(grounding, 300)
        normalized.append(entry)
    if verdict == "pass" and normalized:
        verdict = "changes_requested"
    if verdict == "changes_requested" and not normalized:
        reasoning = value.get("reasoning") or value.get("summary")
        if reasoning:
            normalized.append({
                "severity": "medium",
                "explanation": _clip(reasoning, 2_000),
                "recommended_action": "",
            })
        else:
            raise DeliberationError("critic requested changes without a finding")
    # Code-side sanity check on the critic's absence claims. Companion to the prompt-side
    # strengthening in CHAT_CRITIQUE_SYSTEM: drops findings whose "trail does not contain X"
    # claims are refuted by the trail itself. Fail-open when transcript is not provided,
    # so tests + non-chat call sites (which pass no transcript) keep their existing behavior.
    if transcript is not None and normalized:
        kept, dropped = filter_false_absence_findings(normalized, transcript)
        if dropped:
            normalized = kept
            if verdict == "changes_requested" and not normalized:
                verdict = "pass"
                summary_override = ("Critic findings were all false-absence claims refuted by "
                                    "the trail; verdict downgraded to pass by the sanity check.")
                value["summary"] = summary_override
    summary = value.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        summary = normalized[0]["explanation"] if normalized else "No actionable findings."
    return {"verdict": verdict, "summary": _clip(summary, 2_000), "findings": normalized}


def merge_decorrelated(critiques, *, max_findings: int = 24) -> dict[str, Any]:
    """UNION the findings of several INDEPENDENT critic models (a decorrelated pool) into one critique,
    deduping near-duplicates so two models flagging the same bug count once. Robustness lever: different
    models catch different bugs, so the union has higher recall than any single critic. Deliberately
    under-dedupes (only collapses near-identical explanations) so a real-but-differently-worded finding
    is never dropped. verdict = changes_requested if any finding survives, else pass. Pure + testable."""
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for c in critiques or []:
        if not isinstance(c, dict):
            continue
        for f in (c.get("findings") or []):
            if not isinstance(f, dict):
                continue
            exp = str(f.get("explanation") or "").strip()
            key = exp.lower()[:80]                  # near-dup key: same opening of the explanation
            if not exp or key in seen:
                continue
            seen.add(key)
            merged.append(f)
            if len(merged) >= max_findings:
                break
        if len(merged) >= max_findings:
            break
    verdict = "changes_requested" if merged else "pass"
    return {"verdict": verdict, "findings": merged,
            "summary": f"union of {len([c for c in (critiques or []) if isinstance(c, dict)])} "
                       f"decorrelated critics: {len(merged)} distinct findings"}


# Stop reasons for the shared convergence loop.
CONVERGED = "converged"      # a re-critique came back clean (verdict != changes_requested)
STALLED = "stalled"          # a round failed to shrink the finding count -> stuck, stop
REJECTED = "rejected"        # the improvement was rejected/reverted (think-hard regression / overnight reverify-red)
RECRITIQUE_UNAVAILABLE = "recritique_unavailable"  # couldn't re-critique -> accept what we have and stop
BUDGET = "budget"            # ran out of pass/time budget
MAX_ROUNDS = "max_rounds"    # hit the caller's hard round ceiling


def converge(*, initial_findings, can_continue, improve, recritique,
             max_rounds=None, stall_stops=True, stall_window=1, on_round=lambda rec: None) -> dict[str, Any]:
    """The SHARED critique -> improve -> re-critique convergence loop used by BOTH the interactive
    think-hard engine and the overnight adversarial ladder, so the ladder is a THIN layer over the same
    engine rather than a private fork. Pure control logic — every model/verify/budget call is INJECTED,
    so it is unit-testable with no live model. The caller runs the FIRST critique itself and only enters
    this loop when that verdict is `changes_requested`; this then drives the findings down while they
    strictly drop, stopping EARLY on converge / stall / rejection / budget (the "get it down to one bug"
    behavior common to think-hard's refinement loop and adversary.run_ladder's fix loop).

    Injected:
      - can_continue() -> bool: budget/pass gate, checked before each round (EffortState.can_start_pass
        for think-hard; the remaining-time gate for the ladder).
      - improve(prev_findings: int) -> dict: perform ONE improvement (text refinement, or a test-anchored
        fix + deterministic re-verify). MUST return a dict with "accepted": bool. accepted=False means the
        improvement was rejected/reverted (a think-hard regression or an overnight reverify-red) and the
        loop stops with REJECTED (carry an optional "reason"/extra keys through to the round record).
      - recritique() -> dict|None: a fresh critique (parse_critique/merge output) after an ACCEPTED
        improvement. Return None if it cannot run (e.g. budget spent) -> accept what we have and stop
        with RECRITIQUE_UNAVAILABLE.
      - max_rounds: optional hard ceiling on rounds (the ladder's cap); None = only budget/converge/stall bound it.
      - stall_stops: stop when the finding count fails to STRICTLY drop (both callers use this).
      - stall_window: PATIENCE — how many CONSECUTIVE non-improving rounds to tolerate before STALLED
        (default 1 = the original "stall on the first round that fails to strictly drop"; the overnight
        ladder passes a larger value so a single noisy re-critique — a stochastic finding-count blip that
        jumps back up — doesn't bail an otherwise on-track run). The best (fewest-findings) round is always
        remembered (best_findings/best_round) so the caller can keep THAT result rather than a later blip.
      - on_round(rec): per-round callback for live progress.

    Returns {"rounds": [...], "stop_reason": <one of the constants above>, "final_findings": int,
             "best_findings": int, "best_round": int, "converged": bool, "rounds_run": int}.
    """
    prev_n = len(initial_findings or [])
    best_n = prev_n            # fewest findings seen so far (keep-best)
    best_round = 0             # round that achieved best_n (0 = the caller's own first critique)
    no_improve_streak = 0      # consecutive rounds that failed to beat best_n (the patience counter)
    rounds: list[dict[str, Any]] = []
    reason = None
    r = 0
    while True:
        if max_rounds is not None and r >= int(max_rounds):
            reason = MAX_ROUNDS
            break
        if not can_continue():
            reason = BUDGET
            break
        r += 1
        rec: dict[str, Any] = {"round": r, "findings": prev_n}
        outcome = improve(prev_n) or {}
        for k, v in outcome.items():
            if k != "accepted":
                rec[k] = v
        if not outcome.get("accepted"):
            rec["accepted"] = False
            rounds.append(rec)
            on_round(rec)
            reason = outcome.get("reason") or REJECTED
            break
        rec["accepted"] = True
        recrit = recritique()
        if recrit is None:
            rounds.append(rec)
            on_round(rec)
            reason = RECRITIQUE_UNAVAILABLE
            break
        new_n = len(recrit.get("findings") or [])
        rec["findings_after"] = new_n
        rec["verdict"] = recrit.get("verdict")
        rounds.append(rec)
        on_round(rec)
        if recrit.get("verdict") != "changes_requested":
            if new_n < best_n:
                best_n, best_round = new_n, r
            reason = CONVERGED
            break
        # PATIENCE / keep-best: a round only counts as progress if it beats the FEWEST findings seen so
        # far; stop only after `stall_window` CONSECUTIVE non-improving rounds. This tolerates a single
        # noisy re-critique (a count blip that jumps back up) that would otherwise bail an on-track run,
        # and remembers the best round so the caller keeps that result. window=1 is byte-identical to the
        # original "stall on the first non-strictly-dropping round" (prev_n == best_n at every decision
        # point in that mode, so `new_n < best_n` ⟺ `not (new_n >= prev_n)`).
        if new_n < best_n:
            best_n, best_round = new_n, r
            no_improve_streak = 0
        else:
            no_improve_streak += 1
        if stall_stops and no_improve_streak >= max(1, int(stall_window)):
            reason = STALLED
            break
        prev_n = new_n
    return {"rounds": rounds, "stop_reason": reason,
            "final_findings": best_n if reason == STALLED else prev_n,
            "best_findings": best_n, "best_round": best_round,
            "converged": reason == CONVERGED, "rounds_run": r}


@dataclass
class EffortState:
    mode: str
    budget_seconds: int
    max_passes: int
    started_monotonic: float = field(default_factory=time.monotonic)
    passes: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if self.mode not in EFFORT_MODES:
            raise DeliberationError(f"unknown effort mode: {self.mode}")
        # Absurd is an async max-budget run, so it gets a far larger ceiling; every other mode keeps the
        # ~30-min interactive cap. The TIME budget is the real bound and the convergence loop stops early
        # on converge/stall, so these clamps just cap a pathological non-terminating loop.
        if self.mode == "absurd":
            self.budget_seconds = max(60, min(int(self.budget_seconds), ABSURD_MAX_BUDGET_SECONDS))
            self.max_passes = max(1, min(int(self.max_passes), ABSURD_MAX_PASSES))
        else:
            self.budget_seconds = max(60, min(int(self.budget_seconds), 1_800))
            self.max_passes = max(1, min(int(self.max_passes), 20))

    @property
    def elapsed_seconds(self) -> int:
        return round(time.monotonic() - self.started_monotonic)

    @property
    def remaining_seconds(self) -> int:
        return max(0, self.budget_seconds - self.elapsed_seconds)

    def can_start_pass(self) -> bool:
        return len(self.passes) < self.max_passes and self.remaining_seconds > 0

    def record(self, phase: str, summary: str, **evidence: Any) -> None:
        if len(self.passes) >= self.max_passes:
            raise DeliberationError("pass budget exhausted")
        self.passes.append({
            "pass": len(self.passes) + 1,
            "phase": phase,
            "summary": _clip(summary, 4_000),
            "evidence": evidence,
            "elapsed_seconds": self.elapsed_seconds,
        })

    def journal(self) -> list[dict[str, Any]]:
        out = []
        for item in self.passes:
            evidence = item.get("evidence") or {}
            entry = {
                "pass": item["pass"],
                "phase": item["phase"],
                "summary": item["summary"],
                "elapsed_seconds": item["elapsed_seconds"],
            }
            # Surface key evidence fields (verdict/findings count) so diagnostics and the
            # work journal can see them — evidence itself stays out of the journal payload.
            if "verdict" in evidence:
                entry["verdict"] = evidence.get("verdict")
            if "findings" in evidence:
                findings = evidence.get("findings")
                entry["findings"] = len(findings) if isinstance(findings, list) else findings
            out.append(entry)
        return out


def build_deep_trace(
    *,
    objective: str,
    state: EffortState,
    final_answer: str,
    constraints: list[str] | None = None,
    external_agent: str | None = None,
    external_use: str | None = None,
    max_chars: int = 12_000,
) -> dict[str, Any]:
    """Build a compact decision/evidence handoff, never raw chain-of-thought."""
    if external_use is not None and external_use not in EXTERNAL_USES:
        raise DeliberationError(f"unknown external use: {external_use}")
    packet = {
        "format": "deep_trace_v1",
        "objective": _clip(objective, 2_000),
        "constraints": [_clip(item, 500) for item in (constraints or [])[:20]],
        "effort": {
            "mode": state.mode,
            "passes_completed": len(state.passes),
            "elapsed_seconds": state.elapsed_seconds,
            "budget_seconds": state.budget_seconds,
        },
        "passes": [
            {
                "phase": item["phase"],
                "summary": _clip(item["summary"], 2_000),
                "evidence": item["evidence"],
            }
            for item in state.passes
        ],
        "final_answer": _clip(final_answer, 4_000),
        "external": {
            "agent": external_agent,
            "use": external_use,
            "execution_authority": "not_granted",
        } if external_agent else None,
    }
    encoded = json.dumps(packet, ensure_ascii=True, separators=(",", ":"))
    if len(encoded) <= max_chars:
        return packet
    packet["passes"] = [
        {
            "phase": item["phase"],
            "summary": _clip(item["summary"], 600),
            "evidence": {},
        }
        for item in state.passes
    ]
    packet["final_answer"] = _clip(final_answer, 1_500)
    packet["compacted"] = True
    encoded = json.dumps(packet, ensure_ascii=True, separators=(",", ":"))
    if len(encoded) > max_chars:
        packet["passes"] = packet["passes"][-2:]
        packet["constraints"] = packet["constraints"][:8]
    return packet


def external_choice(agent: str, requested_use: str | None = None) -> dict[str, Any]:
    normalized_agent = agent.strip().lower()
    if normalized_agent not in {"claude", "codex"}:
        raise DeliberationError("external agent must be claude or codex")
    if requested_use is None:
        return {
            "agent": normalized_agent,
            "choice_required": True,
            "choices": [
                {"id": key, **value}
                for key, value in EXTERNAL_USES.items()
            ],
            "billing": "External billing may apply; nothing is sent until a choice is confirmed.",
        }
    if requested_use not in EXTERNAL_USES:
        raise DeliberationError(f"unknown external use: {requested_use}")
    return {
        "agent": normalized_agent,
        "choice_required": False,
        "use": requested_use,
        "billing": "External billing may apply.",
        "execution_authority": (
            "external_primary"
            if requested_use in {"direct", "interactive"}
            else "review_only"
        ),
    }
