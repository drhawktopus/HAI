"""Project #12 Phase-6 overnight autonomy: the unattended run loop + circuit breaker.

Drives approved, test-verifiable backlog items in a bounded, SANDBOX-ONLY run with no human
watching. The control core here is MODEL-FREE and fully testable; the per-item WORKER (which
actually drives a coding task + the adversarial review ladder, 6B-2/6C) is INJECTED, so the
loop's stop-conditions are deterministic under test.

Locked rules honored (see 06-SELF-ENGINEERING-AGENT-PLAN.md):
  - time is a CEILING, not a quota: stop the moment work is genuinely done (backlog exhausted),
    OR no net progress across N items, OR the hard time ceiling -- whichever comes first. Never
    grind to fill the clock.
  - the VERIFIER decides "done", never the model's self-assessment: the worker returns an outcome
    derived from tests/acceptance; this loop only sequences, checkpoints, and stops.
  - sandbox-only + the kill switch are honored: the caller flips sandbox-only on before the loop;
    halt_check() is polled every iteration so the kill switch ends a run promptly.
"""
from __future__ import annotations

import datetime
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import backlog
import config


# --- worker outcomes -------------------------------------------------------------------------
# Only DONE counts as net progress (resets the no-progress counter). Everything else moves on to
# the next item AND counts toward the no-progress circuit breaker -- a night of nothing-but-bails
# is not productive, so it should stop rather than churn the whole backlog.
DONE = "done"          # item completed AND verified (tests/acceptance pass)
BAILED = "bailed"      # attempted, gave up with evidence (couldn't verify / out of depth)
ERROR = "error"        # the worker raised / a transient failure
NO_CHANGE = "no_change"  # nothing to do / produced no diff
SKIPPED = "skipped"    # dry-run or deliberately not executed
PROGRESS_OUTCOMES = frozenset({DONE})
TERMINAL_OUTCOMES = frozenset({DONE, BAILED, ERROR, NO_CHANGE, SKIPPED})


# --- D0 workspace stale-check (pure helpers) -------------------------------------------------------
# Guards against running an overnight item on a workspace whose baseline is OLDER than live (a deploy
# landed but sync-workspace.sh was not run afterward, so HAI would patch stale code).
#
# ARCHITECTURE NOTE (why this is split): no single container sees both trees. The orchestrator sees
# its OWN deployed source (/app/*.py == live, baked at build) but NOT the coding workspace; the
# coding-runner sees the workspace but not live. So the production guard (app._make_stale_guard)
# hashes live locally, fetches the workspace hashes + an "ahead" flag from the runner
# (/files/source_hashes), and feeds them to the PURE workspace_stale() below.
#
# SCOPE = TOP-LEVEL *.py: those are the deployed modules whose drift causes apply failures, and the
# only set the orchestrator can hash at runtime (/app/tests is bind-mounted for the test run only and
# is absent in the live container). A tests-only live change with no source change is not detected —
# rare, and it does not cause apply failures.

def _source_manifest(root: str) -> dict[str, str]:
    """Map top-level ``*.py`` filename -> sha256 hex digest for the source files under *root*
    (skips ``*.bak`` backups). Used on the orchestrator's own ``/app`` to fingerprint live; the
    coding-runner builds the equivalent for the workspace. A missing/unreadable dir -> empty map."""
    import hashlib
    import os
    manifest: dict[str, str] = {}
    if not os.path.isdir(root):
        return manifest
    for name in sorted(os.listdir(root)):
        if not name.endswith(".py") or name.endswith(".bak"):
            continue
        full = os.path.join(root, name)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, "rb") as fh:
                manifest[name] = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            continue
    return manifest


def stale_reason(ws: str = "orchestrator-real") -> str | None:
    """The refusal text when workspace `ws` is BEHIND live (a deploy landed without sync-workspace.sh, so HAI would
    patch old code), else None. ONE derivation for every door that starts work on the workspace: the overnight
    pre-item guard and the coding_task start tool (#34 register "McCoy merge plan" item 5 / Research-Synthesis R2:
    a whole HAI coding job was once built on a stale workspace). FAILS OPEN - a broken check (runner down,
    unreadable /app) returns None: a false refusal blocks good work, the worst case of failing open is the status
    quo. Only the self workspace has a live counterpart; any other workspace returns None."""
    import config
    if ws != "orchestrator-real" or not config.OVERNIGHT_WORKSPACE_STALE_CHECK:
        return None
    lag = workspace_lag(ws)
    if lag["state"] != "stale":
        return None   # in sync, ahead (undeployed work), or could not tell -> fail open
    stale = lag["files"]
    shown = ", ".join(stale[:6]) + (" …" if len(stale) > 6 else "")
    return (f"workspace '{ws}' is STALE vs live: {len(stale)} source file(s) differ ({shown}) with no "
            f"undeployed commits. Run the stack's sync-workspace.sh ({config.STACK_DIR or '<stack>'}) before this run.")


def workspace_lag(ws: str = "orchestrator-real") -> dict:
    """{"state": "in_sync" | "ahead" | "stale" | "unknown", "files": [differing top-level .py]} - the self
    workspace against live /app. "unknown" = could not tell (runner down, unreadable /app); it is never reported
    as in sync. Shared by stale_reason (the doors) and the deck's lag row."""
    import config
    import coding_client
    try:
        live = _source_manifest(config.SELFENG_LIVE_ROOT)
        if not live:
            return {"state": "unknown", "files": []}
        resp = coding_client.workspace_source_hashes(ws) or {}
        raw = resp.get("files")
        if not isinstance(raw, list):
            return {"state": "unknown", "files": []}
        # Scope to TOP-LEVEL *.py (the set the orchestrator's /app can also see): drop tests/ and any nested
        # paths so a tests-only entry never reads as a false diff.
        ws_top = {f.get("rel"): f.get("sha256") for f in raw
                  if isinstance(f, dict) and f.get("rel") and "/" not in str(f.get("rel"))}
        behind, differing = workspace_stale(live, ws_top, bool(resp.get("ahead")))
    except Exception as exc:
        try:
            emit("stale_check_error", repr(exc)[:120])
        except Exception:
            pass
        return {"state": "unknown", "files": []}
    state = "stale" if behind else ("ahead" if differing else "in_sync")
    return {"state": state, "files": list(differing)}


def workspace_stale(
    live_hashes: dict[str, str],
    ws_hashes: dict[str, str],
    ws_ahead: bool,
) -> tuple[bool, list[str]]:
    """Pure decision: is the workspace BEHIND live (stale), given the two source manifests and
    whether the workspace has undeployed commits beyond its last sync baseline?

    Returns ``(is_behind, differing_files)``:
      - identical manifests            -> (False, [])          trees match, run.
      - differ AND workspace is ahead  -> (False, differing)   undeployed work, allow (optimistic).
      - differ AND workspace NOT ahead -> (True,  differing)   live moved without a re-sync -> STALE.

    ``differing_files`` is the sorted set of names whose digests differ (or exist on only one side).
    Comparing hash DICTS (not dirs) keeps this trivially unit-testable and free of I/O."""
    differing = sorted(
        name for name in (set(live_hashes) | set(ws_hashes))
        if live_hashes.get(name) != ws_hashes.get(name)
    )
    if not differing:
        return False, []
    if ws_ahead:
        return False, differing   # undeployed committed work — allowed despite drift
    return True, differing        # behind live with nothing undeployed to explain it -> stale


# --- end D0 helpers -------------------------------------------------------------------------------


# The bail_human reasons that mean "the verifier was green but the item didn't do the asked work"
# (a LYING green): empty diff, tests demanded but never added, spec-named targets never touched.
# Shared by score_run (the hollow counter) and anything reporting on the guard family.
HOLLOW_REASONS = ("no_changes_produced", "hollow_no_tests", "acceptance_scope_missed")


def score_run(items) -> dict:
    """Two-axis run scoring, kept DELIBERATELY SEPARATE (#5): CODING-correctness (did the deterministic
    verifier pass?) is a DIFFERENT axis from REVIEW-convergence (did the adversarial ladder converge
    cleanly, flag for a human, or revert?). A run whose code is correct but whose critics never converged
    is NOT a coding failure, and grading must not conflate the two (that's exactly what made the
    unify benchmark read as a "struggle" when the code passed at 9 minutes). Pure/testable; dry-run
    SKIPPED items are excluded from both axes since nothing was executed."""
    executed = [r for r in (items or []) if r.get("outcome") != SKIPPED]
    coding_correct = sum(1 for r in executed if r.get("outcome") == DONE)
    # B3 honest scoreboard: items that passed verification but failed coverage/delta/empty-diff guards
    # count as "hollow" (a distinct failure class, not just generic "incorrect"). This sub-count ensures
    # the density metric penalizes lying greens rather than ignoring them.
    hollow = sum(1 for r in executed
                 if r.get("outcome") == BAILED
                 and (r.get("adversarial") or {}).get("reason") in HOLLOW_REASONS)
    review = {"converged": 0, "flagged_for_human": 0, "reverted": 0, "skipped": 0, "not_reviewed": 0}
    for r in executed:
        adv = r.get("adversarial")
        verdict = (adv or {}).get("verdict")
        if not adv:
            review["not_reviewed"] += 1            # ladder never ran (item bailed pre-verifier, etc.)
        elif verdict in ("pass", "fixed"):
            review["converged"] += 1               # clean review
        elif verdict == "bail_human":
            review["flagged_for_human"] += 1        # tests GREEN but critics unhappy -> human review
        elif verdict == "reverify_red":
            review["reverted"] += 1                 # a fix broke the verifier -> reverted
        else:                                       # "skipped" (no time / critic error) or unknown
            review["skipped"] += 1
    return {
        "items_executed": len(executed),
        "coding": {"correct": coding_correct, "incorrect": len(executed) - coding_correct,
                   "hollow": hollow},
        "review": review,
    }


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def parse_test_count(summary) -> int | None:
    """Extract the test count from a unittest summary string like
    ``rc=0 | Ran 985 tests in 88.34s`` → ``985``, or ``None`` when absent."""
    import re
    if not isinstance(summary, str):
        return None
    m = re.search(r"Ran\s+(\d+)\s+test", summary)
    if m:
        return int(m.group(1))
    return None


# --- live activity events (observability). The app installs a sink that appends to a tailable
# per-run activity log + surfaces the latest events in /self-eng/overnight/status, so a run's
# real-time progress (which item, drive attempt, verify result, adversarial tier/verdict/fix round)
# is visible instead of inferred. Single active run, so a module-level sink is fine. ---
_EVENT_SINK = [None]


def set_event_sink(fn):
    """Install (fn(phase:str, detail:str)) or None to clear. The worker/ladder call emit()."""
    _EVENT_SINK[0] = fn


def emit(phase, detail=""):
    fn = _EVENT_SINK[0]
    if fn:
        try:
            fn(str(phase), str(detail))
        except Exception:
            pass


@dataclass
class RunBudget:
    """Time ceiling + convergence bounds for one overnight run."""
    max_seconds: int
    no_progress_limit: int = 3   # consecutive non-DONE items before we stop ("no net progress")
    max_items: int = 50          # absolute safety ceiling on items attempted in one run
    started_monotonic: float = field(default_factory=time.monotonic)

    def __post_init__(self):
        # clamp: at least 1 minute, at most 24h (an "all night" run); sane convergence bounds
        self.max_seconds = max(60, min(int(self.max_seconds), 24 * 3600))
        self.no_progress_limit = max(1, int(self.no_progress_limit))
        self.max_items = max(1, int(self.max_items))

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_monotonic

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.max_seconds - self.elapsed_seconds)

    def expired(self) -> bool:
        return self.remaining_seconds <= 0


def _new_run_id() -> str:
    return "ovr-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# Public alias (bl-runid-immediate): callers outside the module (app._overnight_start) generate the
# run id BEFORE the worker thread spins up, killing the "(starting)" placeholder gap in /status.
new_run_id = _new_run_id


def render_heartbeat(turn, tools) -> str:
    """Render a compact author-loop pulse string for the activity feed.

    Examples:
        ``turn 20 · coding_read 8 · coding_search 5``

    - Tool counts are sorted busiest-first (descending by count, then name).
    - If ``tools`` is empty or None, only the turn part is returned.
    """
    parts = [f"turn {int(turn)}"]
    for name, n in sorted((tools or {}).items(), key=lambda kv: (-kv[1], str(kv[0]))):
        parts.append(f"{name} {n}")
    return " · ".join(parts)


def drive_pass_cap(remaining_seconds, *, est_pass_seconds, ladder_reserve_seconds, hard_cap):
    """How many self-eng continuation passes a per-item DRIVE may use so it leaves at least
    ladder_reserve_seconds for the adversarial ladder. Always >= 1 (the drive must get one pass);
    capped at hard_cap (the global SELF_ENGINEERING_MAX_PASSES). Pure function (testable):
    on a tight budget the drive does fewer passes so the ladder still gets to run; on a big
    overnight budget it gets the full hard_cap."""
    est = max(1, int(est_pass_seconds))
    usable = max(0.0, float(remaining_seconds) - max(0, int(ladder_reserve_seconds)))
    passes = int(usable // est)
    return max(1, min(int(hard_cap), passes))


def next_runnable(eligible, completed_ids, attempted_ids):
    """Pick the first eligible item whose `depends_on` are all SATISFIED, so a promoted commission's leaves
    run in dependency order (l1 -> l2 -> ... -> integration) instead of raw backlog order. Pure +
    testable. A dependency is UNsatisfied when it is:
      - another approved-but-not-yet-done item in THIS run (a sibling still to come), or
      - a sibling that was attempted this run but did NOT complete (bailed/errored -- don't build on a
        failed foundation).
    A dep that is neither pending here nor a failed-here sibling is treated as satisfied (it points at
    something external / already done in a prior run). Returns (item|None, reason): 'ok' with an item,
    else 'blocked' (every remaining item waits on an unmet dep -> a cycle or a bailed foundation)."""
    eligible = list(eligible or [])
    if not eligible:
        return None, "empty"
    pending_ids = {it.get("id") for it in eligible}
    completed = set(completed_ids or ())
    attempted = set(attempted_ids or ())
    for it in eligible:
        deps = it.get("depends_on") or []
        unmet = [d for d in deps
                 if d not in completed and (d in pending_ids or d in attempted)]
        if not unmet:
            return it, "ok"
    return None, "blocked"


def run_overnight(
    budget: RunBudget,
    worker: Callable[[dict, RunBudget], dict],
    *,
    item_source: Callable[[set], list],
    halt_check: Callable[[], bool] = lambda: False,
    on_state: Callable[[dict], None] = lambda state: None,
    run_id: str | None = None,
    dry_run: bool = False,
    resume_done_ids: set | None = None,
    seed_items: list | None = None,
    on_item_done: Callable[[str, str], None] = lambda item_id, run_id: None,
    pre_item_guard: Callable[[dict], str | None] = lambda item: None,
    first_id: str | None = None,
    budget_total_s: float | None = None,
    spent_before_s: float = 0.0,
) -> dict:
    """Run the bounded overnight loop and return the final run-state dict.

    worker(item, budget) -> {"outcome": <one of the constants>, "evidence": <str|dict>, ...}.
    item_source(done_ids) -> list of eligible items. Called fresh each iteration to honor
      mid-run re-approvals and exclude completed items.
    halt_check() -> True to stop. on_state(state) fires after every item and at the end to
      persist state for crash-resume.
    on_item_done(item_id, run_id) fires when an item is DONE. This hooks the persistent
      done-ledger to prevent re-running. Failures are swallowed (best-effort) so a hook
      error does not abort the run.
    Resume logic: `first_id` is the in-flight item at restart. It is prioritized while
      eligible, and the budget is reduced by `spent_before_s` to preserve total run time.
    on_state also fires when an item STARTS (with `current` set) to identify the in-flight
    item during checkpointing."""
    run_id = run_id or _new_run_id()
    seed_items = list(seed_items or [])
    state: dict[str, Any] = {
        "run_id": run_id,
        "started_at": _now_iso(),
        "ended_at": None,
        "max_seconds": budget.max_seconds,
        "no_progress_limit": budget.no_progress_limit,
        "dry_run": bool(dry_run),
        "resumed": bool(resume_done_ids or seed_items),
        "items": seed_items,
        # counts recomputed from any seeded items so a resumed run's totals stay accurate
        "done_count": sum(1 for r in seed_items if r.get("outcome") == DONE),
        "bailed_count": sum(1 for r in seed_items if r.get("outcome") == BAILED),
        "scores": score_run(seed_items),   # #5: coding-correctness vs review-convergence, kept separate
        "stop_reason": None,
        "active": True,
        "budget_total_s": float(budget_total_s if budget_total_s is not None else budget.max_seconds),
        "spent_s": float(spent_before_s or 0.0),
        "current": None,
    }

    def _spent():
        state["spent_s"] = round(float(spent_before_s or 0.0) + budget.elapsed_seconds, 1)

    done_ids: set = set(resume_done_ids or ())
    # Capture which armed items were skipped at run start (already in the done-ledger) so the
    # morning report can explain why "6 armed, only 2 ran" instead of silently stopping.
    state["skipped_armed"] = backlog.skipped_armed(done_ids)
    # completed_ids = items that actually reached DONE (for dependency satisfaction). Distinct from
    # done_ids, which is the attempted-exclusion set (includes bails). Rebuilt from seeds on resume.
    completed_ids: set = {r.get("id") for r in seed_items
                          if r.get("outcome") == DONE and r.get("id") is not None}
    no_progress = 0   # fresh convergence window on resume (don't carry a stale stall)
    emit("run_start", run_id)

    while True:
        if halt_check():
            state["stop_reason"] = "halt"
            break
        if budget.expired():
            state["stop_reason"] = "time"
            break
        if no_progress >= budget.no_progress_limit:
            state["stop_reason"] = "no_progress"
            break
        if len(state["items"]) >= budget.max_items:
            state["stop_reason"] = "max_items"
            break
        try:
            eligible = item_source(set(done_ids)) or []
        except Exception as exc:  # an unreadable backlog must end the run cleanly, not crash it
            state["stop_reason"] = "item_source_error"
            state["error"] = repr(exc)
            break
        if not eligible:
            state["stop_reason"] = "exhausted"
            break

        # Dependency-ordered selection: take the first eligible item whose deps are all done. If
        # nothing is runnable, every remaining item is waiting on an unmet dep (a cycle, or a dep
        # that bailed) -- stop cleanly rather than spin or run a leaf before its foundation exists.
        resumed_item = next((it for it in eligible if first_id and it.get("id") == first_id), None)
        first_id = None   # one pick: the interrupted item goes first, then normal order
        if resumed_item is not None:
            item, sel = resumed_item, "resumed"
        else:
            item, sel = next_runnable(eligible, completed_ids, done_ids)
        if item is None:
            state["stop_reason"] = "blocked"
            state["blocked_ids"] = [it.get("id") for it in eligible]
            break
        # pre_item_guard: refuse the item (with a clear error) when the workspace is stale.
        guard_err = pre_item_guard(item)
        if guard_err:
            item_id = item.get("id")
            emit("item_start", f"{item_id}: {item.get('title') or ''}")
            rec: dict[str, Any] = {
                "id": item_id,
                "title": item.get("title"),
                "kind": item.get("kind"),
                "domain": item.get("domain"),
                "parent_id": item.get("parent_id"),
                "workspace": item.get("workspace"),
                "started_at": _now_iso(),
                "elapsed_at_start_s": round(budget.elapsed_seconds, 1),
                "outcome": ERROR,
                "evidence": guard_err,
                "ended_at": _now_iso(),
            }
            state["items"].append(rec)
            emit("item_end", f"{item_id} -> {ERROR}: {guard_err[:80]}")
            if item_id is not None:
                done_ids.add(item_id)
            no_progress += 1   # guard refusal counts as non-progress
            state["scores"] = score_run(state["items"])
            try:
                on_state(dict(state))
            except Exception:
                pass
            continue

        item_id = item.get("id")
        emit("item_start", f"{item_id}: {item.get('title') or ''}")
        rec: dict[str, Any] = {
            "id": item_id,
            "title": item.get("title"),
            "kind": item.get("kind"),
            "domain": item.get("domain"),
            "parent_id": item.get("parent_id"),   # for the morning commission rollup
            "workspace": item.get("workspace"),   # so a bail adjudicator can find the sandbox
            "started_at": _now_iso(),
            "elapsed_at_start_s": round(budget.elapsed_seconds, 1),
        }
        if item.get("test_fixture"):
            rec["test_fixture"] = True   # report tags the line [fixture]; DONE skips the ledger
        state["current"] = {"id": item_id, "title": item.get("title"), "started_at": rec["started_at"]}
        _spent()
        try:
            on_state(dict(state))   # checkpoint at item START: a restart knows what was in flight
        except Exception:
            pass
        if dry_run:
            rec["outcome"] = SKIPPED
            rec["evidence"] = "dry-run: item not executed"
        else:
            try:
                result = worker(item, budget) or {}
            except Exception as exc:
                result = {"outcome": ERROR, "evidence": f"worker raised: {exc!r}"}
            outcome = result.get("outcome", ERROR)
            if outcome not in TERMINAL_OUTCOMES:
                outcome = ERROR
            rec["outcome"] = outcome
            rec["evidence"] = result.get("evidence")
            for k in ("adversarial", "tests", "diff_summary", "timings", "attempts"):
                if k in result:
                    rec[k] = result[k]
        rec["ended_at"] = _now_iso()
        state["items"].append(rec)
        state["current"] = None
        emit("item_end", f"{item_id} -> {rec['outcome']}: {str(rec.get('evidence') or '')[:80]}")

        # Always exclude an attempted item from re-selection (even a dry-run skip), so the loop
        # advances instead of re-picking item[0] forever.
        if item_id is not None:
            done_ids.add(item_id)

        if rec["outcome"] in PROGRESS_OUTCOMES:
            no_progress = 0
            state["done_count"] += 1
            if item_id is not None:
                completed_ids.add(item_id)   # unblocks dependents in dependency-ordered selection
            if item_id is not None and rec["outcome"] == DONE:
                if item.get("test_fixture"):
                    # Fixture DONEs are drill results, not progress: never ledgered.
                    # Thus they remain armed and re-runnable (excluded from done_ids).
                    emit("fixture_done", f"{item_id} is a test fixture -> not ledgered (re-runnable)")
                else:
                    try:
                        on_item_done(item_id, run_id)   # persistent done-ledger (never re-runs later)
                    except Exception:
                        pass
        else:
            no_progress += 1
            if rec["outcome"] == BAILED:
                state["bailed_count"] += 1

        state["scores"] = score_run(state["items"])   # #5: keep the two-axis score live per item
        _spent()
        try:
            on_state(dict(state))   # checkpoint after each item
        except Exception:
            pass

    state["ended_at"] = _now_iso()
    state["active"] = False
    state["current"] = None
    _spent()
    state["elapsed_seconds"] = round(budget.elapsed_seconds, 1)
    state["scores"] = score_run(state["items"])   # #5: final two-axis score
    emit("stop", f"{state['stop_reason']} (done={state['done_count']} bailed={state['bailed_count']} "
                 f"elapsed={state['elapsed_seconds']}s)")
    try:
        on_state(dict(state))
    except Exception:
        pass
    return state


def should_fire(request, *, now, busy, energy_ok, has_items, in_cheapest_window=None):
    """Phase 7B scheduler decision (pure/testable): should a queued 'run-when-cheap' request fire now?
    Returns (fire: bool, reason: str). The scheduler loop polls this; it fires the moment the price
    drops to cheap AND nothing's running AND there's approved work, or gives up at the expiry.
      now/expires_at are epoch seconds. energy_ok is the energy gate's current verdict.
      in_cheapest_window (bool | None): when the request opts into 'prefer_cheapest' and this is
        False, defer with 'await_cheapest' (hold for the day's trough). None/absent = unchanged."""
    if not request:
        return False, "no_request"
    exp = request.get("expires_at")
    if exp is not None and now >= exp:
        return False, "expired"
    # not_before: hold until a requested start time (epoch). Lets a caller schedule a specific launch
    # (e.g. "9pm") rather than only "when cheap". Independent of energy/prefer_cheapest.
    nb = request.get("not_before")
    if nb is not None and now < nb:
        return False, "await_time"
    if busy:
        return False, "busy"
    if not has_items:
        return False, "no_eligible_items"
    if not energy_ok:
        return False, "waiting_for_cheap"
    # Opt-in "prefer cheapest" mode: even when the price is under the ceiling, hold until we are inside
    # the cheapest upcoming window (avoids firing at a mild day's peak). Only an explicit False defers;
    # None (couldn't compute the window) fires as before.
    if request.get("prefer_cheapest") and in_cheapest_window is False:
        return False, "await_cheapest"
    return True, "fire"


def _icw(curve, hours_needed):
    """Is the current hour inside the cheapest upcoming window of length *hours_needed*? Returns
    True/False, or None when the window can't be computed (no curve / no current hour). The caller
    passes the result into should_fire's in_cheapest_window. Pure (the energy import is local so this
    module has no hard energy dependency)."""
    if not curve or curve.get("current_hour") is None:
        return None
    from energy import cheapest_window as _cw
    w = _cw(curve, max(1, int(hours_needed)))
    if not w:
        return None
    ch = curve["current_hour"]
    return w["start_hour"] <= ch <= w["end_hour"]


def done_ids_from_state(state) -> set:
    """Item ids already attempted in a (possibly crashed) run -- the resume exclusion set."""
    return {r.get("id") for r in (state or {}).get("items", []) if r.get("id") is not None}


def is_incomplete(state) -> bool:
    """A run-state left mid-flight by a crash (still active, or no terminal stop_reason) -- resumable."""
    if not isinstance(state, dict):
        return False
    return bool(state.get("active")) or not state.get("stop_reason")


def commission_rollup(items) -> list[str]:
    """Per-commission done/total rollup for items that carry a parent_id (a promoted commission's
    leaves), so the morning report shows commission-level progress, not just a flat leaf list. Empty
    when no item has a parent. Pure + testable."""
    groups: dict[str, dict] = {}
    order: list[str] = []
    for r in items or []:
        pid = r.get("parent_id")
        if not pid:
            continue
        if pid not in groups:
            groups[pid] = {"done": 0, "total": 0}
            order.append(pid)
        groups[pid]["total"] += 1
        if r.get("outcome") == DONE:
            groups[pid]["done"] += 1
    lines = []
    for pid in order:
        g = groups[pid]
        done_all = g["done"] == g["total"]
        lines.append(f"- commission `{pid}`: {g['done']}/{g['total']} leaves done"
                     + ("" if done_all else " (incomplete)"))
    return lines


def _fmt_timings(timings):
    """Format a timings dict into a compact summary line.

    Groups the detailed stage names into three buckets:
      - drive (drive, verify)
      - critique (critique, recritique)
      - fixes (fix, dispute)

    Returns a string like 'drive 7.7m | critique 6.2m | fixes 4.1m' or empty if absent.
    """
    if not timings or not isinstance(timings, dict):
        return ""
    drive_s = timings.get("drive", 0.0) + timings.get("verify", 0.0)
    critique_s = timings.get("critique", 0.0) + timings.get("recritique", 0.0)
    fixes_s = timings.get("fix", 0.0) + timings.get("dispute", 0.0)
    parts = []
    if drive_s > 0:
        parts.append(f"drive {drive_s / 60:.1f}m")
    if critique_s > 0:
        parts.append(f"critique {critique_s / 60:.1f}m")
    if fixes_s > 0:
        parts.append(f"fixes {fixes_s / 60:.1f}m")
    return " | ".join(parts) if parts else ""


def render_run_summary(state) -> list[str]:
    """Markdown lines summarizing ONE run from its durable state (facts only -- the defect-#2
    discipline: a narrated report would lie). Used by the morning report."""
    if not isinstance(state, dict):
        return ["- (no run state)"]
    items = state.get("items") or []
    head = (f"### {state.get('run_id', '?')} "
            f"[{'ACTIVE/incomplete' if is_incomplete(state) else state.get('stop_reason')}]"
            f" - {state.get('done_count', 0)} done, {state.get('bailed_count', 0)} bailed,"
            f" {len(items)} attempted"
            + (f", {state.get('elapsed_seconds')}s" if state.get("elapsed_seconds") is not None else "")
            + (" (dry-run)" if state.get("dry_run") else ""))
    lines = [head]
    s = state.get("scores") or score_run(items)   # #5: coding-correctness vs review-convergence, separate
    cod, rev = s.get("coding") or {}, s.get("review") or {}
    lines.append(
        f"**Scores** - coding {cod.get('correct', 0)}/{s.get('items_executed', 0)} verified"
        + (f" ({cod['hollow']} hollow)" if cod.get("hollow") else "")
        + f" · review {rev.get('converged', 0)} converged / {rev.get('flagged_for_human', 0)} flagged"
        f" / {rev.get('reverted', 0)} reverted")
    rollup = commission_rollup(items)
    if rollup:
        lines += ["**Commissions:**"] + rollup
    skipped = state.get("skipped_armed") or []
    if skipped:
        ids_str = ", ".join(str(s["id"]) for s in skipped)
        lines.append(f"**Skipped (already done):** {ids_str}")
    for r in items[:25]:
        adv = r.get("adversarial") or {}
        adv_note = f" · adv:{adv.get('verdict')}" if adv else ""
        rb = adv.get("restored_to_best")
        if rb:
            adv_note += f" (kept best: round {rb.get('best_round')} @ {rb.get('best_findings')} findings)"
        if adv.get("authorized_test_edits"):
            adv_note += f" · {len(adv['authorized_test_edits'])} authorized test edit(s)"
        ev = str(r.get("evidence") or "").replace("\n", " ")
        if len(ev) > 90:
            ev = ev[:87] + "..."
        timings_note = _fmt_timings(r.get("timings"))
        risk = adv.get("risk") if isinstance(adv, dict) else None
        risk_note = f" | risk:{risk['tier']}" if isinstance(risk, dict) and risk.get("tier") else ""
        esc = adv.get("escalated") if isinstance(adv, dict) else None
        esc_note = ""
        if isinstance(esc, dict) and esc.get("from") and esc.get("to"):
            esc_note = f" \u00b7 esc:{esc['from']}->@{esc['to']}"
        sn = adv.get("scope_note") if isinstance(adv, dict) else None
        # scope_note is populated by the B1.2 coverage gate in overnight_worker:
        # e.g. ["drive_tele", "coverage_gate"] when spec symbols can't be found
        # in production code despite a clean-converged adversarial review.
        sn_note = ""
        if isinstance(sn, list) and sn:
            sn_note = f" · scope-note:{','.join(str(s) for s in sn)}"
        dt = r.get("timings") if isinstance(r.get("timings"), dict) else {}
        dtele = dt.get("drive_tele") if isinstance(dt, dict) else None
        nudge_note = ""
        if isinstance(dtele, dict):
            fired = [lbl for lbl, key in (("idle", "nudges_idle"), ("idle-reset", "idle_resets"), ("deadline", "nudges_deadline"),
                                          ("soft", "nudges_soft"), ("hard", "nudges_hard"))
                     if dtele.get(key)]
            if fired:
                nudge_note = " · nudged:" + "+".join(fired)
        fixture_tag = " [fixture]" if r.get("test_fixture") else ""
        lines.append(f"- `{str(r.get('id') or '')[:16]}`{fixture_tag} **{r.get('outcome')}**{adv_note}{timings_note}{risk_note}{esc_note}{sn_note}{nudge_note} - {ev}")
    return lines


def _valid_discord_user_id(user_id: str) -> bool:
    """Pure validation: is *user_id* a plausible Discord snowflake ID?

    Discord user IDs are base-10 integers in the 17–20 digit range.
    Returns False for empty, whitespace-only, non-numeric, or out-of-range strings."""
    if not isinstance(user_id, str):
        return False
    stripped = user_id.strip()
    if not stripped:
        return False
    if not stripped.isdigit():
        return False
    return 17 <= len(stripped) <= 20


def render_run_dm(state) -> str:
    """Compact DM text summarizing ONE overnight run (pure helper, no I/O).

    Returns "" for an empty dict or None state. Otherwise returns a multi-line string with:
      - run_id, stop_reason, done/bailed counts, elapsed seconds
      - one line per item (id + outcome), capped at 8 items
    """
    if not isinstance(state, dict):
        return ""
    items = state.get("items") or []
    if not state:
        return ""
    lines = [
        f"Overnight run {state.get('run_id', '?')} — {state.get('stop_reason')}",
        f"{state.get('done_count', 0)} done, {state.get('bailed_count', 0)} bailed",
    ]
    elapsed = state.get("elapsed_seconds")
    if elapsed is not None:
        lines.append(f"Elapsed: {elapsed}s")
    for r in items[:8]:
        outcome = r.get("outcome", "?")
        item_id = str(r.get("id") or "")[:16]
        lines.append(f"  {item_id}: {outcome}")
    return "\n".join(lines)


def suite_mutations(pre, post) -> list[str]:
    """Return sorted relative paths of non-test tracked source files that changed, appeared, or vanished between `pre` and `post` SHA-256 maps.

    Tests (`tests/` prefix) are excluded; their integrity is handled by `detect_test_tampering`. Only TRACKED files are fingerprinted; new untracked files are invisible here but caught elsewhere."""
    pre = pre or {}
    post = post or {}
    changed = []
    for rel in set(pre) | set(post):
        r = str(rel)
        if r.startswith("tests/") or "/tests/" in r:
            continue
        if pre.get(rel) != post.get(rel):
            changed.append(r)
    return sorted(changed)


def render_start_dm(run_id, eligible_count, max_seconds) -> str:
    """Returns a one-line notification message for the admin DM.
    This function is pure (no I/O) and is invoked only for actual runs
    to confirm execution, distinct from scheduled-but-idle states."""
    try:
        hours = round(float(max_seconds) / 3600.0, 1)
    except (TypeError, ValueError):
        hours = 0.0
    return (f"Overnight run {run_id} STARTED — {int(eligible_count)} eligible item(s), "
            f"{hours}h cap. Report DM follows when it finishes.")


def build_adjudication_request(state) -> dict | None:
    """Build a bail-adjudication request from a FINISHED run state, or None when nothing bailed.

    Selects items flagged for `bail_human` (critic-churn/hallucination) or test tampering,
    requiring external adjudication to distinguish real defects from hallucinated findings.
    The caller writes this payload; an external agent inspects the sandbox git history and
    writes `<run_id>.adjudication.md` back. Read-only by design: the verdict informs manual review,
    it does not modify code or the backlog."""
    if not isinstance(state, dict) or is_incomplete(state):
        return None
    flagged = []
    for r in (state.get("items") or []):
        adv = r.get("adversarial") or {}
        if adv.get("verdict") == "bail_human" or adv.get("test_tampering"):
            flagged.append({
                "id": r.get("id"),
                "title": r.get("title"),
                "workspace": r.get("workspace"),
                "outcome": r.get("outcome"),
                "verdict": adv.get("verdict"),
                "reason": adv.get("reason"),
                "findings_outstanding": adv.get("findings_outstanding") or [],
                "disputes": adv.get("disputes") or [],
                "restored_to_best": adv.get("restored_to_best"),
                "test_tampering": adv.get("test_tampering"),
            })
    if not flagged:
        return None
    return {
        "run_id": state.get("run_id"),
        "ended_at": state.get("ended_at"),
        "stop_reason": state.get("stop_reason"),
        "workspace_root": (config.STACK_DIR.rstrip("/") + "/coding-workspaces") if config.STACK_DIR else "",
        "items": flagged,
        "ask": (
            "For each item: on the head, inspect the workspace git under workspace_root (baseline vs the "
            "kept checkpoint), and adjudicate every outstanding finding as REAL (cite the offending "
            "lines) or HALLUCINATED (show the code contradicting the claim). State whether the kept "
            "code actually satisfies the item's acceptance. Do NOT change any code or backlog state."
        ),
    }
