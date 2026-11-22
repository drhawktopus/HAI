"""Phase 6C: the adversarial review ladder.

After the DETERMINISTIC verifier passes (tests green), an item gets at least a FLOOR adversarial
critique -- never zero -- and, time permitting, climbs to bigger LOCAL critics. Findings are
TEST-ANCHORED (red -> green): the author model converts each finding into a failing test then
fixes it, and the deterministic verifier must stay green -- so improvements are VERIFIED, not
implemented on the critic's opinion (no weak-verifier regression). Bounded fix/re-review rounds;
bail-to-human at the cap.

Reuses deliberation.parse_critique / CRITIQUE_SYSTEM (wired by the caller). The model calls
(critic, anchor_and_fix) + the deterministic re-verify are INJECTED, so this control logic is
unit-testable without a live model. Time -> tier selection is conservative and the caller
checkpoints before the ladder, so a bad time estimate is safe (skip a tier you had time for, or a
clean revert) -- never a corrupted half-applied state.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import deliberation
import review_context


# Bound the persisted critic-finding TEXT so a run's durable state stays small but a human reviewing a
# bail_human can see WHAT the critics flagged (before, only per-round COUNTS survived — the escalation
# was un-actionable). Clipped + capped per critique; the full log is initial critique + each re-critique.
_PERSIST_MAX_FINDINGS = 10
_PERSIST_EXPLANATION_CHARS = 500
_PERSIST_ACTION_CHARS = 240


def _clip(text, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "…"


def _summarize_critique(stage: str, critique: dict) -> dict:
    """A compact, size-bounded snapshot of one critique for durable persistence (severity + explanation
    + recommended action per finding), so `findings_log` records the actual review, not just counts."""
    critique = critique or {}
    findings = [f for f in (critique.get("findings") or []) if isinstance(f, dict)]
    return {
        "stage": stage,
        "verdict": critique.get("verdict"),
        "count": len(findings),
        "findings": [
            {
                "severity": str(f.get("severity", "medium")).lower()[:20],
                "explanation": _clip(
                    f.get("explanation") or f.get("description") or f.get("message"),
                    _PERSIST_EXPLANATION_CHARS),
                "recommended_action": _clip(
                    f.get("recommended_action") or f.get("recommendation") or f.get("suggestion"),
                    _PERSIST_ACTION_CHARS),
                **({"source": f.get("source")} if f.get("source") else {}),
                # evidence-anchoring passthrough: keep the critic's claimed severity + quote so a human
                # reading the log sees BOTH what was claimed and that the quote failed verification.
                **({"claimed_severity": f.get("claimed_severity")} if f.get("claimed_severity") else {}),
                **({"unanchored": True} if f.get("unanchored") else {}),
                **({"omission": True} if f.get("omission") else {}),
                **({"evidence": [_clip(q, 200) for q in f["evidence"][:2]]}
                   if isinstance(f.get("evidence"), list) and f.get("evidence") else {}),
            }
            for f in findings[:_PERSIST_MAX_FINDINGS]
        ],
    }


def _norm_ws(text) -> str:
    """Whitespace-normalize for evidence matching: collapse every whitespace run to one space, so a
    quote still matches its source across line breaks / re-indentation. Case is preserved (code)."""
    return " ".join(str(text or "").split())


def _omission_exempt(finding, spec_symbols, spec_corpus) -> bool:
    """Return True if the finding is an omission claim that structurally cannot quote diff evidence.

    The plain anchor downgraded these true findings. Exemption requires the finding text to name a
    symbol that (a) appears in `spec_symbols` and (b) is verifiably absent from `spec_corpus`."""
    if not spec_symbols or spec_corpus is None:
        return False
    quotes = finding.get("evidence")
    if isinstance(quotes, str):
        quotes = [quotes]
    ftext = " ".join(str(finding.get(k) or "") for k in
                     ("explanation", "description", "message", "recommended_action", "violated"))
    ftext += " " + " ".join(q for q in (quotes or []) if isinstance(q, str))
    diff_text = review_context.strip_diff_comments(str(spec_corpus))
    return any(s in ftext and s not in diff_text for s in spec_symbols)


def apply_evidence_anchor(critique, corpus, severities=("critical", "high"), min_chars=12,
                          spec_symbols=None, spec_corpus=None) -> dict:
    """Anchors CRITICAL/HIGH findings to verbatim evidence in `corpus`.

    Findings with missing, short (<min_chars), or fabricated quotes are downgraded
     to 'medium' severity and tagged 'unanchored' (preserving original severity in
     'claimed_severity'). Lower severities pass through unchanged. No-op if corpus is empty.

    Exemption: If `spec_symbols` and `spec_corpus` are provided, findings identifying a
     missing required symbol (present in spec_symbols but absent from spec_corpus) retain
     their severity and are tagged 'omission' instead, as absence cannot be quoted.
     Fabricated omissions (symbol not in spec or present in diff) still trigger downgrade."""
    if not corpus or not severities:
        return critique
    norm_corpus = _norm_ws(corpus)
    changed = False
    out = []
    for f in (critique.get("findings") or []):
        if isinstance(f, dict) and str(f.get("severity", "")).lower() in severities:
            quotes = f.get("evidence")
            if isinstance(quotes, str):
                quotes = [quotes]
            anchored = any(
                len(_norm_ws(q)) >= int(min_chars) and _norm_ws(q) in norm_corpus
                for q in (quotes or []) if isinstance(q, str)
            )
            if not anchored:
                if _omission_exempt(f, spec_symbols, spec_corpus):
                    f = {**f, "omission": True}
                else:
                    f = {**f, "claimed_severity": f.get("severity"), "severity": "medium",
                         "unanchored": True}
                changed = True
        out.append(f)
    if not changed:
        return critique
    return {**critique, "findings": out}


def apply_severity_grounding(critique, severities=("critical", "high")) -> dict:
    """Downgrade CRITICAL/HIGH findings missing a `violated` field to MEDIUM.

    Findings lacking an explicit acceptance criterion or invariant violation are
    tagged `ungrounded: true` with their original severity stored in `claimed_severity`
    to preserve visibility while preventing noise. Other severities pass through unchanged.
    """
    if not severities:
        return critique
    changed = False
    out = []
    for f in (critique.get("findings") or []):
        if isinstance(f, dict) and str(f.get("severity", "")).lower() in severities:
            grounded = isinstance(f.get("violated"), str) and bool(f.get("violated").strip())
            if not grounded:
                f = {**f, "claimed_severity": f.get("severity"), "severity": "medium",
                     "ungrounded": True}
                changed = True
        out.append(f)
    if not changed:
        return critique
    return {**critique, "findings": out}


def _ledger_key(finding) -> str:
    """Near-duplicate key for a finding (same opening of its explanation, casefolded) — the SAME keying
    merge_decorrelated uses to dedupe critics. Used by the #4 dispute ledger so a re-raised, already-
    argued-away finding matches its accepted dispute and gets dropped."""
    if not isinstance(finding, dict):
        return ""
    return str(finding.get("explanation") or "").strip().lower()[:80]


def _apply_ledger(findings, ledger) -> list:
    """Drop findings whose key is in the accepted-dispute `ledger` (they were argued away and a critic
    quorum upheld it) so a fresh re-critique re-raising the same nit doesn't keep the loop churning. Pure."""
    if not ledger:
        return list(findings or [])
    return [f for f in (findings or []) if _ledger_key(f) not in ledger]


def detect_test_tampering(diff: str) -> list:
    """#3: scan a unified baseline->working diff for EXISTING test files that were MODIFIED (lines
    removed), RENAMED, or DELETED — as opposed to newly added. Adding a brand-new test file is fine and
    expected; rewriting/removing an existing test to force a green run is gaming ("keep every existing
    test passing" means keep them, not edit what they assert). Returns a list of
    {file, removed_lines, renamed, deleted} for each offending existing test file. Pure + testable."""
    out = []
    st = {"a": None, "b": None, "new": False, "deleted": False, "renamed": False, "removed": 0}

    def _is_testpath(p) -> bool:
        p = (p or "").lower()
        return p.endswith(".py") and "test" in p

    def _flush():
        if not (_is_testpath(st["a"]) or _is_testpath(st["b"])):
            return
        if st["new"]:
            return   # brand-new test file: allowed
        if st["deleted"] or st["renamed"] or st["removed"] > 0:
            out.append({"file": st["b"] or st["a"], "removed_lines": st["removed"],
                        "renamed": st["renamed"], "deleted": st["deleted"]})

    for line in (diff or "").splitlines():
        if line.startswith("diff --git "):
            _flush()
            st = {"a": None, "b": None, "new": False, "deleted": False, "renamed": False, "removed": 0}
            try:
                rest = line[len("diff --git "):]
                a, b = rest.split(" b/", 1)
                st["a"] = a[2:] if a.startswith("a/") else a
                st["b"] = b
            except Exception:
                pass
        elif line.startswith("new file mode"):
            st["new"] = True
        elif line.startswith("deleted file mode"):
            st["deleted"] = True
        elif line.startswith("rename from "):
            st["renamed"] = True
        elif line.startswith("-") and not line.startswith("---"):
            st["removed"] += 1
    _flush()
    return out


def _max_critical_recurrence(findings_log) -> int:
    """#2: the maximum number of DISTINCT critique stages in which the SAME critical/high finding (by
    near-duplicate key) was raised. A high recurrence means the critics kept re-flagging a serious issue
    the fixer never honestly cleared (it argued the finding away / gamed it) — so the run must not resolve
    as converged even if the count later reached zero. Pure + testable."""
    from collections import defaultdict
    seen = defaultdict(set)
    for entry in (findings_log or []):
        stage = entry.get("stage")
        for f in (entry.get("findings") or []):
            if str(f.get("severity", "")).lower() in ("critical", "high"):
                k = _ledger_key(f)
                if k:
                    seen[k].add(stage)
    return max((len(v) for v in seen.values()), default=0)


# --- ladder verdicts ---
PASS = "pass"               # the critic found nothing actionable
FIXED = "fixed"             # findings were test-anchored, fixed, and re-verified green
BAIL_HUMAN = "bail_human"   # cap reached and the critic is still unhappy (tests are GREEN) -> flag for review
REVERIFY_RED = "reverify_red"  # a fix broke the deterministic verifier -> caller must revert (BAILED)
SKIPPED = "skipped"         # no time even for the floor critique (item already passed the verifier)


@dataclass
class AdversaryTier:
    name: str
    model: str               # primary/back-compat single model (== models[0])
    est_seconds: int
    swap: bool
    models: list = None      # the decorrelated critic POOL run this tier; defaults to [model]

    def __post_init__(self):
        if not self.models:
            self.models = [self.model] if self.model else []
        elif not self.model:
            self.model = self.models[0]


def tiers_from_config(raw) -> list[AdversaryTier]:
    out: list[AdversaryTier] = []
    for t in raw or []:
        try:
            models = [str(m) for m in (t.get("models") or []) if str(m).strip()]
            model = str(t.get("model") or (models[0] if models else ""))
            if not model and not models:
                continue
            out.append(AdversaryTier(str(t["name"]), model,
                                     int(t.get("est_seconds", 300)), bool(t.get("swap", True)),
                                     models=models or None))
        except Exception:
            continue
    return out


def select_adversary(remaining_seconds, tiers, *, fix_reserve, margin, force=False):
    """Pick the RICHEST critic that affords a full critique+fix+reverify+margin; else the FLOOR
    (tiers[0]) if it at least affords a single critique; else None (skip-with-flag -- the item has
    already cleared the deterministic verifier, so no adversarial pass is safe, just lower-quality).
    Conservative by construction: never starts a tier it can't finish within budget."""
    tiers = list(tiers or [])
    if not tiers:
        return None
    full = [t for t in tiers if remaining_seconds >= t.est_seconds + fix_reserve + margin]
    if full:
        return full[-1]
    floor = tiers[0]
    if force or remaining_seconds >= floor.est_seconds + margin:
        return floor
    return None


def critic_timeout(tier, remaining_seconds, base_timeout):
    """Per-CALL httpx timeout (seconds) for this tier's critic, so a slow heavy tier (an offloaded 70b
    that takes minutes just to first-token) isn't killed by the short global chat timeout. At least
    base_timeout (ADVERSARY_TIMEOUT), extended up to the tier's whole-cycle estimate for a big-budget
    tier, but never more than the budget actually left. The ladder only SELECTS a tier it can afford,
    so this stays within budget; it just stops the underlying call from dying early. Pure + testable."""
    want = max(float(base_timeout or 0), float(getattr(tier, "est_seconds", 0) or 0))
    return max(1.0, min(float(remaining_seconds), want))


def run_ladder(*, item, budget, tiers,
               critic: Callable[[AdversaryTier, str], dict],
               anchor_and_fix: Callable[[list, Any], Any],
               reverify: Callable[[], dict],
               get_diff: Callable[[], str],
               fix_reserve: int, margin: int, max_rounds: int = 2, force: bool = False,
               stall_on_no_progress: bool = False, stall_window: int = 1,
               actionable_severities=("critical", "high", "medium"),
               checkpoint: Callable[[], Any] | None = None,
               restore: Callable[[Any], Any] | None = None,
               dispute: Callable[[Any, list], dict] | None = None,
               dispute_severities: tuple = ("low",),
               critic_retries: int = 0, critical_recur_limit: int = 0, tamper_guard: bool = False,
               tamper_allowed: tuple = (),
               materiality_severities: tuple = (),
               evidence_anchor: bool = False,
               evidence_severities: tuple = ("critical", "high"), evidence_min_chars: int = 12,
               anchor_corpus: Callable[[str], str] | None = None,
               spec_symbols: tuple | list | None = None,
               severity_grounding: bool = False,
               escalate_tiers: list | None = None,
               escalate_severities: tuple = ("critical", "high"),
               on_event=lambda phase, detail="": None) -> dict:
    """Run the adversarial ladder for one verified item.

    Args:
        on_event: Callback for live progress updates.

    Returns:
        A trail dict containing verdict, tier info, round history, findings logs,
        best round metadata, and outstanding issues to enable actionable human escalation.

    The loop behavior depends on mode and hooks:

    - Legacy (stall_on_no_progress=False): Bails to human after `max_rounds` fixes.
    - Overnight (stall_on_no_progress=True): Drives toward convergence by continuing
      while finding count drops. Stalls if `stall_window` consecutive non-improving rounds
      occur, or if `max_rounds` ceiling/time limit is hit.
    - Keep-best: If checkpoint/restore hooks are present and bail occurs on a worse-than-best round,
      restores the workspace to the best snapshot (or initial state if all fixes worsened quality).
    - Dispute: If a dispute hook is injected, findings in `dispute_severities` are adjudicated.
      Upheld disputes are removed from the finding count to prevent churn. All upheld
      disputes + rationales are recorded for audit.
    - Evidence Anchor: If enabled, critiques require verbatim evidence quotes from the diff.
      Unanchored high/critical findings are downgraded to medium/advisory to prevent
      hallucinated regressions from blocking convergence."""
    trail: dict[str, Any] = {"verdict": None, "tier": None, "tiers_run": [], "rounds": [],
                             "findings_initial": 0, "reason": None}
    ledger: set = set()          # #4: keys of findings whose dispute a critic quorum upheld
    trail["disputes"] = []
    trail["materiality_severities"] = list(materiality_severities)
    trail["timings"]: dict[str, float] = {}   # stage -> wall-clock seconds (monotonic)

    def _apply_materiality(critique):
        """Materiality gate: only findings whose severity is in `materiality_severities` block
        convergence. Medium/low are still recorded RAW in findings_log, but here they are ADVISORY —
        dropped from the convergence count, and when nothing material remains the verdict becomes 'pass'
        so the loop converges on the MATERIAL findings instead of churning on nitpicks. No-op when empty."""
        if not materiality_severities:
            return critique
        raw = list(critique.get("findings") or [])
        material = [f for f in raw if str(f.get("severity", "")).lower() in materiality_severities]
        if len(material) == len(raw):
            return critique
        return {**critique, "findings": material,
                "verdict": "pass" if not material else critique.get("verdict")}
    tier = select_adversary(budget.remaining_seconds, tiers,
                            fix_reserve=fix_reserve, margin=margin, force=force)
    if tier is None:
        on_event("adversarial", "skipped (no time for floor)")
        trail["verdict"] = SKIPPED
        trail["reason"] = "no_time_for_floor"
        return trail
    trail["tier"] = tier.name
    trail["tiers_run"].append(tier.name)
    on_event("adversarial_tier", f"{tier.name} ({', '.join(tier.models or [tier.model])}) critiquing")

    def _run_dispute(critique, stage):
        """#4: offer dispute-eligible findings (severity in `dispute_severities`) to the injected
        `dispute` hook (author argues each one, a critic quorum adjudicates) and drop the findings a
        quorum UPHELD — this round's plus any ledgered from a prior round — so a wrong or non-material
        finding doesn't churn/stall the loop. Upheld disputes + rationale are recorded in
        trail["disputes"]. Pure control; model calls live in the injected `dispute`. Verdict becomes
        'pass' if nothing actionable remains."""
        t_dis = time.monotonic()
        raw = list(critique.get("findings") or [])
        kept = _apply_ledger(raw, ledger)                       # drop anything already argued away
        low = [f for f in kept if str(f.get("severity", "")).lower() in dispute_severities]
        if dispute is not None and low:
            try:
                res = dispute(tier, low) or {}
            except Exception as exc:
                on_event("adversarial_dispute", f"{stage}: error {exc!r}"[:120])
                res = {}
            upheld = [it for it in (res.get("accepted") or []) if isinstance(it, dict)]
            for it in upheld:
                k = _ledger_key(it.get("finding"))
                if k:
                    ledger.add(k)
            if upheld:
                trail["disputes"].append({"stage": stage, "upheld": [
                    {"severity": str((it.get("finding") or {}).get("severity", "")).lower()[:20],
                     "explanation": _clip((it.get("finding") or {}).get("explanation"), 300),
                     "rationale": _clip(it.get("rationale"), 400)} for it in upheld]})
                on_event("adversarial_dispute",
                         f"{stage}: {len(upheld)}/{len(low)} disputed finding(s) upheld & dropped")
            kept = _apply_ledger(kept, ledger)
        trail["timings"]["dispute"] = trail["timings"].get("dispute", 0.0) + (time.monotonic() - t_dis)
        if len(kept) == len(raw):
            return critique
        return {**critique, "findings": kept,
                "verdict": "pass" if not kept else critique.get("verdict")}

    diff0 = get_diff()
    if tamper_guard:                                   # #3: detect gamed/edited existing tests up front
        tamper = detect_test_tampering(diff0)
        if tamper and tamper_allowed:
            # Authorized edits (per backlog grant) are recorded in the trail but do not flag.
            # Unlisted edits remain flagged. Note: authorized edits are still adversarially
            # reviewed by the caller's critic (e.g., weakening a test is critical).
            allowed_files = {str(a).split("::", 1)[0].strip().lower() for a in tamper_allowed}
            authorized = [t for t in tamper if str(t.get("file", "")).lower() in allowed_files]
            tamper = [t for t in tamper if str(t.get("file", "")).lower() not in allowed_files]
            if authorized:
                trail["authorized_test_edits"] = authorized
                on_event("adversarial_tamper",
                         f"{len(authorized)} authorized existing-test edit(s) (per backlog grant) — reviewed, not flagged")
        if tamper:
            trail["test_tampering"] = tamper
            on_event("adversarial_tamper",
                     f"{len(tamper)} existing test file(s) modified/renamed/deleted -> will flag for human")
    tc = time.monotonic()
    try:
        critique = critic(tier, diff0) or {}
    except Exception as exc:
        on_event("adversarial", f"critic_error: {exc!r}"[:120])
        trail["verdict"] = SKIPPED
        trail["reason"] = f"critic_error: {exc!r}"
        return trail
    trail["timings"]["critique"] = time.monotonic() - tc   # initial critique wall-clock

    def _anchor(c, corpus, stage):
        # Two independent substantiation gates, each downgrading an unsupported critical/high to advisory
        # (medium) BEFORE it is logged/counted, so a phantom can't block convergence or whipsaw the fixer:
        #   EVIDENCE anchor  — the finding must QUOTE the material the critic saw (real code, not fabricated).
        #   SEVERITY grounding — the finding must NAME the acceptance criterion/invariant it violates (a real
        #     rule, not an over-rated nitpick). A critical must pass BOTH.
        # anchor_corpus lets the caller widen the evidence corpus to match a context-enriched critique
        # (diff + review packet) — a legit quote from the packet must not read as fabricated.
        if evidence_anchor:
            corp = corpus
            if anchor_corpus is not None:
                try:
                    corp = anchor_corpus(corpus)
                except Exception:
                    corp = corpus   # fall back to the bare diff — stricter, never unsafe
            # B2: the omission exemption checks absence against the BARE diff (`corpus`), not the
            # widened corp — a call-site line in the review packet must not defeat a true "the diff
            # never wires X" finding by making X look present.
            c = apply_evidence_anchor(c, corp, evidence_severities, evidence_min_chars,
                                      spec_symbols=spec_symbols, spec_corpus=corpus)
            n = sum(1 for f in (c.get("findings") or []) if isinstance(f, dict) and f.get("unanchored"))
            if n:
                on_event("adversarial_anchor",
                         f"{stage}: {n} critical/high finding(s) without verifiable evidence -> advisory")
            o = sum(1 for f in (c.get("findings") or []) if isinstance(f, dict) and f.get("omission"))
            if o:
                on_event("adversarial_anchor",
                         f"{stage}: {o} omission finding(s) kept at claimed severity (spec-grounded absence)")
        if severity_grounding:
            c = apply_severity_grounding(c, evidence_severities)
            m = sum(1 for f in (c.get("findings") or []) if isinstance(f, dict) and f.get("ungrounded"))
            if m:
                on_event("adversarial_ground",
                         f"{stage}: {m} critical/high finding(s) not tied to a requirement -> advisory")
        return c

    critique = _anchor(critique, diff0, "initial")
    raw_findings = list(critique.get("findings") or [])
    trail["findings_initial"] = len(raw_findings)
    trail["findings_log"] = [_summarize_critique("initial", critique)]   # #1: RAW critic text (pre-dispute)
    critique = _run_dispute(critique, "initial")                         # #4: argue disputable findings before counting
    critique = _apply_materiality(critique)                              # materiality: medium/low -> advisory
    findings = list(critique.get("findings") or [])
    dropped = len(raw_findings) - len(findings)
    on_event("adversarial_critique", f"{tier.name}: {critique.get('verdict')} ({len(findings)} findings"
             + (f", {dropped} disputed" if dropped else "") + ")")

    # D1 escalate-on-signal (density): a REDUCED-tier run (low/medium risk) that surfaces a MATERIAL
    # critical/high after anchoring must not be RESOLVED by fewer eyes than a high-risk item would get.
    # If escalate_tiers offers a richer critic pool than the one we selected, upgrade the tier NOW so the
    # fix loop + every re-critique run the fuller pool (the initial critique already did the detection).
    # The closures below (_improve/_recritique/_run_dispute) read `tier` as a free var, so reassigning it
    # here propagates. No-op when escalate_tiers is None/not richer, or nothing material was raised.
    if (escalate_tiers and critique.get("verdict") == "changes_requested"
            and any(str(f.get("severity", "")).lower() in escalate_severities for f in findings)):
        richer = select_adversary(budget.remaining_seconds, escalate_tiers,
                                  fix_reserve=fix_reserve, margin=margin, force=True)
        if richer is not None and len(richer.models or [richer.model]) > len(tier.models or [tier.model]):
            triggers = sorted({str(f.get("severity", "")).lower() for f in findings
                               if str(f.get("severity", "")).lower() in escalate_severities})
            on_event("adversarial_escalate",
                     f"material {'/'.join(triggers)} on '{tier.name}' -> escalating to '{richer.name}' "
                     f"({len(richer.models or [richer.model])} critics) for fix + re-critique")
            trail["escalated"] = {"from": tier.name, "to": richer.name, "trigger": triggers}
            trail["tiers_run"].append(richer.name)
            tier = richer

    if critique.get("verdict") != "changes_requested":
        # #3: even a clean pass must flag for a human if the diff gamed an existing test to get there.
        if tamper_guard and trail.get("test_tampering"):
            trail["verdict"] = BAIL_HUMAN
            trail["reason"] = "test_tampering_flag_human"
            on_event("adversarial_tamper", "existing test(s) modified -> flag for human (not converged)")
        else:
            trail["verdict"] = PASS
        return trail

    # The fix -> re-verify -> re-critique loop is now the SHARED deliberation engine
    # (deliberation.converge) -- the ladder is a THIN layer over the SAME loop think-hard uses. adversary
    # injects its test-anchored fix + deterministic re-verify and keeps only its extras (tier selection
    # above, the actionable-severity filter, and mapping the engine's stop-reason onto the ladder verdicts).
    holder = {"findings": findings, "recritique_error": None, "log": trail["findings_log"], "round": 0}

    # keep-best RESTORE plumbing (#2): snapshot the workspace after each accepted round so a later
    # stall/bail on a worse round can hard-reset back to the BEST round's state (not just report it).
    # round_checkpoints[0] is the pre-adversarial VERIFIED state — the right fallback if every adversarial
    # "fix" only made things worse. No-op when the caller injects no checkpoint hook (e.g. unit tests).
    round_checkpoints: dict[int, Any] = {}
    if checkpoint is not None:
        try:
            round_checkpoints[0] = checkpoint()
        except Exception as exc:
            on_event("adversarial_checkpoint", f"round 0 snapshot failed: {exc!r}"[:120])

    def _can_continue():
        # Same "no time to fix" gate as before, checked before each round (so a fix never starts
        # without time to finish it).
        return budget.remaining_seconds > fix_reserve + margin

    def _improve(_prev_n):
        t_fix = time.monotonic()
        holder["round"] += 1
        cur = holder["findings"]
        actionable = [f for f in cur
                      if str(f.get("severity", "")).lower() in actionable_severities] or cur
        rec: dict[str, Any] = {"tier": tier.name, "actionable": len(actionable)}
        on_event("adversarial_fix", f"test-anchoring + fixing {len(actionable)} finding(s)")
        try:
            rec["fix"] = anchor_and_fix(actionable, budget)
        except Exception as exc:
            rec.update({"accepted": False, "reason": "fix_error", "fix_error": repr(exc)})
            trail["timings"]["fix"] = trail["timings"].get("fix", 0.0) + (time.monotonic() - t_fix)
            return rec
        v = reverify() or {}
        passed = bool(v.get("passed"))
        rec["reverify_passed"] = passed
        on_event("adversarial_reverify", f"passed={passed}")
        rec["accepted"] = passed
        if not passed:
            rec["reason"] = "fix_broke_verifier"   # caller reverts -> BAILED
        elif checkpoint is not None:
            try:                                    # snapshot this accepted round for keep-best restore
                cid = checkpoint()
                if cid is not None:
                    round_checkpoints[holder["round"]] = cid
            except Exception as exc:
                on_event("adversarial_checkpoint", f"round {holder['round']} snapshot failed: {exc!r}"[:120])
        trail["timings"]["fix"] = trail["timings"].get("fix", 0.0) + (time.monotonic() - t_fix)
        return rec

    def _recritique():
        # #4 critic-pool reliability: retry a fully-failed critic pool before giving up. Giving up
        # (return None) is NO LONGER treated as a pass downstream — it flags for a human.
        t_rec = time.monotonic()
        c = None
        cur_diff = ""
        for attempt in range(1 + max(0, int(critic_retries))):
            try:
                cur_diff = get_diff()
                c = critic(tier, cur_diff) or {}
                holder["recritique_error"] = None
                break
            except Exception as exc:
                holder["recritique_error"] = repr(exc)
                c = None
                if attempt < int(critic_retries):
                    on_event("adversarial_recritique",
                             f"critic pool failed; retry {attempt + 1}/{int(critic_retries)}")
        if c is None:
            trail["timings"]["recritique"] = trail["timings"].get("recritique", 0.0) + (time.monotonic() - t_rec)
            return None
        stage = f"recritique_{len(holder['log'])}"
        c = _anchor(c, cur_diff, stage)                       # evidence gate against the diff the critic saw
        holder["log"].append(_summarize_critique(stage, c))   # #1: RAW critic text (pre-dispute)
        c = _run_dispute(c, stage)                            # #4: argue disputable findings -> filtered count
        c = _apply_materiality(c)                             # materiality: medium/low -> advisory
        holder["findings"] = list(c.get("findings") or [])
        trail["timings"]["recritique"] = trail["timings"].get("recritique", 0.0) + (time.monotonic() - t_rec)
        return c

    result = deliberation.converge(
        initial_findings=findings, can_continue=_can_continue,
        improve=_improve, recritique=_recritique,
        max_rounds=max(1, int(max_rounds)), stall_stops=bool(stall_on_no_progress),
        stall_window=max(1, int(stall_window)),
    )
    trail["rounds"] = result["rounds"]
    trail["best_round"] = result.get("best_round")        # #2 keep-best: round with the fewest findings
    trail["best_findings"] = result.get("best_findings")
    trail["findings_outstanding"] = holder["log"][-1]["findings"] if holder["log"] else []   # #1: still-open text
    reason = result["stop_reason"]
    if reason == deliberation.CONVERGED:
        trail["verdict"] = FIXED
    elif reason == deliberation.RECRITIQUE_UNAVAILABLE:
        # #1/#4: a critic-pool failure is NOT a pass. Tests are green but we could not COMPLETE the
        # adversarial review (every pool critic failed, even after retries) -> flag for a human and
        # restore the best known state; never silently accept (that false-passed a gamed solution).
        trail["verdict"] = BAIL_HUMAN
        trail["reason"] = f"recritique_failed: {holder['recritique_error'] or 'unavailable'}"
    elif reason == "fix_broke_verifier":
        trail["verdict"] = REVERIFY_RED
        trail["reason"] = "fix_broke_verifier"
        # Restore is unconditional here (unlike the stall path) because the workspace
        # holds the broken fix. If restore fails or hooks are absent, fall back to
        # REVERIFY_RED so the caller can revert.
        if restore is not None:
            br = result.get("best_round")
            cid_ = round_checkpoints.get(br) if br is not None else None
            if cid_ is None and 0 in round_checkpoints:
                br, cid_ = 0, round_checkpoints[0]
            if cid_ is not None:
                try:
                    restore(cid_)
                    trail["verdict"] = BAIL_HUMAN
                    trail["reason"] = "fix_broke_verifier_kept_green"
                    trail["restored_to_best"] = {"best_round": br,
                                                 "best_findings": result.get("best_findings"),
                                                 "checkpoint": cid_}
                    on_event("adversarial_restore",
                             f"fix broke the verifier; kept green round {br} + flagged for human")
                except Exception as exc:
                    trail["restore_error"] = repr(exc)
                    on_event("adversarial_restore", f"keep-green restore failed: {exc!r}"[:120])
    elif reason == "fix_error":
        trail["verdict"] = BAIL_HUMAN
        trail["reason"] = "fix_error"
    elif reason == deliberation.STALLED:
        trail["verdict"] = BAIL_HUMAN
        trail["reason"] = "stalled_no_progress"
        # Report the BEST count reached (keep-best), not the noisy round we happened to bail on.
        trail["stalled_at"] = result.get("best_findings", result["final_findings"])
        on_event("adversarial_stall",
                 f"no progress; best {trail['stalled_at']} at round {result.get('best_round')}")
    elif reason == deliberation.BUDGET:
        trail["verdict"] = BAIL_HUMAN
        trail["reason"] = "out_of_time"
    elif reason == deliberation.MAX_ROUNDS:
        trail["verdict"] = BAIL_HUMAN            # ran all rounds, critic still unhappy (tests green)
        trail["reason"] = "still_changes_requested_at_cap"
    else:
        trail["verdict"] = BAIL_HUMAN
        trail["reason"] = reason or "unresolved"

    # #3 test-tampering guard: rewriting/renaming/deleting an EXISTING test to force a green run is
    # gaming ("keep every existing test passing" means KEEP them) -> never resolve as converged.
    if tamper_guard and trail.get("test_tampering") and trail["verdict"] in (PASS, FIXED):
        trail["verdict"] = BAIL_HUMAN
        trail["reason"] = "test_tampering_flag_human"
        on_event("adversarial_tamper", "existing test(s) modified -> flag for human (not converged)")

    # #2 anti-rationalization: if the critics re-raised the SAME critical/high finding across many
    # separate critiques, the fixer never honestly cleared it (it likely argued the finding away). Don't
    # let such a run resolve as converged even if the count eventually reached zero.
    recur = _max_critical_recurrence(trail.get("findings_log"))
    trail["max_critical_recurrence"] = recur
    if critical_recur_limit and recur >= int(critical_recur_limit) and trail["verdict"] in (PASS, FIXED):
        trail["verdict"] = BAIL_HUMAN
        trail["reason"] = "recurring_critical_flag_human"
        on_event("adversarial_recur",
                 f"a critical/high finding recurred across {recur} critiques -> flag for human")

    # keep-best RESTORE (#2): on a green-but-not-converged bail, hard-reset the workspace to the BEST
    # round's snapshot when we didn't stop on the best round -> a noisy late round isn't what's left on
    # disk. Skipped without a restore hook, or when the current state already IS the best round. Also
    # runs on a critic-pool failure bail (RECRITIQUE_UNAVAILABLE) so we leave the best known state.
    if restore is not None and reason in (deliberation.STALLED, deliberation.BUDGET,
                                          deliberation.MAX_ROUNDS, deliberation.RECRITIQUE_UNAVAILABLE):
        br = result.get("best_round")
        if br is not None and br != result.get("rounds_run"):
            cid = round_checkpoints.get(br)
            if cid is None and 0 in round_checkpoints:   # best round wasn't snapshotted -> clean fallback
                br, cid = 0, round_checkpoints[0]
            if cid is not None:
                try:
                    restore(cid)
                    trail["restored_to_best"] = {"best_round": br,
                                                 "best_findings": result.get("best_findings"),
                                                 "checkpoint": cid}
                    on_event("adversarial_restore",
                             f"restored best round {br} ({result.get('best_findings')} findings)")
                except Exception as exc:
                    trail["restore_error"] = repr(exc)
                    on_event("adversarial_restore", f"restore failed: {exc!r}"[:120])

    on_event("adversarial_done", f"{trail['verdict']} ({trail.get('reason') or ''})")
    return trail
