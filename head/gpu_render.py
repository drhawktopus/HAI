"""Renders pages on a remote GPU via factory order to ensure hardware-accurate output; software rendering in the local VM is unreliable for FPS or heavy pages.

Never schedule render orders on the box serving the current job: its lane is reserved for the active job. Do not queue or wait for busy GPU boxes; return None immediately so the caller can fall back to software rendering."""
import base64
import json
import os
import shutil
import tarfile
import io
import time
import uuid

import coding_client
import config
import p20_queue
import scheduler

CAPABILITY = "render.browser"
TOLKIEN_BOX = config.roster_role("tolkien")     # the box Tolkien runs on (its own GPU lane), or None when this head has none
TOLKIEN_PAGE_CAPABILITY = "rigby.page"
JOBS_TOLKIEN = os.getenv("HAI_RENDER_JOBS_TOLKIEN", "/ssdshared/tabfoundry-jobs/renders")
JOBS_DIR = os.getenv("HAI_RENDER_JOBS_DIR", "/tabjobs/renders")
# The render jobs share as the factories reach it: a setting, never an address in the code.
# Empty means no share is configured, and a GPU render order is refused rather than guessed.
JOBS_UNC = os.getenv("HAI_RENDER_JOBS_UNC", "")
POLL_S = 1.0
ENDED = ("done", "failed", "cancelled", "expired")


def tolkien_box_is_free():
    """Nothing ahead on the Tolkien box and nothing holding it: the one reading of that, for a page and for a test run."""
    load = scheduler.fellowship_load()
    return not load["ahead"] and bool(load.get("tolkien_box_free"))


def free_box(exclude=()):
    """The box Spock would use for a page right now, or None and why not."""
    able = [h for h in scheduler.CARDS if CAPABILITY in scheduler.factory_capabilities(h)]
    if able:
        ranked = scheduler.place("page", only=tuple(able), exclude=tuple(h for h in exclude if h))
        for host, _score, _why in ranked:
            busy, _reason = scheduler.training_busy(host)
            if not busy or scheduler.page_on_lane(host):
                return host, ""
    # TOLKIEN_BOX is checked AFTER the fleet because it is a personal machine (Tolkien's lane), not a shared factory host.
    # `tolkien_box_is_free()` must return a fresh status; a stale or unknown value is treated as unavailable (fail-closed).
    if TOLKIEN_BOX and TOLKIEN_BOX not in exclude and TOLKIEN_PAGE_CAPABILITY in scheduler.factory_capabilities(scheduler.FELLOWSHIP_HOST):
        if tolkien_box_is_free():
            return TOLKIEN_BOX, ""
        able = able + [TOLKIEN_BOX]
    if not able:
        return None, "no factory declares %s" % CAPABILITY
    return None, "every GPU box that can show a page is busy (%s)" % ", ".join(able)


def _unpack(bundle_b64, dest):
    """The runner's tar of the page's folder. Regular files only, and nothing lands outside `dest`."""
    root = os.path.realpath(dest)
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(bundle_b64)), mode="r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            target = os.path.realpath(os.path.join(root, m.name))
            if not target.startswith(root + os.sep):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with tar.extractfile(m) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def _to_tolkien_box(name, rid):
    """A `page.render` order for Tolkien, through the desk's Fellowship form, as chat_window._intake_tolkien sends his chat windows. Tolkien writes result.json last, and straight to the share."""
    from app import FactoryOrderReq, p20_order
    base = JOBS_TOLKIEN + "/" + rid
    tspec = {"kind": "page.render", "name": name, "request": base + "/request.json", "out_dir": base + "/out/" + name, "notify": "failures", "initiator": "coding_render"}
    gpu = {"name": name, "mode": "compute", "load": "gpu", "initiator": "coding_render", "payload": "{FOUNDRY_ROOT}\\bin\\helper_window.py", "args": [], "notify": "failures"}
    reply = p20_order(FactoryOrderReq(spec=gpu, requires=["order.compute"], only=[scheduler.FELLOWSHIP_HOST], family="chat-c1", tier="fellowship", lens="fastest",
                                      fellowship={"spec": tspec, "requires": [TOLKIEN_PAGE_CAPABILITY]}, rush=True))
    steps = [s for s in (reply.get("steps") or []) if isinstance(s, dict)]
    if not reply.get("ok") or not steps:
        raise RuntimeError("the desk refused the page for %s: %s" % (TOLKIEN_BOX, str(reply.get("error") or reply.get("why"))[:200]))
    return {"id": steps[0].get("id")}


def _wait(job_id, host, finished=None):
    """The order's end, asked of the desk (or of the file a Tolkien box order writes last). A box that stops reporting while we wait returns None: could not tell is not done."""
    while True:
        if finished and os.path.exists(finished):
            return {"status": "finished"}
        job = p20_queue.get(job_id) or {}
        status = job.get("status")
        if status in ENDED:
            if finished and os.path.exists(finished):
                return {"status": "finished"}
            return job
        if status == "pending" and not scheduler.factory_capabilities(host):
            return None
        time.sleep(POLL_S)


def show_page(workspace, path, width=None, height=None, steps=None, wait_ms=None, exclude=(), query=""):
    """-> (the render's result with its pictures filed in the workspace, "") or (None, why this was not drawn on a GPU)."""
    host, why = free_box(exclude)
    if not host:
        return None, why
    bundle = coding_client.page_bundle(workspace, path)
    if not isinstance(bundle, dict) or bundle.get("error") or not bundle.get("bundle_b64"):
        return None, "the page's folder could not be bundled: %s" % (str((bundle or {}).get("error"))[:200],)
    rid = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:8])
    folder = os.path.join(JOBS_DIR, rid)
    keep = False
    try:
        os.makedirs(os.path.join(folder, "page"))
        _unpack(bundle["bundle_b64"], os.path.join(folder, "page"))
        with open(os.path.join(folder, "request.json"), "w", encoding="utf-8") as f:
            json.dump({"page": bundle["page"], "width": width, "height": height, "steps": steps, "wait_ms": wait_ms,
                       "query": query or ""}, f)
        name = scheduler.PAGE_ORDER_PREFIX + rid
        if host == TOLKIEN_BOX:
            job = _to_tolkien_box(name, rid)
        else:
            if not JOBS_UNC:
                return None, "no render jobs share is configured (HAI_RENDER_JOBS_UNC is empty), " \
                             "so a GPU render order cannot be placed"
            unc = JOBS_UNC + "\\" + rid
            spec = {"name": name, "mode": "compute", "load": "gpu", "initiator": "coding_render",
                    "payload": "{FOUNDRY_ROOT}\\bin\\render_order.py",
                    "python_exe": "{FOUNDRY_ROOT}\\venv-render\\Scripts\\python.exe",
                    "args": [unc + "\\request.json", "{ORDER_OUT}"], "deliver_to": unc + "\\out", "notify": "failures",
                    "requires": ["order.compute", CAPABILITY]}
            job = p20_queue.enqueue_factory_order(spec, requires=spec["requires"], host=host, rush=True)
        handle = {"kind": "gpu_page", "rid": rid, "name": name, "host": host, "job": job["id"], "workspace": workspace,
                  "path": path}
        import ollama_client
        ollama_client.note_tool_handle(handle)   # a run back after a restart waits for this order (finish_page)
        return _finish(handle)
    except BaseException as e:
        keep = not isinstance(e, Exception)   # the process is going down: the order goes on, and the folder is its delivery
        raise
    finally:
        if not keep:
            shutil.rmtree(folder, ignore_errors=True)


def finish_page(handle):
    """A call a restart cut while its order ran: wait for that same order and file its pictures, as show_page would have.
    -> (result, "") or (None, why)."""
    try:
        return _finish(handle)
    finally:
        shutil.rmtree(os.path.join(JOBS_DIR, handle["rid"]), ignore_errors=True)


def _finish(handle):
    rid, name, host, workspace, path = handle["rid"], handle["name"], handle["host"], handle["workspace"], handle["path"]
    folder = os.path.join(JOBS_DIR, rid)
    if host == TOLKIEN_BOX:
        ended = _wait(handle["job"], scheduler.FELLOWSHIP_HOST, finished=os.path.join(folder, "out", name, "result.json"))
    else:
        ended = _wait(handle["job"], host)
    if ended is None:
        return None, "%s stopped reporting while it held the render" % host
    if ended.get("status") not in ("done", "finished"):
        return None, "the render order on %s ended %s: %s" % (host, ended.get("status"), str(ended.get("error"))[:200])
    out = _delivered(folder)
    if not out:
        return None, "the render order on %s shipped no result" % host
    with open(os.path.join(out, "result.json"), encoding="utf-8") as f:
        result = json.load(f)
    if result.get("error"):
        return dict(result, drawn_on=host), ""
    pictures = {}
    for shot in result.get("shots") or []:
        with open(os.path.join(out, shot["file"]), "rb") as f:
            pictures[shot["file"]] = base64.b64encode(f.read()).decode("ascii")
    filed = coding_client.file_shots(workspace, path, result, pictures)
    if not isinstance(filed, dict) or filed.get("error"):
        return None, "the runner could not file the pictures: %s" % (str((filed or {}).get("error"))[:200],)
    return dict(filed, drawn_on=host), ""


def _delivered(folder):
    """The shipped folder: deliver_to\\<order name>\\ holding result.json. One order per request folder, so one match."""
    base = os.path.join(folder, "out")
    try:
        names = [n for n in os.listdir(base) if os.path.exists(os.path.join(base, n, "result.json"))]
    except OSError:
        return None
    return os.path.join(base, names[0]) if names else None
