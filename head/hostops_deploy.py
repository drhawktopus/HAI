"""Self-deploy executor for the Home AI orchestrator — Project #12 Phase 0 (the keystone).

This runs INSIDE the hostops broker, which is EXTERNAL to the orchestrator. That is the whole point:
the orchestrator cannot safely rebuild + restart itself (a bad deploy would take down the very process
doing the deploy, with no way to recover). The broker can, and it stays up across an orchestrator deploy.

Canary-first state machine — the live orchestrator is never rebuilt or recreated until a candidate built
from the NEW source has already passed health + smoke checks on a separate container/port:

    start -> PREFLIGHT -> BACKUP -> PROMOTE -> CANARY(build+up) -> CANARY VERIFY
                                                                      |-- fail -> tear down canary,
                                                                      |           restore canonical (live
                                                                      |           was never touched)
                                                                      `-- pass -> AWAITING_PROMOTE (human gate)
    promote -> CUTOVER(build+up live) -> LIVE VERIFY
                                            |-- fail -> AUTO-ROLLBACK (restore backup, rebuild, re-verify)
                                            `-- pass -> CLEANUP (tear down canary, prune backups) -> DONE

Cancellation is cooperative: the job checks a flag at every boundary. Pre-cutover cancel unwinds cleanly
(tear down canary + restore canonical). Post-cutover cancel takes the same rollback path as a failed
live verify. A cancel can never leave a half-applied deploy live.

stdlib only. The container runner (one-off docker:cli for `docker compose`) is INJECTED by the sidecar via
set_runner(), so this module has no docker dependency of its own and is fully unit-testable with a fake runner.
"""
import datetime as _dt
import json
import os
import shutil
import threading
import time
import traceback
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import urlparse

# --- Paths (broker-side mounts) -------------------------------------------------------------------------
CANONICAL_DIR = os.getenv("DEPLOY_CANONICAL_DIR", "/canonical")        # rw  = /opt/arrstack/orchestrator
WORKSPACES_DIR = os.getenv("DEPLOY_WORKSPACES_DIR", "/workspaces")     # ro  = /opt/arrstack/coding-workspaces
BACKUPS_DIR = os.getenv("DEPLOY_BACKUPS_DIR", "/deploy-backups")       # rw  (snapshots for rollback)

# --- The one-off `docker compose` runner -----------------------------------------------------------------
DOCKER_CLI_IMAGE = os.getenv("DEPLOY_DOCKER_IMAGE", "docker:cli")
COMPOSE_PROJECT = os.getenv("DEPLOY_COMPOSE_PROJECT", "arrstack")
COMPOSE_DIR = os.getenv("DEPLOY_COMPOSE_DIR", "")             # the stack's folder on the host, bound into the one-off
COMPOSE_NETWORK = os.getenv("DEPLOY_COMPOSE_NETWORK", "")     # the compose network the one-off joins
LIVE_SERVICE = os.getenv("DEPLOY_LIVE_SERVICE", "orchestrator")
CANARY_SERVICE = os.getenv("DEPLOY_CANARY_SERVICE", "orchestrator-canary")

# --- Health / smoke ------------------------------------------------------------------------------------
LIVE_HEALTH_URL = os.getenv("DEPLOY_LIVE_HEALTH", "http://orchestrator:8099/health")
CANARY_HEALTH_URL = os.getenv("DEPLOY_CANARY_HEALTH", "http://orchestrator-canary:8099/health")
# Extra cheap GET endpoints (no model call) that must return 200 on the canary before we trust it.
CANARY_SMOKE_URLS = [u for u in os.getenv(
    "DEPLOY_CANARY_SMOKE", "http://orchestrator-canary:8099/modes").split(",") if u.strip()]
HEALTH_TIMEOUT = int(os.getenv("DEPLOY_HEALTH_TIMEOUT", "150"))         # seconds to wait for a container healthy
HEALTH_POLL = int(os.getenv("DEPLOY_HEALTH_POLL", "3"))
BUILD_TIMEOUT = int(os.getenv("DEPLOY_BUILD_TIMEOUT", "600"))          # passed through to the runner

# Files HAI may self-promote = the decided edit scope: top-level Python + its tests. NOTHING else
# (compose, Dockerfile, requirements, secrets) is promoted by the executor — those stay human-controlled.
PROTECTED_NAMES = {".env", ".git", "__pycache__", ".pytest_cache", "secrets", "state", "history",
                   "backups", "docker-compose.yml", "docker-compose.override.yml"}
BACKUPS_KEEP = int(os.getenv("DEPLOY_BACKUPS_KEEP", "10"))

_RUNNER = None        # injected: (image, cmd, binds, env, user, network, entrypoint) -> (exit_code, output)
_LOCK = threading.Lock()
_CURRENT = None       # the single in-flight (or last) DeployJob


class DeployError(Exception):
    pass


class _Cancelled(Exception):
    pass


def set_runner(fn):
    """Inject the sidecar's one-off container runner (its _run_oneoff)."""
    global _RUNNER
    _RUNNER = fn


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(ts):
    return ts.astimezone().isoformat(timespec="seconds")


# --- Source manifest / promote / backup / rollback -----------------------------------------------------
def _source_files(root):
    """Relative paths of the self-editable source under `root`: top-level *.py + tests/*.py, minus protected.
    Deterministic + sorted. Used both to read the workspace (promote) and snapshot canonical (backup)."""
    out = []
    if os.path.isdir(root):
        for name in os.listdir(root):
            if name in PROTECTED_NAMES:
                continue
            if name.endswith(".py") and os.path.isfile(os.path.join(root, name)):
                out.append(name)
        tdir = os.path.join(root, "tests")
        if os.path.isdir(tdir):
            for name in sorted(os.listdir(tdir)):
                if name.endswith(".py") and os.path.isfile(os.path.join(tdir, name)):
                    out.append(os.path.join("tests", name))
    return sorted(out)


def _file_diff(workspace, canonical):
    """(changed, added) relative paths: workspace files whose bytes differ from canonical (changed) or that
    don't exist in canonical (added). A deploy with neither is rejected at preflight."""
    changed, added = [], []
    for rel in _source_files(workspace):
        wpath, cpath = os.path.join(workspace, rel), os.path.join(canonical, rel)
        if not os.path.exists(cpath):
            added.append(rel)
        else:
            with open(wpath, "rb") as a, open(cpath, "rb") as b:
                if a.read() != b.read():
                    changed.append(rel)
    return changed, added


def _snapshot(canonical, dest):
    """Copy the canonical source set into `dest`, recording the file list (for exact rollback)."""
    files = _source_files(canonical)
    for rel in files:
        dpath = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dpath), exist_ok=True)
        shutil.copy2(os.path.join(canonical, rel), dpath)
    with open(os.path.join(dest, ".manifest.json"), "w") as fh:
        json.dump({"files": files, "ts": _iso(_now())}, fh)
    return files


def _promote(workspace, canonical, manifest):
    """Copy the manifest (changed+added) workspace files into canonical, overwriting. Perms are normalized to
    0644 (world-readable): source is just Python/tests, never secrets, and a stray 0600 mode would brick a
    later non-root `docker compose build` (it silently can't read the file and bakes an empty module)."""
    for rel in manifest:
        cpath = os.path.join(canonical, rel)
        os.makedirs(os.path.dirname(cpath), exist_ok=True)
        shutil.copy2(os.path.join(workspace, rel), cpath)
        os.chmod(cpath, 0o644)


def _restore(backup_dir, canonical):
    """Exactly reverse a promote: restore every backed-up file, then DELETE any current source file that the
    backup didn't contain (i.e. files the promote added). Fully reverts an additive+overwrite promote."""
    with open(os.path.join(backup_dir, ".manifest.json")) as fh:
        backed = set(json.load(fh).get("files", []))
    for rel in backed:
        cpath = os.path.join(canonical, rel)
        os.makedirs(os.path.dirname(cpath), exist_ok=True)
        shutil.copy2(os.path.join(backup_dir, rel), cpath)
        os.chmod(cpath, 0o644)   # same reason as _promote: keep canonical source world-readable
    for rel in _source_files(canonical):
        if rel not in backed:
            try:
                os.remove(os.path.join(canonical, rel))
            except OSError:
                pass


def _prune_backups():
    try:
        snaps = sorted(d for d in os.listdir(BACKUPS_DIR)
                       if os.path.isdir(os.path.join(BACKUPS_DIR, d)))
        for d in snaps[:-BACKUPS_KEEP] if len(snaps) > BACKUPS_KEEP else []:
            shutil.rmtree(os.path.join(BACKUPS_DIR, d), ignore_errors=True)
    except OSError:
        pass


# --- Docker compose (via the injected one-off runner) --------------------------------------------------
def _compose(args, log):
    """Run `docker compose -p <project> <args>` in a one-off docker:cli container (socket + repo mounted).
    Returns (exit_code, output). Raises DeployError if no runner is wired."""
    if _RUNNER is None:
        raise DeployError("deploy runner not configured")
    if not COMPOSE_DIR or not COMPOSE_NETWORK:
        raise DeployError("DEPLOY_COMPOSE_DIR / DEPLOY_COMPOSE_NETWORK are not set: this head cannot run compose")
    script = "cd %s && docker compose -p %s %s" % (COMPOSE_DIR, COMPOSE_PROJECT, " ".join(args))
    log("$ " + script)
    code, out = _RUNNER(
        DOCKER_CLI_IMAGE,
        [script],
        ["%s:%s" % (COMPOSE_DIR, COMPOSE_DIR), "/var/run/docker.sock:/var/run/docker.sock"],
        ["COMPOSE_PROJECT_NAME=" + COMPOSE_PROJECT],
        "0:0",
        COMPOSE_NETWORK,
        ["sh", "-c"],
    )
    tail = (out or "").strip()
    if tail:
        log(tail[-1500:])
    return code, tail


def _build_up(service, log):
    code, _ = _compose(["build", service], log)
    if code != 0:
        raise DeployError("`compose build %s` failed (exit %s)" % (service, code))
    code, _ = _compose(["up", "-d", "--force-recreate", service], log)
    if code != 0:
        raise DeployError("`compose up %s` failed (exit %s)" % (service, code))


def _teardown(service, log):
    try:
        _compose(["rm", "-sf", service], log)
    except DeployError as exc:
        log("teardown warning: %s" % exc)


def _http_get(url, timeout=5):
    p = urlparse(url)
    conn_cls = HTTPSConnection if p.scheme == "https" else HTTPConnection
    conn = conn_cls(p.hostname, p.port or (443 if p.scheme == "https" else 80), timeout=timeout)
    try:
        conn.request("GET", p.path or "/")
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def _wait_healthy(url, log, check_cancel):
    """Poll a /health endpoint until it returns 200 with ok:true, or timeout. Returns True/False."""
    deadline = time.time() + HEALTH_TIMEOUT
    last = ""
    while time.time() < deadline:
        check_cancel()
        try:
            status, body = _http_get(url)
            if status == 200:
                try:
                    if json.loads(body).get("ok") is True:
                        log("health OK: %s" % url)
                        return True
                except (ValueError, TypeError):
                    pass
            last = "HTTP %s" % status
        except Exception as exc:
            last = str(exc)
        time.sleep(HEALTH_POLL)
    log("health FAILED (%s): %s" % (last, url))
    return False


def _smoke(urls, log):
    for url in urls:
        try:
            status, _ = _http_get(url)
        except Exception as exc:
            log("smoke FAILED (%s): %s" % (exc, url))
            return False
        if status != 200:
            log("smoke FAILED (HTTP %s): %s" % (status, url))
            return False
        log("smoke OK: %s" % url)
    return True


# --- The job -------------------------------------------------------------------------------------------
class DeployJob:
    def __init__(self, workspace_id, user):
        self.id = _iso(_now()).replace(":", "").replace("-", "")[:15] + "-" + workspace_id[:12]
        self.workspace_id = workspace_id
        self.user = user or ""
        self.phase = "pending"
        self.logs = []
        self.created_at = _now()
        self.updated_at = _now()
        self.error = None
        self.diff = {"changed": [], "added": []}
        self.backup_dir = None
        self._cancel = threading.Event()
        self._promote = threading.Event()   # set by /deploy/promote to release the human gate
        self._thread = None

    # -- state helpers --
    def _log(self, msg):
        line = "%s %s" % (_iso(_now()), msg)
        self.logs.append(line)
        self.logs[:] = self.logs[-200:]

    def _set(self, phase):
        self.phase = phase
        self.updated_at = _now()
        self._log("phase -> %s" % phase)

    def _check_cancel(self):
        if self._cancel.is_set():
            raise _Cancelled()

    def status(self):
        return {
            "job": self.id,
            "workspace": self.workspace_id,
            "user": self.user,
            "phase": self.phase,
            "diff": self.diff,
            "error": self.error,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "logs": self.logs[-60:],
            "awaiting_promote": self.phase == "awaiting_promote",
            "active": self.phase not in _TERMINAL,
        }

    # -- control --
    def request_promote(self):
        if self.phase != "awaiting_promote":
            return False
        self._promote.set()
        return True

    def request_cancel(self):
        if self.phase in _TERMINAL:
            return False
        self._cancel.set()
        self._promote.set()   # unblock the gate so the thread can observe the cancel
        return True

    # -- run --
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        workspace = os.path.join(WORKSPACES_DIR, self.workspace_id)
        try:
            # PREFLIGHT
            self._set("preflight")
            self._check_cancel()
            if not os.path.isdir(workspace):
                raise DeployError("workspace not found: %s" % self.workspace_id)
            changed, added = _file_diff(workspace, CANONICAL_DIR)
            self.diff = {"changed": changed, "added": added}
            manifest = changed + added
            if not manifest:
                raise DeployError("no source changes vs canonical — nothing to deploy")
            self._log("diff: %d changed, %d added" % (len(changed), len(added)))

            # BACKUP
            self._set("backup")
            self._check_cancel()
            self.backup_dir = os.path.join(BACKUPS_DIR, self.id)
            os.makedirs(self.backup_dir, exist_ok=True)
            n = len(_snapshot(CANONICAL_DIR, self.backup_dir))
            self._log("snapshotted %d canonical files -> %s" % (n, self.backup_dir))

            # PROMOTE
            self._set("promote")
            self._check_cancel()
            _promote(workspace, CANONICAL_DIR, manifest)
            self._log("promoted %d files into canonical" % len(manifest))

            # CANARY build + up (live untouched)
            self._set("canary_build")
            self._check_cancel()
            _build_up(CANARY_SERVICE, self._log)

            # CANARY verify
            self._set("canary_verify")
            if not _wait_healthy(CANARY_HEALTH_URL, self._log, self._check_cancel) or \
                    not _smoke(CANARY_SMOKE_URLS, self._log):
                raise DeployError("canary failed health/smoke — live was never touched")

            # HUMAN GATE
            self._set("awaiting_promote")
            self._log("canary healthy; awaiting manual 'promote to live'")
            self._promote.wait()
            self._check_cancel()

            # CUTOVER (rebuild + recreate live from the same promoted source)
            self._set("cutover")
            _build_up(LIVE_SERVICE, self._log)

            # LIVE verify -> auto-rollback on failure
            self._set("live_verify")
            if not _wait_healthy(LIVE_HEALTH_URL, self._log, lambda: None):
                self._log("live verify FAILED — auto-rolling back")
                self._rollback()
                return

            # CLEANUP
            self._set("cleanup")
            _teardown(CANARY_SERVICE, self._log)
            _prune_backups()
            self._set("done")
            self._log("deploy complete and live is healthy")

        except _Cancelled:
            self._set("cancelling")
            self._log("cancellation requested — unwinding")
            self._unwind_cancel()
        except Exception as exc:
            self.error = str(exc)
            self._log("ERROR: %s" % exc)
            self._log(traceback.format_exc()[-800:])
            self._safe_recover()

    def _rollback(self):
        self._set("rollback")
        try:
            if self.backup_dir:
                _restore(self.backup_dir, CANONICAL_DIR)
                self._log("restored canonical from backup")
            _build_up(LIVE_SERVICE, self._log)
            ok = _wait_healthy(LIVE_HEALTH_URL, self._log, lambda: None)
            _teardown(CANARY_SERVICE, self._log)
            if ok:
                self.error = self.error or "deploy failed; rolled back to previous version (live healthy)"
                self._set("rolled_back")
            else:
                self.error = "ROLLBACK health check failed — manual intervention needed"
                self._set("failed")
        except Exception as exc:
            self.error = "rollback error: %s" % exc
            self._log("ROLLBACK ERROR: %s" % exc)
            self._set("failed")

    def _safe_recover(self):
        """A failure before cutover: live was never touched. Restore canonical (undo the promote) + tear down
        canary so a bad promoted tree doesn't linger. A failure during/after cutover goes through _rollback."""
        if self.phase in ("cutover", "live_verify", "rollback"):
            self._rollback()
            return
        try:
            if self.backup_dir and os.path.isdir(self.backup_dir):
                _restore(self.backup_dir, CANONICAL_DIR)
                self._log("restored canonical (live was never touched)")
            _teardown(CANARY_SERVICE, self._log)
        except Exception as exc:
            self._log("recover warning: %s" % exc)
        self._set("failed")

    def _unwind_cancel(self):
        """Cooperative-cancel unwind. Pre-cutover: restore canonical + tear down canary (clean). If the cancel
        arrived during/after cutover, take the rollback path so live is never left half-applied."""
        if self.phase in ("cutover", "live_verify"):
            self._rollback()
            self._set("cancelled")
            return
        try:
            if self.backup_dir and os.path.isdir(self.backup_dir):
                _restore(self.backup_dir, CANONICAL_DIR)
                self._log("restored canonical")
            _teardown(CANARY_SERVICE, self._log)
        except Exception as exc:
            self._log("cancel-unwind warning: %s" % exc)
        self._set("cancelled")


_TERMINAL = {"done", "failed", "rolled_back", "cancelled"}


# --- Public API used by the sidecar HTTP layer ---------------------------------------------------------
def start(workspace_id, user):
    global _CURRENT
    workspace_id = (workspace_id or "").strip()
    if not workspace_id or "/" in workspace_id or workspace_id.startswith("."):
        return {"error": "invalid workspace id"}
    with _LOCK:
        if _CURRENT is not None and _CURRENT.phase not in _TERMINAL:
            return {"error": "a deploy is already in progress", "job": _CURRENT.id, "phase": _CURRENT.phase}
        job = DeployJob(workspace_id, user)
        _CURRENT = job
    job.start()
    return job.status()


def status(job_id=None):
    with _LOCK:
        job = _CURRENT
    if job is None:
        return {"error": "no deploy has been run"}
    if job_id and job_id != job.id:
        return {"error": "unknown job id (only the latest is tracked)", "latest": job.id}
    return job.status()


def promote(job_id=None):
    with _LOCK:
        job = _CURRENT
    if job is None:
        return {"error": "no deploy to promote"}
    if job_id and job_id != job.id:
        return {"error": "unknown job id", "latest": job.id}
    if not job.request_promote():
        return {"error": "job is not awaiting promotion", "phase": job.phase}
    return {"ok": True, "promoting": job.id}


def cancel(job_id=None):
    with _LOCK:
        job = _CURRENT
    if job is None:
        return {"error": "no deploy to cancel"}
    if job_id and job_id != job.id:
        return {"error": "unknown job id", "latest": job.id}
    if not job.request_cancel():
        return {"error": "job already finished", "phase": job.phase}
    return {"ok": True, "cancelling": job.id}
