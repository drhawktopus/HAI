"""Self-cascading NI review campaign chain.

Design: Native-Intelligence-Review-Campaign-Machinery §Recommended design. The human gate
is the kickoff confirm (run_review_campaign); from there the chain advances itself on
order-completion receipts — extract → parallel review shares → merge → residual escalation
(two top-tier attempts, then auto-exclude) → findings — and stops fail-closed on any step
failure or unreadable state. It never loops past the exclude run.

Public surface:
    make_block(...) -> dict           # campaign block, stamped on decision-point specs only
    on_order_result(job, ok) -> None  # NEVER raises; catches everything, notes.log
"""
import json
import pathlib

import notes


def make_block(campaign_id, mode, work_dir, user, step, tier=0, top_attempts=0):
    """Return the campaign block dict.

    The block is stamped on decision-point specs only (extract solo order; the merge STEP
    of a steps order; the residual review; the exclude run; the findings order) — never on
    the parallel review shares, so the hook fires once per decision, not once per host.
    """
    return {
        "id": campaign_id,
        "mode": mode,
        "work_dir": work_dir,
        "user": user,
        "step": step,
        "tier": tier,
        "top_attempts": top_attempts,
    }


def _derive_paths(work_dir, mode):
    """Return the chain's 7 file paths (the NI corpus naming convention).

    work_dir is the UNC-based work directory.  mode is 'positive' or 'corrective'.
    Returns a dict with keys:
        work, out, shares_glob, residual_work, residual_out, findings_md, findings_json
    """
    stem = "%s.review" % mode
    return {
        "work":           "%s\\%s.work.json"         % (work_dir, mode),
        "out":            "%s\\%s.review.json"       % (work_dir, mode),
        "shares_glob":    "%s\\%s.share-*.json"      % (work_dir, stem),
        "residual_work":  "%s\\%s.residual.work.json"   % (work_dir, stem),
        "residual_out":   "%s\\%s.residual.review.json" % (work_dir, stem),
        "findings_md":    "%s\\FINDINGS-%s.md"             % (work_dir, mode),
        "findings_json":  "%s\\%s.findings.json"          % (work_dir, mode),
    }


def _read_work_items(path):
    """Read a work file at *path* and return the list of non-control item ids.

    Returns [] when the file is missing, unreadable, or malformed (never raises).
    The path may be a container-side path (str or Path) pointing into the
    library-triage mount.
    """
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
    except (OSError, json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return []
    ids = []
    for item in items:
        if isinstance(item, dict) and not item.get("control"):
            item_id = item.get("id")
            if item_id is not None:
                ids.append(item_id)
    return ids


def _read_out_status(path):
    """Read a review/residual out file at *path* and return its status string.

    Returns 'COMPLETE', 'INCOMPLETE', or None (missing/unreadable/malformed).
    The path may be a container-side path (str or Path) pointing into the
    library-triage mount.
    """
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        status = data.get("status") if isinstance(data, dict) else None
        if isinstance(status, str):
            return status
    except (OSError, json.JSONDecodeError, TypeError, UnicodeDecodeError):
        pass
    return None


def on_order_result(job, ok):
    """Handle a factory order result for campaign-ordered jobs.

    Fail-soft: NEVER raises.  Catches all exceptions internally and logs them
    so a campaign bug can never break the receipt path for ordinary orders.

    *job* is the queue record dict from p20_queue.get().  It carries the composed
    spec verbatim under job['spec'].  *ok* is True when the status was 'done'.
    """
    try:
        spec = job.get("spec") if isinstance(job, dict) else None
        if not isinstance(spec, dict):
            return
        campaign = spec.get("campaign")
        if not isinstance(campaign, dict):
            return

        # fields from the campaign block
        campaign_id = campaign.get("id", "unknown")
        mode = campaign.get("mode", "corrective")
        work_dir = campaign.get("work_dir", "")
        user = campaign.get("user", "")
        step = campaign.get("step", "")
        top_attempts = campaign.get("top_attempts", 0)

        # ok=False — any step failure stops the chain (fail-closed)
        if not ok:
            notes.log(
                "NI CAMPAIGN %s STOPPED (fail-closed): step %s failed"
                % (campaign_id, step))
            return

        # --- extract ---------------------------------------------------------
        if step == "extract":
            import tools as _tools
            mount = _tools._triage_container_path(work_dir)
            if mount is None:
                notes.log(
                    "NI CAMPAIGN %s STOPPED (fail-closed): step extract — "
                    "triage mount absent for work_dir %s"
                    % (campaign_id, work_dir))
                return
            # Container-side work file path
            paths = _derive_paths(work_dir, mode)
            c_work = _tools._triage_container_path(paths["work"])
            if c_work is None or not pathlib.Path(c_work).exists():
                notes.log(
                    "NI CAMPAIGN %s STOPPED (fail-closed): step extract — "
                    "work file missing"
                    % campaign_id)
                return
            items = _read_work_items(c_work)
            if not items:
                notes.log(
                    "NI CAMPAIGN %s STOPPED (fail-closed): step extract — "
                    "work file has no non-control items"
                    % campaign_id)
                return
            if not _submit_review_chain(campaign_id, mode, work_dir, user, items):
                # builder already logged refusal details
                return

        # --- merge -----------------------------------------------------------
        elif step == "merge":
            import tools as _tools
            mount = _tools._triage_container_path(work_dir)
            if mount is None:
                notes.log(
                    "NI CAMPAIGN %s STOPPED (fail-closed): step merge — "
                    "triage mount absent"
                    % campaign_id)
                return
            paths = _derive_paths(work_dir, mode)
            c_out = _tools._triage_container_path(paths["out"])
            status = _read_out_status(c_out) if c_out else None
            if status == "COMPLETE":
                _submit_findings(campaign_id, mode, work_dir, user)
                return
            # Residual work exists — escalate to top tier (first attempt)
            c_resid_work = _tools._triage_container_path(paths["residual_work"])
            if c_resid_work and pathlib.Path(c_resid_work).exists():
                _submit_residual_review(
                    campaign_id, mode, work_dir, user, top_attempts=1)
                return
            # Broken state — neither complete nor residual; do not guess
            notes.log(
                "NI CAMPAIGN %s STOPPED (fail-closed): step merge — "
                "broken state, no complete review and no residual work"
                % campaign_id)
            return

        # --- residual-review -------------------------------------------------
        elif step == "residual-review":
            import tools as _tools
            mount = _tools._triage_container_path(work_dir)
            if mount is None:
                notes.log(
                    "NI CAMPAIGN %s STOPPED (fail-closed): step "
                    "residual-review — triage mount absent"
                    % campaign_id)
                return
            paths = _derive_paths(work_dir, mode)
            c_resid_out = _tools._triage_container_path(paths["residual_out"])
            status = _read_out_status(c_resid_out) if c_resid_out else None
            if status == "COMPLETE":
                _submit_findings(campaign_id, mode, work_dir, user)
                return
            if status == "INCOMPLETE" and top_attempts < 2:
                # Retry with top_attempts bumped to 2
                _submit_residual_review(
                    campaign_id, mode, work_dir, user, top_attempts=2)
                return
            if status == "INCOMPLETE" and top_attempts >= 2:
                # Compute missing ids: non-control work items minus out rows
                c_resid_work = _tools._triage_container_path(
                    paths["residual_work"])
                work_ids = set(_read_work_items(c_resid_work)
                              if c_resid_work else [])
                # Row ids from the residual out file
                row_ids = set()
                try:
                    if c_resid_out:
                        data = json.loads(
                            pathlib.Path(c_resid_out).read_text(
                                encoding="utf-8"))
                        # review outs keep rows under "results" ("items" is the
                        # WORK-file key — conflating them excludes grounded rows)
                        rows = data.get("results") if isinstance(data, dict) else []
                        if isinstance(rows, list):
                            for r in rows:
                                if isinstance(r, dict) and r.get("id") is not None:
                                    row_ids.add(r["id"])
                except (OSError, json.JSONDecodeError, TypeError, UnicodeDecodeError):
                    pass
                missing = sorted(work_ids - row_ids)
                reason = (
                    "terminal: top-tier quote-grounding refused twice "
                    "(campaign.step auto-exclude)")
                _submit_exclude_run(
                    campaign_id, mode, work_dir, user, missing, reason)
                return
            # Unknown status — stop
            notes.log(
                "NI CAMPAIGN %s STOPPED (fail-closed): step "
                "residual-review — unknown out status %r"
                % (campaign_id, status))
            return

        # --- exclude-run -----------------------------------------------------
        elif step == "exclude-run":
            import tools as _tools
            mount = _tools._triage_container_path(work_dir)
            if mount is None:
                notes.log(
                    "NI CAMPAIGN %s STOPPED (fail-closed): step "
                    "exclude-run — triage mount absent"
                    % campaign_id)
                return
            paths = _derive_paths(work_dir, mode)
            c_resid_out = _tools._triage_container_path(paths["residual_out"])
            status = _read_out_status(c_resid_out) if c_resid_out else None
            if status == "COMPLETE":
                _submit_findings(campaign_id, mode, work_dir, user)
                return
            # Still incomplete after exclude — never loop
            notes.log(
                "NI CAMPAIGN %s STOPPED: exclude run did not converge"
                % campaign_id)
            return

        # --- findings --------------------------------------------------------
        elif step == "findings":
            notes.log("NI CAMPAIGN %s COMPLETE: findings written" % campaign_id)
            return

        # Unknown step — no-op rather than raising
    except Exception as exc:
        notes.log("NI CAMPAIGN ERROR (fail-soft): %s" % exc)


# ---------------------------------------------------------------------------
# Order submission builders \u2014 each composes its spec and calls app.p20_order
# (imported inside each function to avoid circular imports).
# ---------------------------------------------------------------------------

def _submit_review_chain(campaign_id, mode, work_dir, user, items):
    """Submit the review+merge steps order (parallel shares, then merge).

    Composes a two-step order: parallel shares across non-control items, then merge.
    Only the merge step carries the campaign block (the hook fires once per decision).

    Returns the p20_order result dict when accepted, None when refused (refusals logged).
    """
    import app
    import tools

    paths = _derive_paths(work_dir, mode)
    work = paths["work"]
    out = paths["out"]

    review_entry = tools.COMPUTE_PAYLOADS[tools._REVIEW_CAMPAIGN_REVIEW]
    review_requires = list(review_entry["requires"])
    review_load = review_entry["load"]

    order = {
        "name": "%s-rev" % campaign_id,
        "mode": "compute",
        "load": review_load,
        "payload": "{FOUNDRY_ROOT}\\bin\\native_history_review.py",
        "args": [work, out],
        "notify": "failures",
        "requires": list(review_requires),
        "steps": [
            {
                "name": "shares",
                "parallel": True,
                "items": list(items),
            },
            {
                "name": "merge",
                "after": "shares",
                "load": "cpu",
                "args": [work, out, "--merge"],
                "campaign": make_block(
                    campaign_id=campaign_id,
                    mode=mode,
                    work_dir=work_dir,
                    user=user,
                    step="merge",
                ),
            },
        ],
    }

    result = app.p20_order(app.FactoryOrderReq(
        spec=order,
        requires=list(review_requires),
        parallel=False,
        items=[],
    ))
    if not result.get("ok"):
        why = "; ".join(str(w) for w in (result.get("why") or [])[:3])
        notes.log("NI CAMPAIGN %s STOPPED: review chain refused — %s" % (campaign_id, why))
        return None
    return result


def _submit_findings(campaign_id, mode, work_dir, user):
    """Submit the findings order (single, cpu).

    Returns the p20_order result dict when accepted, None when refused (refusals logged).
    """
    import app
    import tools

    paths = _derive_paths(work_dir, mode)

    findings_entry = tools.COMPUTE_PAYLOADS["native_history_findings"]
    findings_requires = list(findings_entry["requires"])

    order = {
        "name": "%s-findings" % campaign_id,
        "mode": "compute",
        "load": findings_entry["load"],
        "payload": "{FOUNDRY_ROOT}\\bin\\native_history_findings.py",
        "args": [
            work_dir,
            "%s.review" % mode,
            "--mode", mode,
            "--out-md", paths["findings_md"],
            "--out-json", paths["findings_json"],
        ],
        "notify": "failures",
        "requires": list(findings_requires),
        "campaign": make_block(
            campaign_id=campaign_id,
            mode=mode,
            work_dir=work_dir,
            user=user,
            step="findings",
        ),
    }

    result = app.p20_order(app.FactoryOrderReq(
        spec=order,
        requires=list(findings_requires),
        parallel=False,
        items=[],
    ))
    if not result.get("ok"):
        why = "; ".join(str(w) for w in (result.get("why") or [])[:3])
        notes.log("NI CAMPAIGN %s STOPPED: findings refused — %s" % (campaign_id, why))
        return None
    return result


def _submit_residual_review(campaign_id, mode, work_dir, user, top_attempts):
    """Submit the residual review order (single, pinned to the top tier).

    Returns the p20_order result dict when accepted, None when refused (refusals logged).
    """
    import app
    import tools

    paths = _derive_paths(work_dir, mode)
    residual_work = paths["residual_work"]
    residual_out = paths["residual_out"]

    review_entry = tools.COMPUTE_PAYLOADS[tools._REVIEW_CAMPAIGN_REVIEW]
    requires = list(review_entry["requires"]) + ["triage.ctx.65536"]

    order = {
        "name": "%s-resid%d" % (campaign_id, top_attempts),
        "mode": "compute",
        "load": review_entry["load"],
        "payload": "{FOUNDRY_ROOT}\\bin\\native_history_review.py",
        "args": [residual_work, residual_out],
        "notify": "failures",
        "requires": list(requires),
        "campaign": make_block(
            campaign_id=campaign_id,
            mode=mode,
            work_dir=work_dir,
            user=user,
            step="residual-review",
            tier=1,
            top_attempts=top_attempts,
        ),
    }

    result = app.p20_order(app.FactoryOrderReq(
        spec=order,
        requires=list(requires),
        parallel=False,
        items=[],
    ))
    if not result.get("ok"):
        why = "; ".join(str(w) for w in (result.get("why") or [])[:3])
        notes.log("NI CAMPAIGN %s STOPPED: residual review refused — %s" % (campaign_id, why))
        return None
    return result


def _submit_exclude_run(campaign_id, mode, work_dir, user, missing_ids, reason):
    """Submit the exclude run order.

    Like the residual review but extends args with --exclude <id> pairs and --exclude-reason.

    Returns the p20_order result dict when accepted, None when refused (refusals logged).
    """
    import app
    import tools

    paths = _derive_paths(work_dir, mode)
    residual_work = paths["residual_work"]
    residual_out = paths["residual_out"]

    review_entry = tools.COMPUTE_PAYLOADS[tools._REVIEW_CAMPAIGN_REVIEW]
    requires = list(review_entry["requires"]) + ["triage.ctx.65536"]

    args = [residual_work, residual_out]
    for mid in missing_ids:
        args.extend(["--exclude", str(mid)])
    args.extend(["--exclude-reason", reason])

    order = {
        "name": "%s-exclude" % campaign_id,
        "mode": "compute",
        "load": review_entry["load"],
        "payload": "{FOUNDRY_ROOT}\\bin\\native_history_review.py",
        "args": args,
        "notify": "failures",
        "requires": list(requires),
        "campaign": make_block(
            campaign_id=campaign_id,
            mode=mode,
            work_dir=work_dir,
            user=user,
            step="exclude-run",
            tier=1,
            top_attempts=2,
        ),
    }

    result = app.p20_order(app.FactoryOrderReq(
        spec=order,
        requires=list(requires),
        parallel=False,
        items=[],
    ))
    if not result.get("ok"):
        why = "; ".join(str(w) for w in (result.get("why") or [])[:3])
        notes.log("NI CAMPAIGN %s STOPPED: exclude run refused — %s" % (campaign_id, why))
        return None
    return result
