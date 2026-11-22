"""HAI jobs are desk orders (`p20_queue`, kind "hai.job") targeting host `kirk` to execute `/query/stream` requests.

Scheduling gates:
- `not_before` (epoch seconds): hard start time constraint.
- `after_ids`: predecessor orders must be `done`.

Lifecycle & Invariants:
- Budget clock starts upon execution, not submission.
- Persistence: Survives restarts like standard accepted runs.
- Failure handling:
  - Default: `failed` order blocks successors.
  - `go_on_after_failure`: `failed` order releases successors (for independent measurement runs); `cancelled` (Stop) still blocks.
- Exclusion: No factory can claim this order (requires undeclared `hai.job` capability)."""
import time


def _q():
    import p20_queue   # per call: the current module object
    return p20_queue

KIND = "hai.job"
HOST = "kirk"


def submit(query, *, name, not_before=None, after=(), ship=None, blind=False, go_on_after_failure=False):
    """Put a HAI job on the desk.

    `query`: The /query/stream request dict (validated by caller).
    `ship`: Dict with "workspace" and "path" for the job folder. Shipped out of the shared workspace upon completion before marking the order, ensuring chained jobs never see it.
    `go_on_after_failure`: If True, starts after predecessors end (done or failed)."""
    spec = {"kind": KIND, "name": str(name or "hai-job"), "query": dict(query), "user": query.get("user") or "Guest",
            "not_before": float(not_before) if not_before else None, "requires": [KIND],
            "ship": {"workspace": str(ship["workspace"]), "path": str(ship["path"])} if ship else None,
            "blind": bool(blind),   # hide every open coding task before it starts (hide_open_tasks)
            "go_on_after_failure": bool(go_on_after_failure)}
    return _q().enqueue_factory_order(spec, requires=[KIND], after_ids=[str(a) for a in (after or ()) if a], host=HOST)


def is_job(order):
    return (order.get("spec") or {}).get("kind") == KIND


def jobs(user=None):
    """Every HAI job on the desk. A job in flight also carries `habits`: one line on how its run is working (renders,
    looks, rewrites), the same meter its model hears and its finish gate asks (run_habits). Added to the copy returned
    here; the desk's record is not touched."""
    import run_habits
    out = [j for j in _q().all_jobs() if is_job(j)]
    out = [dict(j, habits=run_habits.LIVE[j["run_id"]]) if j.get("run_id") in run_habits.LIVE and j.get("status") == "running"
           else j for j in out]
    return [j for j in out if user is None or (j.get("spec") or {}).get("user") == user]


def due(now=None):
    """The pending HAI jobs whose start time has come and whose predecessors are all done, oldest first."""
    now = time.time() if now is None else now
    every = _q().all_jobs()
    by_id = {j.get("id"): j for j in every}
    out = []
    for j in every:
        if not is_job(j) or j.get("status") != "pending":
            continue
        nb = (j.get("spec") or {}).get("not_before")
        if nb and now < float(nb):
            continue
        if _q().blocked_by(j, by_id):
            continue
        out.append(j)
    return sorted(out, key=lambda j: str(j.get("created") or ""))


def tick(start, now=None, alive=None):
    """Start every due job with `start(order) -> run id`, marking it `running` BEFORE the start (one-shot: a crash between
    the mark and the start leaves it running-without-a-run, which `alive` reconciles; it is never started twice). A start
    that raises puts the order back to `pending` with the reason when it is a wait (`start` raises `Wait`), else `failed`.
    `alive(run_id) -> bool` reconciles orders marked running whose run is gone without a recorded end. -> ids started."""
    started = []
    for j in due(now):
        _q().mark(j["id"], "running", started_at=time.time())
        try:
            if (j.get("spec") or {}).get("blind"):
                failed_shipments = [pid for pid in j.get("after_ids") or []
                                    if (_q().get(pid) or {}).get("ship_error")]
                if failed_shipments:
                    raise Wait("blind start waits for verified predecessor artifact isolation: " + ", ".join(failed_shipments))
            rid = start(j)
        except Wait as w:
            _q().mark(j["id"], "pending", hold_reason=str(w))
            continue
        except Exception as exc:                                     # noqa: BLE001 - recorded on the order, never lost
            _q().mark(j["id"], "failed", error="could not start: %s: %s" % (type(exc).__name__, str(exc)[:300]))
            continue
        _q().mark(j["id"], "running", run_id=rid)
        started.append(j["id"])
    if alive is not None:
        for j in jobs():
            rid = j.get("run_id")
            if j.get("status") == "running" and rid and not alive(rid):
                _q().mark(j["id"], "failed", error="its run %s ended without a recorded result" % rid)
    return started


def ended(order_id, route, shipper=None):
    """The run of `order_id` ended by `route` (the result's route). Ships the job's folder first when the order names one
    (`shipper(workspace, path) -> {shipped_to, files}`), so the order is marked - and a successor released - only after its
    folder has left; then marks the order. A shipment that fails is recorded on the order (`ship_error`), never lost, and
    never turns a finished run into a failed one. A dead order blocks its successors (the desk's rule)."""
    r = str(route or "")
    status = "cancelled" if r == "cancelled" else "failed" if r == "error" else "done"
    extra = {}
    ship = ((_q().get(order_id) or {}).get("spec") or {}).get("ship")
    if ship and shipper is not None:
        try:
            out = shipper(ship["workspace"], ship["path"])
            if isinstance(out, dict) and out.get("error"):
                extra["ship_error"] = str(out["error"])[:300]
                if out.get("completed_task_shipment"):
                    extra["partial_shipment"] = out["completed_task_shipment"]
            else:
                extra.update(shipped_to=(out or {}).get("shipped_to"), shipped_files=(out or {}).get("files"))
                if (out or {}).get("source_task_id"):
                    extra["ship_provenance"] = {key: out[key] for key in (
                        "source_task_id", "logical_workspace", "source_workspace", "supporting_base_shipment", "unfinished_task",
                        "preserved_checkpoint") if key in out}
        except Exception as exc:                                     # noqa: BLE001 - recorded, never lost
            extra["ship_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:280])
    # Use `run_route` to record the executed path; `route` is reserved for the placement template (enqueue_factory_order) and must not be overwritten.
    return _q().mark(order_id, status, run_route=r, ended_at=time.time(), **extra)


def cancel(order_id):
    """A pending job is cancelled on the desk; a running one is reported back so the caller cancels its run (whose end
    then marks the order cancelled). -> ("cancelled" | "running" | "not cancellable", order or None)."""
    j = _q().get(order_id)
    if not j or not is_job(j):
        return "not cancellable", None
    if j.get("status") == "pending":
        return "cancelled", _q().mark(order_id, "cancelled", error="cancelled before it started")
    if j.get("status") == "running":
        return "running", j
    return "not cancellable", j


def hide_open_tasks(list_tasks):
    """Every open coding task out of sight before a BLIND job starts (run_chain's hide_open_tasks, done by the order): a job chained
    after another under the same user could otherwise list the earlier job's task and resume it (a stopped chat's task can be
    picked up). `list_tasks() -> {"tasks": [...]}` (coding_client.task("list")). It adds to rag-hide.json's "tasks", keeping
    everything else in the file. -> how many are hidden now. Raises Wait when the tasks cannot be listed: a blind job never
    starts with them in view."""
    import json
    import os
    import rag
    got = list_tasks()
    if not isinstance(got, dict) or not isinstance(got.get("tasks"), list):
        raise Wait("could not list the open coding tasks to hide: %s" % str(got)[:200])
    ids = {str(t.get("id")) for t in got["tasks"] if t.get("id")}
    path = rag.hide_file()
    try:
        with open(path, encoding="utf-8") as f:
            hide = json.load(f)
    except (OSError, ValueError):
        hide = {"why": "a blind HAI job hid the open tasks before it started (hai_jobs.hide_open_tasks)"}
    hide["tasks"] = sorted(set(hide.get("tasks") or []) | ids)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(hide, f)
    os.replace(tmp, path)
    return len(hide["tasks"])


class Wait(Exception):
    """A start that must wait (an energy deferral): the order stays pending with the reason, never consumed."""
