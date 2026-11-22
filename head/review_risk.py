"""D1 risk-tiered review (density phase): a MECHANICAL, zero-model-cost risk score per item that
chooses how much of the adversarial ladder to run. It changes only the TIER DEPTH — never a critic
prompt, the evidence anchor, disputes, materiality, the tamper guard, the acceptance-delta guard, or
the human deploy gate. The full ladder stays the default for anything non-trivial, and the
invariant surface is a hard floor to `high` regardless of size.

Design: 09-DENSITY-PLAN.md. Wired in app._ov_review, gated by config.ADVERSARY_RISK_TIERING.
Pure + unit-tested (test_review_risk.py). Safe direction of error = OVER-classify (more review):
an unparseable diff or an invariant-surface touch always returns `high`.
"""
from __future__ import annotations

import review_context

# Invariant surface: SPECIFIC identifiers/filenames that indicate auth / identity / approval / deploy /
# secrets code (the HAI-INVARIANTS.md surface). Kept specific on purpose — a change here ALWAYS gets the
# full ladder, so common words ("token", "admin") are excluded to avoid sending every diff to `high` and
# nullifying the speedup. Add markers as the invariant surface grows.
_INVARIANT_FILE_MARKERS = (
    "cancellation.py",      # QueryRun registry + identity lock
    "hostops_deploy",       # self-deploy executor
    "hostops_sidecar",      # privileged docker broker
    "action_review",        # write-action approval gate
)
_INVARIANT_CONTENT_MARKERS = (
    "x-hai-user", "_gated_identity", "_is_locked", "register_with_user", "get_latest_for_user",
    "self_deploy", "deploy_promote", "/run/secrets", "_secret_value", "confirm_action",
    "admin_users", "external_choice", "approved_overnight",
)


def _changed_line_counts(diff: str) -> tuple[int, int]:
    """(added, removed) real content lines — excludes the +++/--- file headers."""
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def _is_test_file(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return base.startswith("test_") or base.endswith("_test.py") or "/tests/" in ("/" + path)


def _core_changed_lines(diff: str) -> int:
    """Added+removed content lines in NON-TEST files only. Test size does not reflect production risk
    (a test can't break prod), and every test-verifiable item carries a MANDATORY test (~10-15 lines)
    — counting those made the LOW tier effectively unreachable (real finding, run ovr-20260707T083146Z:
    an ~8-line source change scored MEDIUM because its test pushed the diff to 20 lines). Sizing on
    source lines makes the tier reflect the risk of the change, not the heft of its test."""
    total = 0
    cur_is_test = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            # 'diff --git a/<path> b/<path>' — classify by the b-side path.
            cur_is_test = _is_test_file(parts[-1][2:]) if len(parts) >= 4 else False
            continue
        if cur_is_test:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            total += 1
        elif line.startswith("-") and not line.startswith("---"):
            total += 1
    return total


def _invariant_surface_hits(files: list[str], diff: str) -> list[str]:
    """Ordered, de-duplicated markers (filenames or added-line identifiers) that put this diff on the
    invariant surface. Only ADDED lines are scanned (a removal doesn't newly touch the surface)."""
    hits: list[str] = []
    for f in files:
        fl = f.lower()
        for m in _INVARIANT_FILE_MARKERS:
            if m in fl:
                hits.append(f)
                break
    added_text = "\n".join(
        line[1:].lower() for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++"))
    for m in _INVARIANT_CONTENT_MARKERS:
        if m in added_text:
            hits.append(m)
    seen: set = set()
    out: list[str] = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def score(item, diff, *, low_max_lines: int = 15, low_max_files: int = 2,
          high_min_lines: int = 120, high_min_files: int = 5) -> tuple[str, list[str]]:
    """Return ("low"|"medium"|"high", reasons). Deterministic; no model calls, no I/O.

    Order (first match wins, safe-toward-`high`):
      0. no/unparseable diff            -> high  (can't assess -> full review)
      1. invariant-surface touch        -> high  (hard floor)
      2. human `risk:` pin of "high"    -> high  (human can raise, never lower below computed)
      3. large (lines OR files)         -> high
      4. tiny (lines AND files)         -> low
      5. otherwise                      -> medium
    """
    if not diff or not str(diff).strip():
        return "high", ["no diff available -> safe default (full review)"]
    files = review_context.changed_files(diff)
    if not files:
        return "high", ["diff has no parseable file headers -> safe default (full review)"]

    # Count only core (non-test) source lines; test changes are not production risk.
    core_lines = _core_changed_lines(diff)
    core_files = [f for f in files if not _is_test_file(f)]
    nfiles = len(core_files)

    surface = _invariant_surface_hits(files, diff)
    if surface:
        return "high", ["touches invariant surface: " + ", ".join(surface[:5])]

    if str(item.get("risk") or "").strip().lower() == "high":
        return "high", ["human risk:high pin"]

    if core_lines >= high_min_lines or nfiles >= high_min_files:
        return "high", [f"large change ({core_lines} core lines, {nfiles} source files)"]

    if core_lines <= low_max_lines and nfiles <= low_max_files:
        why = f"tests-only ({core_lines} core lines)" if not core_files \
            else f"small change ({core_lines} core lines, {nfiles} source files)"
        return "low", [why]

    return "medium", [f"normal change ({core_lines} core lines, {nfiles} source files)"]
