"""Phase 8: the decomposition (self-chunking) phase.

A `commission` is one objective too big to be a single test-verifiable leaf. This module runs a
PLANNING pass -- think-hard whose OUTPUT is a task list instead of a diff -- that turns one
commission into an ordered set of test-verifiable sub-tasks ("leaves"), each with its own
acceptance, plus a final INTEGRATION leaf that proves the chunks actually compose (per-leaf unit
tests cannot).

It is the unify point with deliberation.py: the plan is DRAFTED, independently CRITIQUED, and
REFINED, reusing deliberation.parse_critique / merge_critiques / CRITIQUE_SYSTEM. Two extra critique
sources fold into the same refine step:
  * STRUCTURAL validation (deterministic: every leaf test-verifiable + has acceptance, depends_on
    references resolve, no dependency cycle, exactly one integration leaf) -- authoritative, so a
    structural problem always forces another round.
  * HUMAN FEEDBACK on the plan (feedback_as_critique) -- top-priority notes the planner must
    address, so the plan is revisable the same way an interactive think-hard plan is.

The model calls (propose, critique) are INJECTED, so the control logic -- draft/critique/refine
loop, structural validation, topological ordering -- is unit-testable without a live model. The
real deps wire to the orchestrator's heavy model + the adversary host. NOTHING here executes a
leaf or promotes a plan: emit_plan only PROPOSES leaves (into proposals), and promotion to the
human-only backlog is a separate, human-gated step (the locked human-only-approval invariant).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import deliberation
from deliberation import DeliberationError, parse_critique, merge_critiques


MAX_LEAVES = 20


class PlanError(RuntimeError):
    """A plan that cannot be parsed or is structurally impossible (e.g. a dependency cycle)."""


PLAN_SYSTEM = (
    "You are a senior engineer DECOMPOSING one large task ('commission') into the smallest set of "
    "INDEPENDENTLY test-verifiable sub-tasks ('leaves') that, executed in order, accomplish it. "
    "You are NOT writing code -- you are writing the plan. Hard rules: (1) every leaf must be small "
    "enough to implement and prove with its OWN unit test, and must state that test as its "
    "'acceptance'; (2) order leaves with 'depends_on' so each one can land while the whole existing "
    "test suite stays green; (3) include EXACTLY ONE final leaf with \"integration\": true whose "
    "acceptance is an integration test proving the leaves COMPOSE into the commission's goal -- it must "
    "depend on the others; (4) WIRING IS PART OF THE WORK: any leaf that adds a production-facing "
    "function, method, or endpoint must ALSO wire it into its real production call site in the SAME "
    "leaf's objective+acceptance (a helper nothing calls is an incomplete leaf, even if its unit "
    "test passes); (5) the integration leaf's acceptance must exercise the REAL end-to-end "
    "production call path -- the actual entrypoints a user/caller hits -- and assert the CROSS-LEAF "
    "WIRING, never component-level access or mocks of sibling leaves (a mocked seam is exactly "
    "where composition bugs hide); (6) SIZE CALIBRATION -- every leaf pays a FIXED review cost "
    "regardless of how small it is, so a plan of many trivial leaves is far slower than a few "
    "substantial ones. MERGE any leaf that would be a lone tiny helper or under roughly 20 changed "
    "lines into the sibling it most belongs with; each leaf should carry a meaningful, independently-"
    "testable change. Aim for the FEWEST leaves that still keep each one independently verifiable and "
    "the suite green between them -- a handful of substantial leaves, never a long list of trivial "
    "ones; (7) THE MAP TRAVELS WITH THE LEAF: every non-integration leaf must carry 'evidence' "
    "-- the verbatim code snippets and file:line anchors its implementer needs, gathered from "
    "your planning reads -- so a fresh implementer context spends its budget WRITING, never "
    "re-reading (a leaf whose implementer must re-map the codebase is an incomplete leaf); "
    "(8) BUDGET: each leaf's objective must be achievable in at most ~25 tool calls -- if the "
    "evidence you can gather does not make that plausible, split or shrink the leaf. "
    "Return JSON: {\"leaves\": [{\"id\", \"title\", \"objective\", \"acceptance\", "
    "\"evidence\", \"depends_on\": [ids], \"integration\": false}], \"notes\"}. Use short "
    "stable ids like \"l1\",\"l2\"."
)


# The plan gate's port of deliberation.py's fixer-dispute pair: a critic finding must survive the
# planner's evidence-quoting rebuttal and a skeptical adjudication, or it drops. Uncertain = the
# finding stands.
PLAN_DISPUTE_AUTHOR_SYSTEM = (
    "You are the PLANNER whose decomposition plan is under adversarial review. You are shown the "
    "plan (its leaves, each carrying its evidence), the existing-code grounding, and a list of "
    "reviewer findings. For EACH finding decide 'fix' or 'dispute'. Choose 'dispute' ONLY when the "
    "finding is genuinely non-material, already handled by the plan, or factually wrong -- and give "
    "a CONCRETE, specific rationale grounded in the plan or the code shown (quote the exact leaf "
    "text, evidence snippet, or code lines that disprove it; never 'it is fine' or 'this is "
    "minor'). For a finding that claims a wrong file:line anchor or a misread of the code "
    "structure, quote the anchor's actual content. A dispute with a vague or hand-wavy rationale "
    "will be rejected, so when in doubt choose 'fix'. Never dispute something that reflects a real "
    "improvement, however small. Return JSON: an array with one object per finding IN THE ORDER "
    'GIVEN, each {"index": <int>, "action": "fix"|"dispute", "rationale": <string>}.'
)

PLAN_DISPUTE_ADJUDICATE_SYSTEM = (
    "You are an INDEPENDENT reviewer adjudicating a DISPUTE over one finding on a decomposition "
    "plan. You are given the plan (leaves + evidence), the existing-code grounding, the finding, "
    "and the planner's rationale for disputing it. Decide whether the dispute is VALID -- i.e. the "
    "finding really is non-material, already handled, or incorrect. VERIFY the rationale against "
    "the plan and code yourself: check that the lines and anchors the planner cites actually exist "
    "and say what is claimed. Be skeptical: uphold the dispute ONLY if the rationale is concrete, "
    "specific, and correct against the material shown; reject vague, generic, or hand-wavy "
    "rationales, and reject any dispute of something that is a genuine improvement. For a HIGH or "
    "CRITICAL finding, uphold ONLY if the quoted material itself DEMONSTRATES the finding is "
    "false; otherwise reject. When uncertain, REJECT (the finding then simply stands). Return "
    'JSON: {"valid": true|false, "reason": <string>}.'
)


@dataclass
class Leaf:
    id: str
    title: str
    objective: str
    acceptance: str
    evidence: str = ""
    depends_on: list[str] = field(default_factory=list)
    integration: bool = False
    kind: str = "test-verifiable"

    def as_backlog_item(self, *, parent_id: str, workspace: str) -> dict:
        """Render as a backlog-shaped item (the shape a human promotes into backlog.yaml)."""
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "domain": "orchestrator",
            "workspace": workspace,
            "objective": self.objective,
            "acceptance": self.acceptance,
            "evidence": self.evidence,
            "parent_id": parent_id,
            "depends_on": list(self.depends_on),
            "integration": self.integration,
            "approved_overnight": False,   # human-only; never pre-approved by the planner
        }


def _clip(text, limit):
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit]


def parse_plan(content: str) -> list[Leaf]:
    """Parse the planner's JSON into normalized leaves. Raises PlanError on unusable structure.
    Uses the SHARED robust extractor (deliberation.extract_json) so the planner's <think>/prose
    wrapping is tolerated identically to the critique parser -- one source of truth."""
    try:
        value = deliberation.extract_json(content)
    except ValueError as exc:
        raise PlanError("planner returned invalid JSON") from exc
    raw = value.get("leaves") if isinstance(value, dict) else value
    if not isinstance(raw, list) or not raw:
        raise PlanError("plan has no leaves")
    leaves: list[Leaf] = []
    seen: set[str] = set()
    for i, item in enumerate(raw[:MAX_LEAVES]):
        if not isinstance(item, dict):
            continue
        lid = _clip(item.get("id") or f"l{i + 1}", 40) or f"l{i + 1}"
        if lid in seen:                       # de-dupe ids deterministically
            lid = f"{lid}-{i + 1}"
        seen.add(lid)
        deps = item.get("depends_on") or []
        deps = [_clip(d, 40) for d in deps if str(d or "").strip()] if isinstance(deps, list) else []
        leaves.append(Leaf(
            id=lid,
            title=_clip(item.get("title") or lid, 200),
            objective=_clip(item.get("objective") or item.get("description"), 2_000),
            acceptance=_clip(item.get("acceptance") or item.get("test"), 2_000),
            evidence=_clip(item.get("evidence"), 6_000),
            depends_on=deps,
            integration=bool(item.get("integration")),
        ))
    if not leaves:
        raise PlanError("plan had no usable leaf objects")
    return leaves


def validate_plan(leaves: list[Leaf], strict_evidence: bool = False) -> list[dict]:
    """DETERMINISTIC structural review -> a list of critique-shaped findings (empty = clean).

    These are authoritative: any finding here means the plan is not yet executable, so emit_plan
    forces another refine round regardless of what the model critic thought.

    strict_evidence: the decomposed DRIVER requires every implementation leaf to carry its
    evidence (a fresh leaf context cannot re-survey); the interactive planning flow keeps its
    original contract, so the requirement is opt-in rather than a behavior change there."""
    findings: list[dict] = []
    ids = {lf.id for lf in leaves}

    def bad(sev, explanation, action=""):
        findings.append({"severity": sev, "explanation": explanation,
                         "recommended_action": action, "source": "structural"})

    for lf in leaves:
        if not lf.objective:
            bad("critical", f"leaf {lf.id} has no objective.", "Give it a concrete objective.")
        if not lf.acceptance:
            bad("critical", f"leaf {lf.id} ('{lf.title}') has no test-verifiable acceptance.",
                "State the unit test that proves it done.")
        if strict_evidence and not lf.integration and not lf.evidence.strip():
            bad("high", f"leaf {lf.id} ('{lf.title}') carries no evidence - its implementer "
                "would have to re-map the codebase, which is the failure this field exists "
                "to prevent.",
                "Add the verbatim snippets and file:line anchors the implementer needs.")
        for d in lf.depends_on:
            if d not in ids:
                bad("high", f"leaf {lf.id} depends_on '{d}', which is not a leaf in the plan.",
                    "Fix the dependency id or add the missing leaf.")
            if d == lf.id:
                bad("high", f"leaf {lf.id} depends on itself.", "Remove the self-dependency.")

    integ = [lf for lf in leaves if lf.integration]
    if len(integ) == 0:
        bad("critical", "the plan has no integration leaf.",
            "Add exactly one leaf with integration:true whose acceptance is an integration test "
            "proving the leaves compose.")
    elif len(integ) > 1:
        bad("high", f"the plan has {len(integ)} integration leaves; there must be exactly one.",
            "Keep a single integration leaf.")
    else:
        if not integ[0].depends_on:
            bad("high", "the integration leaf depends on nothing, so it cannot prove composition.",
                "Make the integration leaf depend on the leaves it integrates.")

    # cycle detection is authoritative: an un-orderable plan is not executable.
    try:
        topo_order(leaves)
    except PlanError as exc:
        bad("critical", str(exc), "Break the dependency cycle so the leaves can be ordered.")
    return findings


def topo_order(leaves: list[Leaf]) -> list[Leaf]:
    """Kahn topological sort over depends_on (deps must run first). Raises PlanError on a cycle.
    Unknown dep ids are ignored here (validate_plan reports them); ordering stays deterministic by
    preserving the planner's original order among ready nodes."""
    by_id = {lf.id: lf for lf in leaves}
    order_index = {lf.id: i for i, lf in enumerate(leaves)}
    indeg = {lf.id: 0 for lf in leaves}
    for lf in leaves:
        for d in lf.depends_on:
            if d in by_id and d != lf.id:
                indeg[lf.id] += 1
    ready = sorted([lid for lid, n in indeg.items() if n == 0], key=lambda x: order_index[x])
    out: list[Leaf] = []
    while ready:
        cur = ready.pop(0)
        out.append(by_id[cur])
        for lf in leaves:
            if cur in lf.depends_on and lf.id != cur:
                indeg[lf.id] -= 1
                if indeg[lf.id] == 0:
                    ready.append(lf.id)
        ready.sort(key=lambda x: order_index[x])
    if len(out) != len(leaves):
        stuck = sorted(set(by_id) - {lf.id for lf in out})
        raise PlanError(f"dependency cycle among leaves: {', '.join(stuck)}")
    return out


def feedback_as_critique(notes) -> dict | None:
    """Turn human plan feedback into a top-priority critique so the SAME refine step folds it in.
    `notes` is a string or list of strings; empty -> None (no feedback this round)."""
    if not notes:
        return None
    if isinstance(notes, str):
        notes = [n.strip() for n in notes.splitlines() if n.strip()]
    findings = [{"severity": "high", "explanation": _clip(n, 2_000),
                 "recommended_action": "Revise the plan to address this.", "source": "human"}
                for n in notes if str(n).strip()]
    if not findings:
        return None
    return {"verdict": "changes_requested", "summary": "human feedback on the plan", "findings": findings}


def _structural_critique(findings: list[dict]) -> dict:
    if not findings:
        return {"verdict": "pass", "summary": "structurally valid", "findings": []}
    return {"verdict": "changes_requested",
            "summary": f"{len(findings)} structural problem(s)", "findings": findings}


def emit_plan(*, commission: dict,
              propose: Callable[[dict, list[Leaf] | None, dict | None], str],
              critique: Callable[[list[Leaf]], str],
              feedback=None, max_rounds: int = 3,
              on_event: Callable[[str, str], None] = lambda phase, detail="": None,
              strict_evidence: bool = False,
              advisory_pass: bool = False,
              dispute: Callable[[list[Leaf], list[dict]], list[dict]] | None = None) -> dict:
    """Draft -> critique -> refine a decomposition for one commission. Returns a trail:
       {leaves: [item...], ordered_ids, rounds: [...], verdict, structural_clean}.

    propose(commission, prior_leaves, combined_critique) -> JSON plan text (round 1: prior/critique None).
    critique(leaves) -> JSON critique text (reviewed with CRITIQUE_SYSTEM by the caller's model).
    A plan is ACCEPTED only when the model critic passes AND structural validation is clean AND any
    human feedback has been folded in. Otherwise it refines up to max_rounds, then returns the best
    plan so far flagged needs_review so a human still sees it.

    strict_evidence / advisory_pass / dispute are the decomposed DRIVER's opt-ins; the interactive
    flow keeps its original contract (any finding blocks; no dispute exchange).
    - advisory_pass: only CRITICAL-severity model findings (plus every structural and human
      finding) block acceptance; sub-critical survivors ride out on trail["advisory_findings"]
      as warnings instead of forcing another round.
    - dispute(leaves, findings) -> the findings that SURVIVE one author-rebuttal + adjudication
      exchange over the model critic's findings. Fail-soft: an error leaves them unchanged."""
    workspace = commission.get("workspace") or "orchestrator-real"
    parent_id = commission.get("id") or "commission"
    fb = feedback_as_critique(feedback)
    trail: dict[str, Any] = {"leaves": [], "ordered_ids": [], "rounds": [],
                             "verdict": None, "structural_clean": False}
    prior: list[Leaf] | None = None
    combined: dict | None = fb            # round 1 already carries any standing human feedback

    for rnd in range(1, max(1, int(max_rounds)) + 1):
        on_event("plan_draft", f"round {rnd}: drafting decomposition")
        try:
            draft = propose(commission, prior, combined)
            leaves = parse_plan(draft)
        except PlanError as exc:
            on_event("plan_error", f"round {rnd}: {exc}"[:120])
            snippet = str(locals().get("draft") or "")[:200].replace("\n", " ").strip()
            trail["rounds"].append({"round": rnd, "error": str(exc), "raw": snippet})
            if prior is None:
                continue                  # give the planner another shot at producing valid JSON
            leaves = prior               # fall back to the last good plan, then critique it

        structural = validate_plan(leaves, strict_evidence=strict_evidence)
        try:
            model_review = parse_critique(critique(leaves))
        except DeliberationError as exc:
            model_review = {"verdict": "pass", "summary": f"critic unavailable: {exc}", "findings": []}

        dropped = 0
        m_findings = list(model_review.get("findings") or [])
        if dispute is not None and m_findings:
            try:
                survived = dispute(leaves, m_findings)
            except Exception as exc:
                on_event("plan_dispute_error", str(exc)[:120])
                survived = m_findings
            if isinstance(survived, list) and len(survived) < len(m_findings):
                dropped = len(m_findings) - len(survived)
                model_review = dict(model_review)
                model_review["findings"] = survived
                model_review["verdict"] = "changes_requested" if survived else "pass"
                on_event("plan_dispute", f"round {rnd}: {dropped} finding(s) refuted and dropped")

        combined = merge_critiques(model_review, _structural_critique(structural), "structural")
        if fb is not None:
            combined = merge_critiques(combined, fb, "human")

        all_findings = combined.get("findings") or []
        advisory: list[dict] = []
        if advisory_pass:
            blocking = [f for f in all_findings
                        if f.get("source") in ("structural", "human")
                        or str(f.get("severity") or "").strip().lower() == "critical"]
            blocking_ids = {id(f) for f in blocking}
            advisory = [f for f in all_findings if id(f) not in blocking_ids]
            clean = not structural and not blocking
        else:
            clean = (combined.get("verdict") != "changes_requested") and not structural

        rec = {"round": rnd, "leaves": len(leaves), "verdict": combined.get("verdict"),
               "structural_findings": len(structural), "findings": len(all_findings)}
        if dropped:
            rec["disputed_dropped"] = dropped
        if advisory_pass:
            rec["advisory"] = len(advisory)
        trail["rounds"].append(rec)
        on_event("plan_critique",
                 f"round {rnd}: {combined.get('verdict')} ({rec['findings']} findings, "
                 f"{len(structural)} structural)")

        prior = leaves
        if clean:
            trail["verdict"] = "ready"
            trail["structural_clean"] = True
            if advisory:
                trail["advisory_findings"] = advisory
                on_event("plan_advisory", f"ready with {len(advisory)} advisory finding(s)")
            break
        # human feedback is consumed once folded into a refine round (subsequent rounds re-critique)
        fb = None
    else:
        trail["verdict"] = "needs_review"

    # Surface the findings the final plan STILL carries so a reviewer (and the future approve gate)
    # can see WHAT is unresolved, not just a count. Empty when the last round was clean.
    if trail.get("verdict") != "ready":
        trail["open_findings"] = list((combined or {}).get("findings") or [])
    final = prior or []
    return _finalize(trail, final, parent_id=parent_id, workspace=workspace, on_event=on_event)


def _finalize(trail, leaves, *, parent_id, workspace, on_event):
    trail["structural_clean"] = not validate_plan(leaves)
    ordered = topo_order(leaves) if trail["structural_clean"] else leaves
    trail["ordered_ids"] = [lf.id for lf in ordered]
    trail["leaves"] = [lf.as_backlog_item(parent_id=parent_id, workspace=workspace) for lf in ordered]
    if trail.get("verdict") is None:
        trail["verdict"] = "ready" if trail["structural_clean"] else "needs_review"
    on_event("plan_done", f"{trail['verdict']} ({len(leaves)} leaves, "
                          f"structural_clean={trail['structural_clean']})")
    return trail


def _leaves_from_items(items) -> list[Leaf]:
    """Rebuild Leaf objects from previously-rendered backlog-item dicts (a session's current plan),
    so an interactive revision can hand the planner the plan to revise rather than starting over."""
    out = []
    for it in items or []:
        if not isinstance(it, dict) or not it.get("id"):
            continue
        out.append(Leaf(id=str(it["id"]), title=str(it.get("title") or it["id"]),
                        objective=str(it.get("objective") or ""), acceptance=str(it.get("acceptance") or ""),
                        evidence=str(it.get("evidence") or ""),
                        depends_on=list(it.get("depends_on") or []), integration=bool(it.get("integration"))))
    return out


def revise_once(*, commission: dict, current_leaves,
                propose: Callable[[dict, list[Leaf] | None, dict | None], str],
                feedback,
                on_event: Callable[[str, str], None] = lambda phase, detail="": None) -> dict:
    """ONE interactive revision pass: hand the planner the CURRENT plan + the human's feedback, take
    one redraft, run the DETERMINISTIC structural check, and return the trail. No model critic here --
    in the interactive loop the human IS the critic (the full model critique is emit_plan / on-demand).
    Falls back to the current plan if the redraft is unparseable, so a bad turn never loses the plan."""
    workspace = commission.get("workspace") or "orchestrator-real"
    parent_id = commission.get("id") or "commission"
    prior = _leaves_from_items(current_leaves)
    fb = feedback_as_critique(feedback)
    on_event("plan_revise", "applying feedback")
    try:
        leaves = parse_plan(propose(commission, prior or None, fb))
    except PlanError as exc:
        on_event("plan_error", str(exc)[:120])
        leaves = prior
    structural = validate_plan(leaves)
    trail = {"verdict": None, "rounds": [{"round": 1, "leaves": len(leaves), "verdict": "revise",
             "structural_findings": len(structural), "findings": len(structural)}],
             "structural_clean": False, "structural_problems": structural}
    return _finalize(trail, leaves, parent_id=parent_id, workspace=workspace, on_event=on_event)


# --- conversational plan file: a human-readable plan.md (editable directly) + structured plan.yaml ---

FEEDBACK_MARKER = "## Feedback"
_FEEDBACK_HELP = ("_Reply in the HAI chat to revise this plan, or edit the lines below and re-run "
                  "`/plan`. Leave empty to accept. Then `/plan approve`._")


def render_plan_md(trail: dict, *, commission: dict) -> str:
    """Human-readable, chat-renderable plan with an editable Feedback section at the bottom."""
    lines = [f"# Decomposition plan — {commission.get('id', 'commission')}: {commission.get('title') or ''}".rstrip(),
             "",
             f"**Status:** {trail.get('verdict')}  ·  **leaves:** {len(trail.get('leaves') or [])}  ·  "
             f"**structurally clean:** {'yes' if trail.get('structural_clean') else 'NO — needs work'}",
             "",
             "## Commission objective", str(commission.get("objective") or "").strip() or "_(none)_", "",
             "## Integration acceptance",
             str(commission.get("integration_acceptance") or "").strip() or "_(none — the plan must add one)_", "",
             "## Leaves (execution order)"]
    by_id = {lf["id"]: lf for lf in (trail.get("leaves") or [])}
    for lid in (trail.get("ordered_ids") or list(by_id)):
        lf = by_id.get(lid)
        if not lf:
            continue
        tag = " **[INTEGRATION]**" if lf.get("integration") else ""
        deps = ", ".join(lf.get("depends_on") or []) or "—"
        lines += [f"### {lf['id']} — {lf.get('title') or ''}{tag}",
                  f"- **depends_on:** {deps}",
                  f"- **objective:** {lf.get('objective') or ''}",
                  f"- **acceptance:** {lf.get('acceptance') or ''}", ""]
    problems = trail.get("structural_problems") or []
    if problems:
        lines += ["## ⚠ Structural problems to resolve"]
        lines += [f"- {p.get('explanation')}" for p in problems] + [""]
    open_findings = trail.get("open_findings") or []
    if open_findings:
        lines += ["## Outstanding findings (unresolved by the last round)"]
        for f in open_findings:
            sev = str(f.get("severity") or "medium")
            src = f.get("source")
            who = f" _[{src}]_" if src else ""
            lines.append(f"- **[{sev}]**{who} {f.get('explanation') or ''}")
            act = f.get("recommended_action")
            if act:
                lines.append(f"  - FIX: {act}")
        lines.append("")
    rounds = trail.get("rounds") or []
    if rounds:
        lines += ["## Critique trail"]
        for r in rounds:
            if r.get("error"):
                raw = f' · raw started: "{r["raw"]}"' if r.get("raw") else ""
                lines.append(f"- round {r.get('round')}: ⚠ draft did not parse — {r.get('error')} "
                             f"(fell back to the prior plan){raw}")
            else:
                lines.append(f"- round {r.get('round')}: {r.get('verdict')} "
                             f"({r.get('findings', 0)} findings, {r.get('structural_findings', 0)} structural)")
        lines.append("")
    lines += [FEEDBACK_MARKER, _FEEDBACK_HELP, ""]
    return "\n".join(lines)


def parse_feedback_md(md_text: str) -> list[str]:
    """Extract human feedback lines from a plan.md's Feedback section (the direct-edit channel).
    Skips the help line and blanks; returns [] when nothing was added."""
    text = md_text or ""
    idx = text.find(FEEDBACK_MARKER)
    if idx < 0:
        return []
    body = text[idx + len(FEEDBACK_MARKER):]
    out = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line == _FEEDBACK_HELP.strip() or line.startswith("_"):
            continue
        out.append(line.lstrip("-* ").strip())
    return [x for x in out if x]


def write_plan(plans_dir: str, commission: dict, trail: dict) -> dict:
    """Persist plan.yaml (structured, for the future approve step) + plan.md (conversational/editable).
    Returns {dir, md, yaml}. Best-effort: never raise into the chat path."""
    import os
    import json
    commission_id = str(commission.get("id") or "commission")
    d = os.path.join(plans_dir, commission_id)
    paths = {"dir": d, "md": os.path.join(d, "plan.md"), "yaml": os.path.join(d, "plan.yaml")}
    try:
        os.makedirs(d, exist_ok=True)
        with open(paths["md"], "w", encoding="utf-8") as fh:
            fh.write(render_plan_md(trail, commission=commission))
        payload = {"commission": {k: commission.get(k) for k in ("id", "title", "objective",
                   "integration_acceptance", "workspace")},
                   "verdict": trail.get("verdict"), "structural_clean": trail.get("structural_clean"),
                   "ordered_ids": trail.get("ordered_ids"), "leaves": trail.get("leaves")}
        try:
            import yaml
            with open(paths["yaml"], "w", encoding="utf-8") as fh:
                yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True)
        except Exception:
            with open(paths["yaml"], "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
    except OSError:
        pass
    return paths


def read_feedback(plans_dir: str, commission_id: str) -> list[str]:
    """Read direct-edit feedback from an existing plan.md, or [] if none/unreadable."""
    import os
    p = os.path.join(plans_dir, str(commission_id or "commission"), "plan.md")
    try:
        with open(p, encoding="utf-8") as fh:
            return parse_feedback_md(fh.read())
    except OSError:
        return []
