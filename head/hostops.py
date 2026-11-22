"""Orchestrator-side client for the host-ops broker sidecar (Phase 4).

Thin HTTP client (like coding_client / arrclient): the orchestrator stays socket-free and asks the sidecar to
perform the few whitelisted host/docker actions. Read helpers return model-friendly strings; the write action
(restart) goes through pending_actions so it inherits admin-gating + confirm + risk flags + audit.
"""
import httpx

import config
import pending_actions


class HostOpsError(Exception):
    pass


def _headers():
    h = {}
    if config.HOSTOPS_TOKEN:
        h["x-hostops-token"] = config.HOSTOPS_TOKEN
    return h


def _request(method, path, params=None, json_body=None, timeout=None):
    try:
        r = httpx.request(method, f"{config.HOSTOPS_URL}{path}", params=params, json=json_body,
                          headers=_headers(), timeout=timeout or config.HOSTOPS_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if method == "POST" and path.startswith("/deploy/") and path.rstrip("/").rsplit("/", 1)[-1] in ("start", "promote", "cancel"):
            _audit_deploy(method, path, json_body, data)
        return data
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:300] if exc.response is not None else ""
        raise HostOpsError(f"{method} {path} failed: HTTP {exc.response.status_code}: {body}") from exc
    except Exception as exc:
        if isinstance(exc, HostOpsError):
            raise
        raise HostOpsError(f"{method} {path} failed: {exc}") from exc


def _audit_deploy(method, path, json_body, result):
    """Audit a mutating deploy-broker op (start/promote/cancel) so autonomous / broker-direct deploys leave
    a trail in ops-audit.jsonl - not only the pending-card confirm path. Fail-soft."""
    op = path.rstrip("/").rsplit("/", 1)[-1]
    try:
        pending_actions._audit("DEPLOY_OP", {
            "kind": f"deploy.{op}",
            "user": (json_body or {}).get("user", ""),
            "risk": "HIGH",
            "metadata": {"method": method, "path": path,
                         "job": (json_body or {}).get("job")
                         or (result or {}).get("job") or (result or {}).get("promoting")},
        }, result)
    except Exception:
        pass

def docker_status(name_filter=None):
    """Read-only. List stack containers (name, state, status) via the broker, optional substring filter."""
    try:
        data = _request("GET", "/docker/ps")
    except HostOpsError as exc:
        return f"Could not reach the host-ops broker: {exc}"
    containers = data.get("containers", [])
    nf = (name_filter or "").strip().lower()
    if nf:
        containers = [c for c in containers if nf in (c.get("name") or "").lower()]
    if not containers:
        return "No matching containers." if nf else "No containers reported."
    lines = [f"{c.get('name')}: {c.get('state')} ({c.get('status')})" for c in containers]
    return "Containers:\n" + "\n".join(lines)


def docker_logs(name, tail=100):
    """Read-only. Tail a container's logs via the broker."""
    name = (name or "").strip()
    if not name:
        return "docker_logs needs a container name (see docker_status)."
    try:
        tail = int(tail or 100)
    except (TypeError, ValueError):
        tail = 100
    try:
        data = _request("GET", "/docker/logs", params={"name": name, "tail": tail})
    except HostOpsError as exc:
        return f"Could not get logs for {name}: {exc}"
    logs = (data.get("logs") or "").strip()
    if not logs:
        return f"{name}: (no log output in the last {data.get('tail', tail)} lines)"
    return f"{name} — last {data.get('tail', tail)} lines:\n{logs[-6000:]}"


def docker_restart_preview(name, user=None):
    """Confirm-gated container restart via the broker. The broker also refuses PROTECTED containers, so a
    confirmed call still can't restart the VPN/Pi-hole/orchestrator/broker themselves."""
    name = (name or "").strip()
    if not name:
        return {"status": "error", "message": "docker_restart needs a container name (see docker_status)."}
    if name in config.HOSTOPS_PROTECTED:
        return {"status": "error",
                "message": f"{name} is protected (VPN/DNS/orchestrator/broker) and is not restartable here."}
    preview = (f"Restart the '{name}' container (docker restart {name}). This briefly drops {name} while it "
               "comes back up.")

    def _execute():
        return _request("POST", "/docker/restart", json_body={"name": name})

    return pending_actions.register(
        "docker.restart",
        preview,
        _execute,
        user=user,
        destructive=True,
        metadata={"service": "docker", "action": "restart", "name": name},
    )


def _container_action_preview(name, action, user=None):
    name = (name or "").strip()
    if not name:
        return {"status": "error", "message": f"docker_{action} needs a container name (see docker_status)."}
    if action in ("stop", "restart") and name in config.HOSTOPS_PROTECTED:
        return {"status": "error",
                "message": f"{name} is protected (VPN/DNS/orchestrator/broker) and is not {action}able here."}
    verb = {"stop": "Stop", "start": "Start", "restart": "Restart"}[action]
    tail = {"stop": " (it stays down until started again)",
            "start": "", "restart": " (briefly drops it while it comes back up)"}[action]
    preview = f"{verb} the '{name}' container (docker {action} {name}){tail}."

    def _execute():
        return _request("POST", f"/docker/{action}", json_body={"name": name})

    return pending_actions.register(
        f"docker.{action}", preview, _execute, user=user,
        destructive=(action in ("stop", "restart")),
        metadata={"service": "docker", "action": action, "name": name})


def docker_stop_preview(name, user=None):
    return _container_action_preview(name, "stop", user=user)


def docker_start_preview(name, user=None):
    return _container_action_preview(name, "start", user=user)


def watchtower_run_preview(user=None):
    """Confirm-gated on-demand Watchtower run (one-off --run-once): checks every managed container for a newer
    image, pulls, and RECREATES the ones with updates (same as the 4am auto-run; honors the disable label)."""
    def _execute():
        return _request("POST", "/watchtower/run", timeout=config.WATCHTOWER_RUN_TIMEOUT)

    return pending_actions.register(
        "watchtower.run",
        "Run Watchtower once on demand: pulls newer images for all managed containers and RECREATES the ones "
        "with updates (same as the nightly 4am run; claudediscordbot is excluded by label). Several containers "
        "may briefly restart.",
        _execute,
        user=user,
        destructive=True,
        metadata={"service": "watchtower", "action": "run_once"},
    )


def git_status():
    """Read-only. Show uncommitted homelab-config changes + the latest commit (local repo)."""
    try:
        data = _request("GET", "/git/status")
    except HostOpsError as exc:
        return f"Could not get git status: {exc}"
    out = (data.get("output") or "").strip()
    return f"Homelab-config git status:\n{out}" if out else "Working tree clean (nothing to commit)."


def git_commit_preview(message, user=None):
    """Confirm-gated LOCAL git commit (no push) of the current homelab-config changes. Push stays manual."""
    message = (message or "").strip()
    if not message:
        return {"status": "error", "message": "git_commit needs a commit message."}

    def _execute():
        return _request("POST", "/git/commit", json_body={"message": message}, timeout=60)

    return pending_actions.register(
        "git.commit",
        f"Commit the current homelab-config changes to the LOCAL git repo (NO push) with message:\n  "
        f"\"{message}\"\nReview with git_status and push to GitHub yourself when ready.",
        _execute,
        user=user,
        destructive=False,
        metadata={"service": "git", "action": "commit"},
    )


def caddy_reload_preview(user=None):
    """Confirm-gated Caddy config reload (graceful, no downtime) — apply Caddyfile edits made via write_config."""
    def _execute():
        return _request("POST", "/caddy/reload")

    return pending_actions.register(
        "caddy.reload",
        "Reload Caddy's config (caddy reload --config /etc/caddy/Caddyfile) to apply any Caddyfile edits. "
        "Graceful — no proxy downtime.",
        _execute,
        user=user,
        destructive=False,
        metadata={"service": "caddy", "action": "reload"},
    )


def pihole_restartdns_preview(user=None):
    """Confirm-gated Pi-hole DNS restart — apply lan.list / DNS changes. Brief DNS resolution blip."""
    def _execute():
        return _request("POST", "/pihole/restartdns")

    return pending_actions.register(
        "pihole.restartdns",
        "Reload Pi-hole's DNS (pihole reloaddns) to apply lan.list / custom-DNS changes — updates the lists and "
        "flushes the cache without bouncing the DNS server.",
        _execute,
        user=user,
        destructive=False,
        metadata={"service": "pihole", "action": "restartdns"},
    )


# --- Project #12 Phase 0: self-deploy (the orchestrator rebuilding + redeploying itself) ----------------
# The actual safe-deploy state machine lives in the EXTERNAL broker (hostops_deploy.py) so a bad deploy
# can't take down the thing doing the deploying. These thin helpers just drive it: start (confirm-gated),
# poll status, promote-to-live (a second confirm = the manual gate), and cancel (immediate, like Ctrl-C).

def self_deploy_preview(workspace="orchestrator-real", user=None):
    """Confirm-gated start of a canary-first self-deploy: promote the workspace's source into canonical,
    build+health-check a CANARY copy (live untouched), then pause for a manual 'promote to live'. HIGH risk:
    it rebuilds the orchestrator itself. Auto-rollback guards the live cutover."""
    workspace = (workspace or "orchestrator-real").strip()

    def _execute():
        return _request("POST", "/deploy/start", json_body={"workspace": workspace, "user": user or ""})

    return pending_actions.register(
        "self.deploy",
        (f"Start a SELF-DEPLOY from the '{workspace}' workspace. This promotes its changed Python/tests into "
         "the orchestrator's canonical source, builds a CANARY copy on :8101 and health-checks it (the LIVE "
         "orchestrator is NOT touched yet), then pauses for your manual 'promote to live'. On promote it "
         "rebuilds + recreates the live orchestrator with health-gated AUTO-ROLLBACK. You can cancel anytime."),
        _execute,
        user=user,
        destructive=True,
        metadata={"service": "deploy", "action": "start", "workspace": workspace, "self_deploy": True},
    )


def deploy_status_data(job=None):
    """Structured read-only deploy status for tools and the Phase-1 browser watcher."""
    try:
        return _request("GET", "/deploy/status", params={"job": job} if job else None)
    except HostOpsError as exc:
        return {"error": f"Could not get deploy status: {exc}"}


def deploy_status(job=None):
    """Read-only. Current self-deploy phase, file diff, awaiting-promote flag, and recent log lines."""
    data = deploy_status_data(job)
    if data.get("error"):
        return data["error"]
    diff = data.get("diff") or {}
    lines = [f"Deploy {data.get('job')} — phase: {data.get('phase')}"]
    if diff.get("changed") or diff.get("added"):
        lines.append(f"  changes: {len(diff.get('changed', []))} modified, {len(diff.get('added', []))} added")
    if data.get("awaiting_promote"):
        lines.append("  ⏸ canary is healthy — awaiting your 'promote to live' (deploy_promote) or cancel.")
    if data.get("error"):
        lines.append(f"  error: {data['error']}")
    log_tail = data.get("logs") or []
    if log_tail:
        lines.append("  recent:\n    " + "\n    ".join(log_tail[-12:]))
    return "\n".join(lines)


def deploy_promote_preview(job=None, user=None):
    """Confirm-gated manual 'promote to live' — releases a canary-verified deploy into the live cutover
    (rebuild + recreate the live orchestrator) with health-gated auto-rollback. HIGH risk."""
    def _execute():
        return _request("POST", "/deploy/promote", json_body={"job": job or ""})

    return pending_actions.register(
        "self.deploy.promote",
        ("PROMOTE the canary-verified deploy TO LIVE: rebuild + recreate the live orchestrator from the "
         "already-validated source. Brief orchestrator blip. If the live health-check fails it AUTO-ROLLS-BACK "
         "to the previous version. Only promote after deploy_status shows the canary healthy."),
        _execute,
        user=user,
        destructive=True,
        metadata={"service": "deploy", "action": "promote", "self_deploy": True},
    )


def deploy_cancel(job=None):
    """Immediate (NOT confirm-gated) cancel of an in-flight self-deploy — like Ctrl-C. The broker unwinds at
    the next safe boundary: pre-cutover it restores canonical + tears down the canary; post-cutover it rolls
    back. A cancel can never leave a half-applied deploy live."""
    try:
        data = _request("POST", "/deploy/cancel", json_body={"job": job} if job else {})
    except HostOpsError as exc:
        return f"Could not cancel deploy: {exc}"
    if data.get("error"):
        return data["error"]
    return f"Cancellation requested for deploy {data.get('cancelling')}. Poll deploy_status to watch it unwind."


# --- Registered-host SSH admin (Project #34/#35) -------------------------------------------------------
# Thin client over the broker's /host endpoints. The orchestrator NEVER holds a key or opens a socket; it
# names a REGISTERED host + a command and the sidecar runs it. Reads go through host_admin_run (the broker
# refuses obviously-destructive commands); mutations go through host_admin_exec_preview -> pending_actions
# (admin-gate + confirm + audit), the same path every other write tool here uses.

def host_admin_list():
    """Read-only. Registered hosts the broker can administer over SSH (name, user@host, description).
    Credentials stay in the broker and are never shown."""
    try:
        data = _request("GET", "/host/list")
    except HostOpsError as exc:
        return f"Could not reach the host-ops broker: {exc}"
    hosts = data.get("hosts", [])
    if not hosts:
        return "No hosts are registered for SSH admin."
    lines = [f"{h.get('name')} ({h.get('user')}@{h.get('host')})"
             + (f" — {h['description']}" if h.get("description") else "") for h in hosts]
    return "Registered admin hosts:\n" + "\n".join(lines)


def _format_host_result(host, data):
    if data.get("error"):
        return f"[{host}] broker error: {data['error']}"
    rc = data.get("exit_code")
    out = (data.get("output") or "").strip()
    head = f"[{host}] exit_code={rc}"
    return f"{head}\n{out}" if out else f"{head} (no output)"


def host_admin_run(host, command):
    """Read/inspection command on a registered host over SSH (no confirm). The broker refuses commands that
    look destructive — use host_admin_exec for anything that changes the host."""
    host = (host or "").strip()
    command = (command or "").strip()
    if not host or not command:
        return "host_admin_run needs a registered host name and a command (see host_admin_list)."
    try:
        data = _request("POST", "/host/exec",
                        json_body={"host": host, "command": command, "destructive": False},
                        timeout=config.HOSTOPS_HOST_EXEC_TIMEOUT)
    except HostOpsError as exc:
        return f"host_admin_run failed: {exc}"
    return _format_host_result(host, data)


def host_admin_exec_preview(host, command, user=None):
    """Confirm-gated administrative command on a registered host over SSH (any command, incl. destructive).
    Runs only after the user approves and confirm_action is called."""
    host = (host or "").strip()
    command = (command or "").strip()
    if not host or not command:
        return {"status": "error", "message": "host_admin_exec needs a registered host and a command."}

    def _execute():
        return _request("POST", "/host/exec",
                        json_body={"host": host, "command": command, "destructive": True},
                        timeout=config.HOSTOPS_HOST_EXEC_TIMEOUT)

    return pending_actions.register(
        "host.exec",
        (f"Run an ADMINISTRATIVE command on host '{host}' over SSH (as the runner's registered identity):\n"
         f"    {command}\n"
         "This can change or damage that host. It runs only after you approve and confirm_action is called."),
        _execute,
        user=user,
        destructive=True,
        metadata={"service": "hostadmin", "action": "exec", "host": host, "command": command[:200]},
    )
