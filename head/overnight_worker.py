"""Phase 6B-2: the per-item overnight worker.

Drives ONE approved backlog item to a verified outcome, reusing the proven self-engineering
machinery. It creates a deterministic coding task, asks the 27B (via the orchestrator's own
self-eng path) to implement it, then lets the DETERMINISTIC VERIFIER (coding_test / acceptance)
decide DONE vs BAILED -- never the model's self-assessment (the defect-#2 lesson).

The model-driving and coding calls are INJECTED (WorkerDeps), so the worker's control logic --
task lifecycle, bounded HYBRID retry, outcome mapping, budget/halt checks -- is unit-testable
without a live model. The real deps wire to coding_client + the orchestrator's _run_query_core.

Locked retry policy (HYBRID): each attempt drives implementation then runs the verifier; on a red
verifier, retry up to max_attempts (diagnosis-driven). The coding-runner's Phase-3 recovery
(identical-failure fingerprinting + one baseline restore) handles anti-repeat DETERMINISTICALLY
inside drive(), so this layer just bounds attempts and maps the verifier result to an outcome.

Timings (Phase 6B-3): every stage records wall-clock seconds (monotonic clock) into a per-item
"timings" dict. Purely additive; no behavior change to any stage. Stages: drive, verify, critique,
fix rounds, re-critique, dispute resolution. Missing stages are simply absent from the dict.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import overnight
import review_context
from overnight import DONE, BAILED, ERROR, NO_CHANGE  # outcome constants


@dataclass
class WorkerDeps:
    start_task: Callable[[dict], str]                 # (item) -> task_id
    drive: Callable[[str, dict, object], dict | None]  # (task_id, item, budget) -> optional {telemetry: ...}
    verify: Callable[[str, dict], dict]               # (task_id, item) -> {"passed": bool, "summary": str}
    finish_task: Callable[[str], None]                # (task_id) -> merge/close on success
    bail_task: Callable[[str], None]                  # (task_id) -> abandon + revert workspace
    # 6C: optional adversarial review run AFTER the deterministic verifier passes, BEFORE finish.
    # (task_id, item, budget) -> trail dict {verdict, ...}; None disables the ladder.
    review: Callable[[str, dict, object], dict] | None = None
    # When wired, items claiming new tests but adding none in diff are refused DONE.
    # None disables the guard (defaulted in unit tests to preserve existing cases).
    get_diff: Callable[[str], str] | None = None


def _add_timing(timings: dict[str, float], stage: str, dt: float) -> None:
    """Accumulate a monotonic-clock delta into the timings dict."""
    timings[stage] = timings.get(stage, 0.0) + dt


# rc=0 is insufficient for DONE if acceptance demands tests: the diff must actually add coverage

import re as _re

# Clear test-AUTHORING phrases. Substrings (not one big regex) so each is easy to audit for false
# positives. Deliberately conservative: 'existing tests pass' / 'the suite stays green' must NOT match
# (a false positive would bail a legitimate item), while 'unit tests', 'a new test', 'tests assert…'
# do. See _acceptance_demands_tests.
_ACCEPTANCE_TEST_PHRASES = (
    "unit test", "new test", "add a test", "adds a test", "adding a test", "add test", "adds test",
    "regression test", "passing test", "test case", "test that", "tests that", "tests assert",
    "test asserting", "tests cover", "test covering", "test proving",
)
_ADDED_TEST_DEF = _re.compile(r"^\+\s*(?:async\s+)?def\s+test\w*\s*\(", _re.MULTILINE)
_ADDED_TEST_FILE = _re.compile(r"^\+\+\+\s+b/(?:.*/)?tests?/[\w./-]*test[\w./-]*\.py", _re.MULTILINE)


def _acceptance_demands_tests(acceptance) -> bool:
    """True when the acceptance text explicitly calls for AUTHORING tests (not merely 'the suite
    stays green'). Deliberately conservative — a false positive would bail a legitimate item, so we
    only fire on clear test-authoring language, never on 'existing tests pass' boilerplate."""
    if not acceptance or not isinstance(acceptance, str):
        return False
    low = acceptance.lower()
    return any(phrase in low for phrase in _ACCEPTANCE_TEST_PHRASES)


def _diff_adds_tests(diff) -> bool:
    """True when a unified diff ADDS test coverage: an added ``def test_*`` line (also covers a
    brand-new test file, whose body is all '+' lines), or a newly-created file under a tests/ dir.
    Removed lines (leading '-') never count."""
    if not diff or not isinstance(diff, str):
        return False
    return bool(_ADDED_TEST_DEF.search(diff) or _ADDED_TEST_FILE.search(diff))



_DIFF_TOUCHED_RE = _re.compile(r"^\+\+\+\s+b/(\S+)", _re.MULTILINE)


def _diff_touched_files(diff) -> set[str]:
    """Basenames of every file a unified diff touches, from its ``+++ b/<path>`` headers."""
    if not diff or not isinstance(diff, str):
        return set()
    return {m.group(1).rsplit("/", 1)[-1] for m in _DIFF_TOUCHED_RE.finditer(diff)}


def _is_test_path(path: str) -> bool:
    """A workspace path that holds tests (or test tooling) rather than production code."""
    parts = str(path or "").replace("\\", "/").split("/")
    base = parts[-1] if parts else ""
    return any(p in ("tests", "test") for p in parts[:-1]) or base.startswith("test_")


def _nontest_diff(diff) -> str:
    """The production portion of a unified diff: per-file chunks excluding test files.
    This filters out test mentions to ensure required symbols appear in production code."""
    if not diff or not isinstance(diff, str):
        return ""
    kept: list[str] = []
    include = True
    for line in str(diff).splitlines():
        if line.startswith("diff --git "):
            # path from the b/ side (last token), e.g. "diff --git a/x.py b/x.py"
            tok = line.split()[-1]
            include = not _is_test_path(tok[2:] if tok.startswith("b/") else tok)
        elif line.startswith("+++ "):
            tok = line.split()[-1]
            if tok != "/dev/null":
                include = not _is_test_path(tok[2:] if tok.startswith("b/") else tok)
            if include:
                kept.append(line)
            continue
        if include:
            kept.append(line)
    return "\n".join(kept)


def _missed_scope(item, diff) -> list[str]:
    """Return scope targets from the objective/acceptance spec that are missing
    from the diff. Coverage is deliberately generous to avoid false positives:
    * A ``*.py`` file target is covered if any file with that basename is touched.
    * A symbol target is covered if it appears anywhere in the diff text
      (including context lines), except within Python comments (`#`).
      - Dotted refs (e.g., ``module.func``) match on the final attribute only.
      - String literals count as real wiring."""
    spec = f"{item.get('objective') or ''} {item.get('acceptance') or ''}"
    targets = review_context.extract_scope_targets(spec)
    if not targets:
        return []
    touched = _diff_touched_files(diff)   # file coverage counts test files too (a spec may name one)
    # SYMBOL coverage is B1.1: the wiring must appear in NON-TEST code, comments stripped.
    text = review_context.strip_diff_comments(_nontest_diff(diff))
    missed = []
    for t in targets:
        if t.endswith(".py"):
            if t.rsplit("/", 1)[-1] not in touched:
                missed.append(t)
        elif t not in text and t.rsplit(".", 1)[-1] not in text:
            missed.append(t)
    return missed


def work_item(item, budget, deps: WorkerDeps, *, max_attempts: int = 2,
              halt_check: Callable[[], bool] = lambda: False) -> dict:
    """Drive one backlog item. Returns {"outcome", "evidence", "attempts", ...}.

    homelab-domain items cannot auto-apply (sandbox-only) -- they bail with a clear note until the
    morning-review apply path exists (a later sub-phase). orchestrator items run the implement ->
    verify -> bounded-retry loop with the verifier deciding DONE."""
    timings: dict[str, float] = {}   # stage -> wall-clock seconds (monotonic)

    if item.get("domain") == "homelab":
        return {"outcome": BAILED,
                "evidence": "homelab-domain item needs the morning-review apply path (not yet built); "
                            "overnight cannot auto-apply live config. Skipped.",
                "timings": timings}

    try:
        task_id = deps.start_task(item)
    except Exception as exc:
        return {"outcome": ERROR, "evidence": f"could not start coding task: {exc!r}",
                "timings": timings}
    if not task_id:
        return {"outcome": ERROR, "evidence": "start_task returned no task id",
                "timings": timings}

    attempts: list[dict] = []
    # B1 feedback channel: a scope-gap forced fix round re-drives with an AUGMENTED objective (the
    # exact missed targets spelled out) so the author knows what to wire, without mutating the
    # caller's item record. Starts as the item itself; only a scope gap ever swaps it.
    drive_item = item
    try:
        for attempt in range(1, max(1, int(max_attempts)) + 1):
            if halt_check():
                deps.bail_task(task_id)
                return {"outcome": BAILED, "evidence": "halted mid-item", "attempts": attempts,
                        "task_id": task_id, "timings": timings}
            if getattr(budget, "remaining_seconds", 1) <= 0:
                deps.bail_task(task_id)
                return {"outcome": BAILED, "evidence": "out of time mid-item", "attempts": attempts,
                        "task_id": task_id, "timings": timings}
            overnight.emit("drive", f"attempt {attempt}/{max_attempts} (implementing)")
            t0 = time.monotonic()
            try:
                drive_meta = deps.drive(task_id, drive_item, budget)
            except Exception as exc:
                _add_timing(timings, "drive", time.monotonic() - t0)
                overnight.emit("drive_error", f"attempt {attempt}: {exc!r}"[:120])
                attempts.append({"attempt": attempt, "drive_error": repr(exc)})
                continue
            _add_timing(timings, "drive", time.monotonic() - t0)
            # Drive callback may return telemetry dict; copy it if present. Graceful no-op.
            if isinstance(drive_meta, dict) and drive_meta.get("telemetry"):
                timings["drive_tele"] = drive_meta["telemetry"]
            # Time verify separately
            tv = time.monotonic()
            v = deps.verify(task_id, item) or {}
            _add_timing(timings, "verify", time.monotonic() - tv)
            overnight.emit("verify", f"attempt {attempt}: passed={bool(v.get('passed'))} | {v.get('summary')}")
            attempts.append({"attempt": attempt, "passed": bool(v.get("passed")),
                             "summary": v.get("summary")})
            if v.get("passed"):
                adversarial = None
                if deps.review is not None:
                    overnight.emit("review_start", "verifier green -> adversarial ladder")
                    try:
                        adversarial = deps.review(task_id, item, budget)
                    except Exception as exc:
                        adversarial = {"verdict": "skipped", "reason": f"review_error: {exc!r}"}
                    # Merge per-stage timings from the ladder trail (if present).  The ladder
                    # records its own internal stage times (critique, fix, recritique, dispute),
                    # so we do NOT add a separate wrapper-level "critique" wall-clock here — that
                    # would double-count with whatever the ladder already timed.
                    if adversarial and isinstance(adversarial.get("timings"), dict):
                        for k, vv in adversarial["timings"].items():
                            _add_timing(timings, k, vv)
                    # A fix that broke the deterministic verifier => revert the whole item (BAILED).
                    if adversarial and adversarial.get("verdict") == "reverify_red":
                        deps.bail_task(task_id)
                        return {"outcome": BAILED,
                                "evidence": "adversarial fix broke the verifier; reverted",
                                "attempts": attempts, "task_id": task_id, "adversarial": adversarial,
                                "timings": timings}

                # Empty/whitespace diff means no actual change; treat as not done and revert.
                if deps.get_diff is not None:
                    try:
                        _dt = deps.get_diff(task_id)
                    except Exception:
                        _dt = None
                    if _dt is not None and not str(_dt).strip():
                        overnight.emit("empty_diff", "item produced no changes -> bail_human")
                        deps.bail_task(task_id)
                        return {"outcome": BAILED,
                                "evidence": "no changes produced: the item's diff is empty — the drive "
                                            "made no edits (e.g. a search-storm that never wrote code)",
                                "attempts": attempts, "task_id": task_id, "timings": timings,
                                "adversarial": {**(adversarial if isinstance(adversarial, dict) else {}),
                                                "verdict": "bail_human", "reason": "no_changes_produced"}}

                # Acceptance-coverage gate: green verifier is necessary but NOT sufficient (unit-only passes).
                # Check spec targets against diff; if missed, allow ONE retry via drive_item, else bail human.
                # Fails OPEN on missing diff/spec/error to avoid killing genuinely-green items.
                if deps.get_diff is not None:
                    try:
                        _scope_diff = deps.get_diff(task_id)
                    except Exception:
                        _scope_diff = None
                    missed = _missed_scope(item, _scope_diff) if _scope_diff else []
                    if missed:
                        attempts[-1]["scope_missed"] = list(missed)
                        if attempt < max(1, int(max_attempts)):
                            overnight.emit("scope_gap",
                                           f"acceptance names untouched target(s) {missed} -> forced fix round")
                            drive_item = {**item, "objective": (
                                f"{item.get('objective') or ''}\n\nSCOPE GAP (mechanical check on your "
                                f"current diff): the objective/acceptance names {', '.join(missed)} but "
                                "your diff never touches them. The unit suite is green, but the change "
                                "is NOT integrated — complete the wiring in those exact targets (edit "
                                "them, or add code that references them), keeping the suite green.")}
                            continue
                        _rv = adversarial.get("verdict") if isinstance(adversarial, dict) else None
                        if _rv in ("pass", "fixed"):
                            overnight.emit("scope_note",
                                           f"gate-only miss {missed} but review converged clean -> done + note")
                            adversarial = {**adversarial, "scope_note": list(missed)}
                        else:
                            overnight.emit("scope_gap",
                                           f"still untouched after forced fix round: {missed} -> bail_human")
                            finding = {
                                "severity": "high",
                                "explanation": "Integration-hollow: the objective/acceptance names "
                                               f"{', '.join(missed)} but the item's cumulative diff never "
                                               "touches them, even after a forced fix round spelling out "
                                               "the gap. The verifier passed only because un-wired code "
                                               "cannot break the existing suite.",
                                "recommended_action": "Hand-finish the wiring in the named targets, or "
                                                      "re-run with sharpened acceptance; do not mark DONE.",
                            }
                            scope_adv = dict(adversarial) if isinstance(adversarial, dict) else {}
                            scope_adv["verdict"] = "bail_human"
                            scope_adv["reason"] = "acceptance_scope_missed"
                            scope_adv["findings_outstanding"] = (
                                list(scope_adv.get("findings_outstanding") or []) + [finding])
                            deps.bail_task(task_id)
                            return {"outcome": BAILED,
                                    "evidence": "hollow: acceptance names "
                                                + ", ".join(missed)
                                                + " but the diff never touches them — flagged for human review",
                                    "attempts": attempts, "task_id": task_id, "timings": timings,
                                    "adversarial": scope_adv}

                # Acceptance-delta guard: an item whose acceptance NAMES new tests must actually add
                # test coverage. Checked AFTER review so tests added during fix rounds count. Fails
                # OPEN — a diff-fetch error or an acceptance that doesn't ask for tests never bails a
                # genuinely-green item. A hollow pass is routed through the SAME flag-for-human channel
                # as bail_human (synthesized adversarial verdict) and the work is reverted clean.
                if (deps.get_diff is not None
                        and _acceptance_demands_tests(item.get("acceptance"))):
                    try:
                        diff_text = deps.get_diff(task_id) or ""
                    except Exception:
                        diff_text = ""
                    if diff_text and not _diff_adds_tests(diff_text):
                        overnight.emit("acceptance_delta",
                                       "acceptance names new tests but the diff adds none -> bail_human")
                        finding = {
                            "severity": "high",
                            "explanation": "Acceptance requires new tests, but the item's cumulative "
                                           "diff adds no test coverage (no added `def test_*` and no "
                                           "new tests/ file). The verifier passed only because inert "
                                           "code cannot break the existing suite.",
                            "recommended_action": "Re-run with sharpened acceptance naming the exact "
                                                  "tests, or hand-finish; do not mark DONE.",
                        }
                        hollow_adv = dict(adversarial) if isinstance(adversarial, dict) else {}
                        hollow_adv["verdict"] = "bail_human"
                        hollow_adv["reason"] = "hollow_no_tests"
                        hollow_adv["findings_outstanding"] = (
                            list(hollow_adv.get("findings_outstanding") or []) + [finding])
                        deps.bail_task(task_id)
                        return {"outcome": BAILED,
                                "evidence": "hollow: acceptance requires new tests but the diff adds "
                                            "no test coverage — flagged for human review",
                                "attempts": attempts, "task_id": task_id, "timings": timings,
                                "adversarial": hollow_adv}
                overnight.emit("item_done", f"finishing task {str(task_id)[:8]}")
                deps.finish_task(task_id)
                out = {"outcome": DONE, "evidence": v.get("summary") or "verifier passed",
                       "attempts": attempts, "task_id": task_id, "tests": v, "timings": timings}
                if adversarial is not None:
                    out["adversarial"] = adversarial   # PASS/FIXED = clean; bail_human = green but flagged for review
                return out
        # attempts exhausted without a green verifier
        deps.bail_task(task_id)
        last = attempts[-1] if attempts else {}
        return {"outcome": BAILED,
                "evidence": "verifier never passed within attempt budget: "
                            + str(last.get("summary") or last.get("drive_error") or "no detail"),
                "attempts": attempts, "task_id": task_id, "timings": timings}
    except Exception as exc:
        try:
            deps.bail_task(task_id)
        except Exception:
            pass
        return {"outcome": ERROR, "evidence": f"worker exception: {exc!r}", "attempts": attempts,
                "task_id": task_id, "timings": timings}
