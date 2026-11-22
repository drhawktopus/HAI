"""Host-ops broker sidecar (stdlib only) — Project #11 Phase 4.

The ONE privileged place that touches the Docker Engine. It runs in a small container with the docker socket
mounted and exposes a NARROW, token-authed, whitelisted HTTP API on the arrnet network (NOT host-exposed).
The orchestrator stays socket-free and calls this for the few host/docker actions it needs; every future
capability (caddy reload, image pull, watchtower, pi-hole, git) is just another whitelisted endpoint here.

Security model:
  - token auth (x-hostops-token) — shared secret mounted from a root-only file; no token -> 401.
  - it talks to docker via the Engine API over /var/run/docker.sock using stdlib (no docker CLI, no deps).
  - it NEVER runs arbitrary commands. Only the endpoints below, and write actions refuse PROTECTED containers
    (itself, the orchestrator, the VPN netns owner, pi-hole) so a confirmed call can't sever the stack.
  - the orchestrator additionally admin-gates + confirm-gates every write tool on top of this.
"""
import json
import hmac
import os
import re

# the author address of the commits this sidecar makes (a setting; only a plain address is used)
GIT_EMAIL = os.environ.get("HOSTOPS_GIT_EMAIL", "").strip()
if not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", GIT_EMAIL):
    GIT_EMAIL = "hai@localhost"
import socket
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

import hostops_deploy

PORT = int(os.getenv("HOSTOPS_PORT", "9120"))
DOCKER_SOCK = os.getenv("DOCKER_SOCK", "/var/run/docker.sock")
TOKEN = ""
for _src in (os.getenv("HOSTOPS_TOKEN", ""), os.getenv("HOSTOPS_TOKEN_FILE", "")):
    if _src and os.path.isfile(_src):
        with open(_src) as _fh:
            TOKEN = _fh.read().strip()
            break
    elif _src:
        TOKEN = _src.strip()
        break

# Containers that must never be restarted/stopped via this broker (would sever the stack or the broker itself). A
# deployment adds its own through HOSTOPS_PROTECTED (e.g. a VPN container other services share a network with).
PROTECTED = {c.strip() for c in os.getenv(
    "HOSTOPS_PROTECTED", "hostops,orchestrator,pihole").split(",") if c.strip()}
LOGS_MAX_TAIL = int(os.getenv("HOSTOPS_LOGS_MAX_TAIL", "300"))
SOCK_TIMEOUT = int(os.getenv("HOSTOPS_SOCK_TIMEOUT", "180"))   # long enough for a one-off run to finish

# The Docker network the one-off containers join (it reaches the stack and the LAN).
ONEOFF_NETWORK = os.getenv("HOSTOPS_ONEOFF_NETWORK", "")   # the compose network a one-off joins; "" = none configured
# Watchtower on-demand (one-off --run-once) + git (local commit only, no push) one-off params.
WATCHTOWER_IMAGE = os.getenv("WATCHTOWER_IMAGE", "containrrr/watchtower:latest")
GIT_IMAGE = os.getenv("GIT_IMAGE", "alpine/git:latest")
GIT_REPO_BIND = os.getenv("GIT_REPO_BIND", "")   # <stack folder>:/repo; "" = the git routes are off
GIT_USER = os.getenv("GIT_USER", "1000:1000")   # the repo owner's uid:gid — clean commit ownership, no dubious-owner

# --- Registered-host SSH admin (Project #34/#35): runner-mediated general host administration. The
# orchestrator names a REGISTERED host + a command; the broker runs it over SSH inside a one-off container
# using a key it owns (bind-mounted into that container only — never into this sidecar's memory, never
# returned to the model). Registry is a small JSON of {name: {host,user,key,description}}; `key` is a HOST
# path the docker daemon bind-mounts into the exec container.
HOSTS_FILE = os.getenv("HOSTOPS_HOSTS_FILE", "/run/secrets/hostops_hosts.json")
SSH_IMAGE = os.getenv("HOSTOPS_SSH_IMAGE", GIT_IMAGE)          # alpine/git ships OpenSSH; reuse it (no new image)
SSH_NETWORK = os.getenv("HOSTOPS_SSH_NETWORK", ONEOFF_NETWORK)
HOST_EXEC_TIMEOUT = int(os.getenv("HOSTOPS_HOST_EXEC_TIMEOUT", "1200"))   # generous: a device flash can take minutes
TRANSCRIPT_REFRESH_TOKEN_FILE = os.getenv('UHURA_REFRESH_TOKEN_FILE', '/run/secrets/uhura_refresh_token')


def _transcript_refresh_authed(headers):
    """A liaison token authorizes only fixed transcript publication, never host administration."""
    try:
        with open(TRANSCRIPT_REFRESH_TOKEN_FILE) as f:
            token = f.read().strip()
    except OSError:
        return False
    return bool(token) and hmac.compare_digest(headers.get('X-Uhura-Liaison', ''), token)


def _refresh_transcripts(payload):
    if not isinstance(payload, dict) or set(payload) not in ({'request_id'},{'request_id','action'}):
        raise ValueError('Only a refresh request ID is accepted')
    request_id = payload['request_id']
    if not isinstance(request_id, str) or not re.fullmatch(r'[0-9a-f]{32}', request_id):
        raise ValueError('Invalid refresh request ID')
    action=payload.get('action')
    if action not in (None,'trigger','status'):raise ValueError('Invalid transcript task action')
    entry = _load_hosts().get('softserve-transcripts')
    if not entry:
        raise RuntimeError('Registered Softserve publisher is unavailable')
    # The registered key is forced server-side to refresh_via_ssh.py and has no
    # shell/forwarding/PTY authority. Its only input is this fixed verb + UUID.
    command = {None:'refresh-transcripts','trigger':'trigger-transcripts','status':'transcript-status'}[action]+' '+request_id
    code, output = _ssh_exec(entry, command)
    if code != 0:
        raise RuntimeError('Transcript publisher failed; publication completion is not confirmed')
    try:
        receipt = json.loads(output.strip().splitlines()[-1])
        if action is not None:
            if (receipt.get('status')!='requested' or receipt.get('request_id')!=request_id
                    or receipt.get('task')!='Uhura Transcripts'
                    or receipt.get('task_state') not in ('Ready','Running','Queued','Disabled','Unknown')
                    or not isinstance(receipt.get('started_new_instance'),bool)
                    or not isinstance(receipt.get('task_last_result'),int)):
                raise ValueError()
            return {key:receipt[key] for key in ('status','request_id','requested_utc','task','task_state',
                      'instance_id','started_new_instance','task_last_result','task_last_run_utc')}
        if (receipt.get('status') != 'completed' or receipt.get('request_id') != request_id
                or not re.fullmatch(r'[0-9a-f]{32}', receipt.get('generation', ''))):
            raise ValueError()
        return {key: receipt[key] for key in ('status', 'request_id', 'generation', 'snapshot_started_utc',
                                              'completed_utc', 'documents', 'stats')}
    except (ValueError, IndexError, KeyError, AttributeError, TypeError):
        raise RuntimeError('Publisher returned no valid completion receipt') from None


def _load_oneoff_routes():
    """POST routes an extension adds: every hostops_sidecar_ext_<name>.py beside this file may declare ONEOFF_ROUTES
    {path: fn(payload) -> {"image", "cmd", "binds", "env", "user", "result"}}. The extension's code fixes the image and
    command (a request never supplies them), so each route is as narrow as the broker's own endpoints. A broker with no
    such file has none of these routes."""
    import glob
    import importlib.util
    routes = {}
    here = os.path.dirname(os.path.abspath(__file__))
    for path in sorted(glob.glob(os.path.join(here, "hostops_sidecar_ext_*.py"))):
        spec = importlib.util.spec_from_file_location(os.path.splitext(os.path.basename(path))[0], path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        routes.update(getattr(mod, "ONEOFF_ROUTES", {}))
    return routes


ONEOFF_ROUTES = _load_oneoff_routes()


def _load_hosts():
    """Registered hosts (fresh each call so registry edits apply without a sidecar restart). {} on any error."""
    try:
        with open(HOSTS_FILE) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# Obvious mutation verbs. host_admin_run (read mode) refuses a command matching any of these (or a `>`
# redirect) and tells the caller to use the confirm-gated host_admin_exec. This is honest-mistake protection
# for an ADMIN-trusted model, not a sandbox against a hostile one; the general (confirm-gated) path runs
# anything. Device tools (heimdall/fastboot/adb) are deliberately NOT here so inspection reads stay
# frictionless — their mutating subcommands (flash/erase/format/push/install) are caught on their own.
DESTRUCTIVE_TOKENS = {
    "rm", "rmdir", "unlink", "shred", "mv", "cp", "dd", "mkfs", "mke2fs", "fdisk", "parted", "sgdisk",
    "flash", "flashing", "format", "erase", "wipe", "truncate", "chmod", "chown", "chgrp", "ln",
    "install", "push", "uninstall", "reboot", "shutdown", "poweroff", "halt", "init", "kill", "pkill",
    "killall", "tee", "apt", "apt-get", "dpkg", "pip", "pip3", "sh", "bash", "eval", "systemctl",
    "service", "mount", "umount",
}
_TOKEN_SPLIT = re.compile(r"[\s|;&()<>]+")


def _looks_destructive(command):
    """Return the matched token if a read-mode command looks mutating, else None. Catches piped/chained/
    absolute-path forms (`foo | rm`, `/bin/dd`, `a; mkfs`) and any output redirection."""
    if ">" in command:
        return "> (output redirection)"
    for tok in _TOKEN_SPLIT.split(command):
        base = tok.strip().rsplit("/", 1)[-1]
        if base in DESTRUCTIVE_TOKENS or base.startswith("mkfs"):   # mkfs.ext4 / mkfs.vfat / mkfs.f2fs ...
            return base
    return None


def _ssh_exec(entry, command):
    """Run `command` on a registered host over SSH inside a one-off container. The command is passed via env
    (HOST_CMD) so it can't inject the LOCAL sh; the remote login shell parses it. The key is bind-mounted
    read-only into the one-off ONLY. Returns (exit_code, combined_output); rc 255 means an ssh/transport
    failure."""
    key, user, host = entry.get("key"), entry.get("user"), entry.get("host")
    if not (key and user and host):
        raise RuntimeError("registry entry is incomplete (need key, user, host)")
    script = ("ssh -i /key -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null "
              "-o LogLevel=ERROR -o ConnectTimeout=10 -o BatchMode=yes "
              f"{user}@{host} -- \"$HOST_CMD\"")
    return _run_oneoff(SSH_IMAGE, [script], [f"{key}:/key:ro"], ["HOST_CMD=" + command],
                       "0:0", SSH_NETWORK, entrypoint=["sh", "-c"], wait_timeout=HOST_EXEC_TIMEOUT)


class _UnixHTTPConnection(HTTPConnection):
    """HTTPConnection over a unix domain socket (for the docker Engine API)."""
    def __init__(self, sock_path, timeout=None):
        super().__init__("localhost")
        self._sock_path = sock_path
        self._timeout = timeout or SOCK_TIMEOUT

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self._timeout)
        s.connect(self._sock_path)
        self.sock = s


def _docker(method, path, body=None, sock_timeout=None):
    conn = _UnixHTTPConnection(DOCKER_SOCK, timeout=sock_timeout)
    try:
        headers = {"Content-Type": "application/json"} if body is not None else {}
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, data
    finally:
        conn.close()


def _docker_exec(container, cmd):
    """Run a FIXED command inside a container via the Engine API (create exec -> start -> inspect exit code).
    cmd is hardcoded per endpoint — never user-supplied — so this is not an arbitrary-exec/shell capability.
    Returns (exit_code, output)."""
    create = json.dumps({"Cmd": cmd, "AttachStdout": True, "AttachStderr": True, "Tty": False}).encode()
    status, data = _docker("POST", f"/containers/{quote(container)}/exec", create)
    if status not in (200, 201):
        raise RuntimeError(f"exec create failed: HTTP {status}: {data[:200].decode('utf-8', 'replace')}")
    exec_id = json.loads(data).get("Id")
    if not exec_id:
        raise RuntimeError("exec create returned no id")
    start = json.dumps({"Detach": False, "Tty": False}).encode()
    status, out = _docker("POST", f"/exec/{quote(exec_id)}/start", start)
    if status not in (200, 201):
        raise RuntimeError(f"exec start failed: HTTP {status}")
    output = _demux_logs(out)
    status, ins = _docker("GET", f"/exec/{quote(exec_id)}/json")
    code = json.loads(ins).get("ExitCode") if status == 200 else None
    return code, output


def _demux_logs(raw):
    """Docker's non-TTY log stream frames each chunk as [stream(1) 0 0 0 size(4-be)][payload]. Strip the
    frame headers; if the data isn't framed (TTY containers), return it as-is."""
    out, i, n = [], 0, len(raw)
    try:
        while i + 8 <= n:
            stype = raw[i]
            size = int.from_bytes(raw[i + 4:i + 8], "big")
            if stype not in (0, 1, 2) or i + 8 + size > n:
                return raw.decode("utf-8", "replace")   # not framed -> raw
            out.append(raw[i + 8:i + 8 + size])
            i += 8 + size
        if i != n:
            return raw.decode("utf-8", "replace")
        return b"".join(out).decode("utf-8", "replace")
    except Exception:
        return raw.decode("utf-8", "replace")


def _pull_image(image):
    """Pull an image via the Engine API (POST /images/create). Reading the streamed response to EOF blocks
    until the pull finishes. Engine `create` does NOT auto-pull, so one-off runs call this on a missing image."""
    if ":" in image.rsplit("/", 1)[-1]:
        name, tag = image.rsplit(":", 1)
    else:
        name, tag = image, "latest"
    status, _ = _docker("POST", f"/images/create?fromImage={quote(name, safe='/')}&tag={quote(tag)}")
    if status not in (200, 201):
        raise RuntimeError(f"image pull failed for {image}: HTTP {status}")


def _run_oneoff(image, cmd, binds, env, user, network, entrypoint=None, wait_timeout=None):
    """Create -> start -> wait -> logs -> remove a one-off container (the `compose run --rm` equivalent).
    A network of "" and an empty bind in `binds` are refused: the setting behind them is not set on this head.
    Returns (exit_code, output). Used for extension routes / Watchtower run-once / git / self-deploy builds.
    The command shape is fixed per endpoint; the only request-supplied value is a git commit message, passed
    via env (not the command string), so it can't inject. wait_timeout lets a slow op (a `compose build`)
    outlast the default socket timeout without raising it for every call."""
    if network != "none" and not network:
        raise RuntimeError("HOSTOPS_ONEOFF_NETWORK is not set: no network for a one-off container")
    if any(not b for b in binds):
        raise RuntimeError("a bind for this one-off is not set (GIT_REPO_BIND?)")
    create = {
        "Image": image, "Cmd": cmd, "User": user, "Env": env,
        "HostConfig": {"Binds": binds, "NetworkMode": network, "AutoRemove": False},
    }
    if entrypoint is not None:
        create["Entrypoint"] = entrypoint
    body = json.dumps(create).encode()
    status, data = _docker("POST", "/containers/create", body)
    if status == 404 and b"No such image" in data:
        _pull_image(image)                                   # Engine create won't auto-pull; do it then retry
        status, data = _docker("POST", "/containers/create", body)
    if status not in (200, 201):
        raise RuntimeError(f"create failed: HTTP {status}: {data[:200].decode('utf-8','replace')}")
    cid = json.loads(data).get("Id")
    try:
        status, _ = _docker("POST", f"/containers/{cid}/start")
        if status not in (204, 200):
            raise RuntimeError(f"start failed: HTTP {status}")
        status, w = _docker("POST", f"/containers/{cid}/wait", sock_timeout=wait_timeout)
        code = json.loads(w).get("StatusCode") if status == 200 else None
        status, logs = _docker("GET", f"/containers/{cid}/logs?stdout=1&stderr=1&tail=400",
                               sock_timeout=wait_timeout)
        return code, _demux_logs(logs)
    finally:
        _docker("DELETE", f"/containers/{cid}?force=1")


def _container_summary():
    status, data = _docker("GET", "/containers/json?all=1")
    if status != 200:
        raise RuntimeError(f"docker ps failed: HTTP {status}")
    out = []
    for c in json.loads(data):
        name = (c.get("Names") or ["/?"])[0].lstrip("/")
        out.append({"name": name, "state": c.get("State"), "status": c.get("Status"),
                    "image": c.get("Image")})
    return sorted(out, key=lambda x: x["name"])


def _valid_name(name):
    return bool(name) and all(ch.isalnum() or ch in "-_." for ch in name)


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        if not TOKEN:
            return True   # no token configured -> open (dev only); deploy always sets one
        return self.headers.get("x-hostops-token", "") == TOKEN

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            self._json(200, {"ok": True, "protected": sorted(PROTECTED)})
            return
        if not self._authed():
            self._json(401, {"error": "bad or missing token"})
            return
        if u.path == "/docker/ps":
            try:
                self._json(200, {"containers": _container_summary()})
            except Exception as e:
                self._json(502, {"error": str(e)})
            return
        if u.path == "/docker/logs":
            q = parse_qs(u.query)
            name = q.get("name", [""])[0]
            tail = min(int(q.get("tail", ["100"])[0] or 100), LOGS_MAX_TAIL)
            if not _valid_name(name):
                self._json(400, {"error": "invalid container name"})
                return
            status, data = _docker("GET", f"/containers/{quote(name)}/logs?stdout=1&stderr=1&tail={tail}")
            if status == 404:
                self._json(404, {"error": f"no such container: {name}"})
                return
            if status != 200:
                self._json(502, {"error": f"docker logs failed: HTTP {status}"})
                return
            self._json(200, {"name": name, "tail": tail, "logs": _demux_logs(data)})
            return
        if u.path == "/git/status":
            try:
                code, out = _run_oneoff(
                    GIT_IMAGE,
                    ["cd /repo && git status --short && echo '----' && git log -1 --oneline"],
                    [GIT_REPO_BIND], [], GIT_USER, "none", entrypoint=["sh", "-c"])
            except Exception as e:
                self._json(502, {"error": str(e)})
                return
            self._json(200, {"exit_code": code, "output": (out or "").strip()[-3000:]})
            return
        if u.path == "/deploy/status":
            q = parse_qs(u.query)
            self._json(200, hostops_deploy.status(q.get("job", [None])[0]))
            return
        if u.path == "/host/list":
            hosts = _load_hosts()
            self._json(200, {"hosts": [{"name": n, "host": e.get("host"), "user": e.get("user"),
                                        "description": e.get("description", "")}
                                       for n, e in sorted(hosts.items())]})
            return
        self._json(404, {"error": "not found"})

    def _container_action(self, name, action):
        """stop/start/restart a container by name (action is fixed by the route, not request text)."""
        if not _valid_name(name):
            self._json(400, {"error": "invalid container name"})
            return
        if action in ("stop", "restart") and name in PROTECTED:
            self._json(403, {"error": f"{name} is protected and cannot be {action}ed via the broker"})
            return
        status, data = _docker("POST", f"/containers/{quote(name)}/{action}")
        if status == 404:
            self._json(404, {"error": f"no such container: {name}"})
            return
        if status not in (204, 200, 304):
            self._json(502, {"error": f"{action} failed: HTTP {status}: {data[:200].decode('utf-8','replace')}"})
            return
        self._json(200, {action: name})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == '/transcripts/refresh':
            if not _transcript_refresh_authed(self.headers):
                return self._json(401, {'error': 'bad or missing refresh token'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 128:
                    raise ValueError()
                payload = json.loads(self.rfile.read(length))
                return self._json(200, _refresh_transcripts(payload))
            except (ValueError, TypeError):
                return self._json(400, {'error': 'Invalid refresh request.'})
            except Exception:
                return self._json(502, {'error': 'Transcript publication did not complete.'})
        if not self._authed():
            self._json(401, {"error": "bad or missing token"})
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            payload = {}
        if u.path in ("/docker/restart", "/docker/stop", "/docker/start"):
            self._container_action((payload.get("name") or "").strip(), u.path.rsplit("/", 1)[1])
            return
        # Fixed-command exec endpoints (the command is hardcoded here, never from the request body).
        if u.path == "/caddy/reload":
            self._exec_endpoint("caddy", ["caddy", "reload", "--config", "/etc/caddy/Caddyfile"])
            return
        if u.path == "/pihole/restartdns":
            # Pi-hole v6's CLI dropped `restartdns`; `reloaddns` updates the lists + flushes the cache
            # (applies lan.list / custom-DNS edits) without bouncing FTL.
            self._exec_endpoint("pihole", ["pihole", "reloaddns"])
            return
        if u.path in ONEOFF_ROUTES:
            # an extension's fixed one-off run: its code picks the image and command; the request only picks
            # among the options that code offers (e.g. a dry-run)
            try:
                spec = ONEOFF_ROUTES[u.path](payload)
                code, out = _run_oneoff(spec["image"], spec["cmd"], spec.get("binds", []), spec.get("env", []),
                                        spec.get("user", "0:0"), ONEOFF_NETWORK)
            except Exception as e:
                self._json(502, {"error": str(e)})
                return
            self._json(200 if code == 0 else 502,
                       dict(spec.get("result", {}), exit_code=code, output=(out or "")[-3000:]))
            return
        if u.path == "/watchtower/run":
            try:
                code, out = _run_oneoff(WATCHTOWER_IMAGE, ["--run-once"],
                                        ["/var/run/docker.sock:/var/run/docker.sock"], [], "0:0",
                                        ONEOFF_NETWORK)
            except Exception as e:
                self._json(502, {"error": str(e)})
                return
            self._json(200 if code == 0 else 502, {"exit_code": code, "output": (out or "")[-3000:]})
            return
        if u.path == "/deploy/start":
            self._json(200, hostops_deploy.start((payload.get("workspace") or "").strip(),
                                                 (payload.get("user") or "").strip()))
            return
        if u.path == "/deploy/promote":
            self._json(200, hostops_deploy.promote((payload.get("job") or "").strip() or None))
            return
        if u.path == "/deploy/cancel":
            self._json(200, hostops_deploy.cancel((payload.get("job") or "").strip() or None))
            return
        if u.path == "/git/commit":
            msg = (payload.get("message") or "").strip()
            if not msg:
                self._json(400, {"error": "commit message is required"})
                return
            # MSG passed via env (NOT interpolated into the command) so the message can't inject shell.
            script = ('cd /repo && git add -A && '
                      'git -c user.name="HAI" -c user.email="%s" commit -m "$MSG"' % GIT_EMAIL)
            try:
                code, out = _run_oneoff(GIT_IMAGE, [script], [GIT_REPO_BIND],
                                        ["MSG=" + msg], GIT_USER, "none", entrypoint=["sh", "-c"])
            except Exception as e:
                self._json(502, {"error": str(e)})
                return
            text = (out or "").strip()
            nothing = "nothing to commit" in text.lower()
            self._json(200 if (code == 0 or nothing) else 502,
                       {"exit_code": code, "committed": code == 0, "nothing_to_commit": nothing,
                        "output": text[-1500:]})
            return
        if u.path == "/host/exec":
            host = (payload.get("host") or "").strip()
            command = payload.get("command") or ""
            destructive = payload.get("destructive") in (True, 1, "1", "true")
            hosts = _load_hosts()
            entry = hosts.get(host)
            if not entry:
                self._json(404, {"error": f"unknown host '{host}'; registered: {sorted(hosts)}"})
                return
            if not command.strip():
                self._json(400, {"error": "command is required"})
                return
            if not destructive:
                hit = _looks_destructive(command)
                if hit:
                    self._json(400, {"error": f"command looks destructive (matched {hit!r}); "
                                              "use the confirm-gated host_admin_exec for mutations"})
                    return
            try:
                code, out = _ssh_exec(entry, command)
            except Exception as e:
                self._json(502, {"error": str(e)})
                return
            self._json(200, {"host": host, "exit_code": code, "output": (out or "")[-6000:]})
            return
        self._json(404, {"error": "not found"})

    def _exec_endpoint(self, container, cmd):
        try:
            code, out = _docker_exec(container, cmd)
        except Exception as e:
            self._json(502, {"error": str(e)})
            return
        self._json(200 if code == 0 else 502,
                   {"container": container, "exit_code": code, "output": (out or "")[-1500:]})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    # The self-deploy executor runs `docker compose` build/up in one-off containers via _run_oneoff, with a
    # longer wait so a slow build can finish; health polling lives in hostops_deploy itself (stdlib http).
    hostops_deploy.set_runner(
        lambda *a, **k: _run_oneoff(*a, wait_timeout=hostops_deploy.BUILD_TIMEOUT, **k))
    # ThreadingHTTPServer so /deploy/status and /deploy/cancel stay responsive while a deploy job runs.
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
