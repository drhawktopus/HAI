"""Scheduled sweep for reviewing CLAIMS changed in the library since last run.

Uses mtime + stored snapshot triggers to ensure reviews happen even if the
system fails to request them. Produces a worklist (post-hoc); it is not a
write gate, as RAG has already reloaded by the time this runs.

Submits one background order (`work-w1` family, urgency `background`, kind
`review`) on the `fellowship` tier to avoid interfering with live desktop
work or consuming local GPU resources. Must not disturb a person or live HAI
work.

The order's result is COLLECTED when the desk reports it done (its completion
receipt, app.p20_job_result) or at the next sweep. While one review order is
still pending the sweep does not stack another. A snapshot advances only for a
document whose review came back, so unchanged documents are never skipped.

First run establishes a baseline snapshot and reviews nothing to avoid firing
excessive calls on a cold start. Every outcome is reported (even clean ones)
so a broken sweep is distinguishable from a healthy library."""
import json
import os
import shutil
import threading
import time
import uuid

import config

try:
    import claim_review
except ImportError:                                  # never take the caller down
    claim_review = None

DIRS = [os.path.join(config.DOCS_DIR, d) for d in ("Reference", "Runbook", "LLM-Project")]
SNAP_DIR = os.path.join(config.CLAIM_REVIEW_STATE_DIR, "snapshots")
FINDINGS_DIR = os.path.join(config.CLAIM_REVIEW_STATE_DIR, "findings")
PENDING = os.path.join(config.CLAIM_REVIEW_STATE_DIR, "pending.json")
STAGED_DIR = os.path.join(config.CLAIM_REVIEW_STATE_DIR, "staged")

FAMILY, TIER, KIND = "work-w1", "fellowship", "review"
REL_DIR = "campaigns/claim-review"
# the order's files on the jobs share: Kirk's mount, the GPU factories' UNC path (the eval campaigns' share), Tolkien's
ORDER_LOCAL = os.environ.get("CLAIM_REVIEW_ORDER_LOCAL") or os.path.join(
    os.environ.get("TABFOUNDRY_SHARE", "/tabjobs"), *REL_DIR.split("/"))
ORDER_UNC = os.environ.get("CLAIM_REVIEW_ORDER_UNC") or ""
ORDER_TOLKIEN = os.environ.get("CLAIM_REVIEW_ORDER_TOLKIEN") or (
    os.environ.get("TABFOUNDRY_SHARE_FOUNDRY", "/ssdshared/tabfoundry-jobs") + "/" + REL_DIR)
PAYLOAD = "{FOUNDRY_ROOT}\\bin\\infer_ollama.py"
TERMINAL = ("done", "failed", "cancelled", "expired")

DM_URL = os.environ.get("HAWKADMIN_DM_URL", "http://hawkadminbot:8477/dm")
DM_TOKEN = config.HAWKADMIN_DM_TOKEN
DM_USER = os.environ.get("ADMIN_DISCORD_ID", "")

_LOCK = threading.RLock()              # the timer's sweep and the desk's completion receipt


def notify_desk(message, tag="docs:review"):
    """Mirrors the `notify_desk` interface in tablet_foundry and p20_foundry.
    Fail-soft: missing credentials return silently, but network failures must print to avoid silent loss."""
    print("[DESK] %s" % message.replace("\n", " | "))
    if not (DM_TOKEN and DM_USER):
        return
    try:
        import urllib.request
        req = urllib.request.Request(
            DM_URL,
            data=json.dumps({"user_id": DM_USER,
                             "message": "[%s] %s" % (tag, message)}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-DM-Token": DM_TOKEN})
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status >= 300:
                print("[DESK] relay refused: HTTP %s" % r.status)
    except Exception as exc:
        print("[DESK] relay failed: %s: %s" % (type(exc).__name__, exc))


def _snap_path(path):
    flat = path.replace(config.DOCS_DIR, "").strip("/\\").replace(os.sep, "__").replace("/", "__")
    return os.path.join(SNAP_DIR, flat)


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".incoming"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _desk():
    import app                              # the same in-process doors every order uses (fellowship_turn's precedent)
    return app


def _docs():
    docs = []
    for d in DIRS:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.endswith(".md"):
                docs.append(os.path.join(d, name))
    # Include only HAI-authored journals (-hai suffix); skip other session logs.
    jdir = os.path.join(config.DOCS_DIR, "Journal")
    if os.path.isdir(jdir):
        for name in sorted(os.listdir(jdir)):
            if name.endswith("-hai.md"):
                docs.append(os.path.join(jdir, name))
    return docs


def order_body(name, items):
    """(FactoryOrderReq kwargs, the order's file paths) for ONE review order over `items` [{"id", "doc", "prompt"}]:
    the GPU form (infer_ollama, the reviewer's instructions as each item's system prompt, its answer held to
    claim_review.SCHEMA by structured output, model and settings from the weighed row through the policy tokens) and
    the Fellowship form (the CHAT1 surface, no system prompt rides, so the
    instructions lead each task). Files are written here, under the order's own name."""
    import fellowship_turn
    day = time.strftime("%Y%m%d")
    local = os.path.join(ORDER_LOCAL, day)
    unc, tol = ORDER_UNC + "\\" + day, ORDER_TOLKIEN + "/" + day
    os.makedirs(local, exist_ok=True)
    _write(os.path.join(local, name + ".items.json"), json.dumps(
        {"items": [{"id": it["id"], "system": claim_review._SYSTEM, "prompt": it["prompt"], "format": claim_review.SCHEMA}
                   for it in items]},
        indent=1, ensure_ascii=False))
    _write(os.path.join(local, name + ".payload.py"), fellowship_turn.payload_tasks_text(
        [(it["id"], claim_review._SYSTEM + "\n\n" + it["prompt"]) for it in items]))
    gpu = {"name": name, "mode": "compute", "load": "gpu", "initiator": "hai", "payload": PAYLOAD,
           "args": [unc + "\\" + name + ".items.json", unc + "\\" + name + ".out.json",
                    "--model", "{POLICY_MODEL}", "--rounds", "1", "--num-ctx", "{POLICY_NUM_CTX}",
                    "{POLICY_TEMPERATURE_ARGS}", "{POLICY_THINK_ARGS}", "{POLICY_OFFLOAD_ARGS}"],
           "progress_glob": unc + "\\" + name + ".out.progress\\*.done", "progress_total": len(items),
           "notify": "failures"}
    fel = {"kind": "llm", "name": name, "payload": tol + "/" + name + ".payload.py",
           "tasks": [it["id"] for it in items], "replicates": 1, "notify": "failures",
           "deliver_to": tol + "/" + name + ".results.json", "initiator": "hai",
           "request": "documentation claim review: %d changed doc(s)" % len(items)}
    body = {"spec": gpu, "requires": ["order.compute"], "kind": KIND, "family": FAMILY, "tier": TIER,
            "urgency": "background", "fellowship": {"spec": fel, "requires": ["fellowship.placed"]}}
    paths = {"out": os.path.join(local, name + ".out.json"), "results": os.path.join(local, name + ".results.json")}
    return body, paths


def submit(items, desk=None):
    """-> (order id, paths, where) or (None, why, None). A HOLD is kept: background work waits for a box at the desk
    (the desk places it when one is free) - unlike a chat turn, nothing here routes around it."""
    app = desk or _desk()
    name = "claim-review-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:4])
    body, paths = order_body(name, items)
    try:
        reply = app.p20_order(app.FactoryOrderReq(**body))
    except Exception as exc:                                        # noqa: BLE001
        return None, "the desk refused the review order: %r" % (exc,), None
    if reply.get("ok") and reply.get("held"):
        return reply.get("id"), paths, "held at the desk (%s)" % "; ".join(reply.get("why") or [])[:200]
    steps = [s for s in (reply.get("steps") or []) if isinstance(s, dict) and s.get("id")]
    if not reply.get("ok") or len(steps) != 1:
        return None, "the desk refused the review order: %s" % (
            reply.get("error") or "; ".join(reply.get("why") or []) or str(reply)[:200]), None
    return steps[0]["id"], paths, "placed on %s" % steps[0].get("host")


def _rows(pend, view):
    """-> (rows, why, missing, who): {item id: (answer, thinking)} from the order's delivery, or None with why. The
    Fellowship's results carry the run's control (a failed control voids the delivery); the GPU form's out.json one row
    per item. `missing`: the file is not on the share (yet) - the one case worth a second look. `who`: the worker and
    model the delivery itself names (what was served, never what was asked for)."""
    host = view.get("host") or "?"
    if host == "fellowship":
        res = _read_json(pend["paths"]["results"])
        if res is None:
            return None, "no results on the share", True, host
        if not res.get("control_passed"):
            return None, "the run's control did not pass", False, host
        rows = [r for r in res.get("rows") or [] if r.get("task")]
        workers = sorted({str(r.get("_host") or r.get("node") or "") for r in rows} - {""})
        who = "the Fellowship (%s)" % ", ".join(workers) if workers else "the Fellowship"
        return {r["task"]: (r.get("answer") or "", "") for r in rows}, None, False, who
    out = _read_json(pend["paths"]["out"])
    if out is None:
        return None, "no out.json on the share", True, host
    model = out.get("served") or out.get("model")
    who = "%s %s" % (host, model) if isinstance(model, str) and model else host
    return {r.get("id"): (r.get("answer") or "", r.get("thinking") or "")
            for r in out.get("rows") or [] if r.get("id") and not r.get("error")}, None, False, who


def _clear(pend):
    try:
        os.remove(PENDING)
    except OSError:
        pass
    shutil.rmtree(os.path.join(STAGED_DIR, pend.get("name") or "-"), ignore_errors=True)


def collect(desk=None):
    """Read the pending review order's fate from the desk and act on it. -> None (nothing pending), {"pending": status}
    (still at the desk), or the outcome. Never raises."""
    with _LOCK:
        pend = _read_json(PENDING)
        if not pend:
            return None
        app = desk or _desk()
        try:
            view = app._order_status_view(pend["id"]) or {}
        except Exception as exc:                                    # noqa: BLE001 - no verdict: keep it pending
            return {"pending": "unknown", "why": "could not read the order: %r" % (exc,), "id": pend["id"]}
        st = view.get("status")
        if not view.get("ok", True) and st is None:
            notify_desk("review order %s is gone from the desk (%s) - its %d doc(s) stay for the next sweep."
                        % (pend.get("name"), view.get("error") or "no record", len(pend.get("docs") or [])))
            _clear(pend)
            return {"lost": True}
        if st not in TERMINAL:
            return {"pending": st or "unknown", "id": pend["id"], "name": pend.get("name")}
        if st != "done":
            notify_desk("review order %s ended %s%s - nothing was reviewed; its %d doc(s) stay for the next sweep. "
                        "This is NOT a clean result." % (pend.get("name"), st, (": %s" % view.get("reason"))
                                                         if view.get("reason") else "", len(pend.get("docs") or [])))
            _clear(pend)
            return {"ended": st}
        rows, why, missing, where = _rows(pend, view)
        if rows is None:
            if missing and not pend.get("missing_seen"):
                # the delivery can trail the receipt on the share: look again at the next receipt or sweep, once
                pend["missing_seen"] = time.strftime("%Y-%m-%d %H:%M")
                _write(PENDING, json.dumps(pend, indent=1))
                return {"pending": "delivery not on the share yet", "id": pend["id"]}
            notify_desk("review order %s is done but delivered nothing usable (%s) - its %d doc(s) stay for the next "
                        "sweep. This is NOT a clean result." % (pend.get("name"), why, len(pend.get("docs") or [])))
            _clear(pend)
            return {"undelivered": why}
        findings, reviewed, unread = [], [], []
        for d in pend.get("docs") or []:
            got = rows.get(d["id"])
            res = claim_review.parse_answer(*got) if got else None
            if res is None:
                unread.append(d["doc"])
                continue
            reviewed.append(d["doc"])
            for item in res.get("unbacked", []):
                findings.append({"doc": d["doc"], **item})
            staged = os.path.join(STAGED_DIR, pend["name"], d["id"])
            text = _read(staged)
            if text is not None:                                    # the text that was REVIEWED becomes the baseline
                _write(_snap_path(d["path"]), text)
        stamp = time.strftime("%Y-%m-%d %H:%M")
        tail = (" %d doc(s) gave no usable answer and stay for the next sweep: %s." % (len(unread), ", ".join(unread))
                if unread else "")
        if findings:
            out = os.path.join(FINDINGS_DIR, time.strftime("%Y-%m-%d") + ".md")
            os.makedirs(FINDINGS_DIR, exist_ok=True)
            with open(out, "a", encoding="utf-8") as f:
                f.write("\n## sweep %s (order %s on %s)\n\n" % (stamp, pend.get("name"), where))
                for it in findings:
                    f.write("* **%s** — %s\n  * why: %s\n" % (it["doc"], it["claim"], it.get("why", "")))
            notify_desk("%d unbacked claim(s) across %d reviewed doc(s) — %s (order %s on %s).%s"
                        % (len(findings), len(reviewed), out, pend.get("name"), where, tail))
        else:
            # Say so even when clean. Silence must not be able to mean "the sweep is broken".
            notify_desk("clean: %d doc(s) reviewed, 0 unbacked claims (order %s on %s).%s"
                        % (len(reviewed), pend.get("name"), where, tail))
        _clear(pend)
        return {"reviewed": len(reviewed), "findings": findings, "unread": unread, "where": where}


def on_order_result(job):
    """The desk's completion receipt (app.p20_job_result): collect at once when it is the pending review order."""
    try:
        pend = _read_json(PENDING)
        if pend and (job or {}).get("id") == pend.get("id"):
            collect()
    except Exception as exc:                                        # noqa: BLE001 - a receipt hook never raises
        print("[DESK] claim review receipt hook failed: %s: %s" % (type(exc).__name__, exc))


def sweep(dry_run=False, desk=None):
    with _LOCK:
        os.makedirs(SNAP_DIR, exist_ok=True)
        os.makedirs(FINDINGS_DIR, exist_ok=True)
        baseline = not os.listdir(SNAP_DIR)

        prev = None if dry_run else collect(desk)
        if prev and "pending" in prev:
            notify_desk("SWEEP DID NOT SUBMIT - the previous review order %s is still %s at the desk; changes since "
                        "then stay for the next sweep. Nothing new was reviewed; this is NOT a clean result."
                        % (prev.get("name") or prev.get("id"), prev["pending"]))
            return {"skipped": True, "pending": prev}

        changed, items, staged = [], [], []
        for path in _docs():
            now = _read(path)
            if now is None:
                continue
            snap = _snap_path(path)
            before = _read(snap)
            if before == now:
                continue
            doc = os.path.basename(path)
            changed.append(doc)
            diff = "" if baseline or claim_review is None else claim_review.build_diff(before or "", now, doc)
            if baseline or claim_review is None or not claim_review.worth_review(diff):
                if not dry_run:
                    _write(snap, now)                               # nothing to review: the change is the baseline
                continue
            iid = "d%03d" % (len(items) + 1)
            # Today's date must lead the prompt via fellowship_turn.today_line(); static or manually derived dates fail external validation.
            import fellowship_turn
            items.append({"id": iid, "doc": doc, "path": path,
                          "prompt": fellowship_turn.today_line() + claim_review.prompt_for(doc, diff)})
            staged.append((iid, now))

        if baseline:
            notify_desk("baseline taken: %d documents snapshotted, none reviewed. Reviewing from the "
                        "next sweep." % len(changed))
            return {"baseline": True, "snapshotted": len(changed)}
        if dry_run:
            return {"changed": len(changed), "would_review": [it["doc"] for it in items]}
        if not items:
            notify_desk("clean: %d changed doc(s), none with a change worth a review (each under %d characters), "
                        "0 unbacked claims" % (len(changed), config.CLAIM_REVIEW_MIN_DIFF))
            return {"changed": len(changed), "reviewed": 0, "findings": []}

        oid, paths, where = submit(items, desk)
        if not oid:
            # Could not place => could not review: the snapshots are left untouched, so the next run reviews this
            # churn, and the desk is told plainly that nothing was checked.
            notify_desk("SWEEP DID NOT RUN - %s. Nothing was reviewed; this is NOT a clean result." % paths)
            return {"skipped": True, "why": paths}
        name = os.path.basename(paths["out"])[:-len(".out.json")]
        for iid, text in staged:
            _write(os.path.join(STAGED_DIR, name, iid), text)
        _write(PENDING, json.dumps({"id": oid, "name": name, "submitted": time.strftime("%Y-%m-%d %H:%M"),
                                    "paths": paths, "docs": [{k: it[k] for k in ("id", "doc", "path")} for it in items]},
                                   indent=1))
        print("[DESK] review order %s for %d doc(s): %s" % (name, len(items), where))
        return {"submitted": oid, "name": name, "items": len(items), "where": where}


if __name__ == "__main__":
    import sys
    print(json.dumps(sweep(dry_run="--dry-run" in sys.argv), indent=1, default=str)[:2000])
