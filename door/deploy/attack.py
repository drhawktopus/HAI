"""Live attacks on Uhura. Every line states what was expected and what happened. Non-zero exit on any failure.

PUBLIC runs through the droplet from a non-vendor address. DIRECT goes straight at the container on the LAN,
to prove Uhura holds without the droplet's help.
"""
import base64
import hashlib
import hmac
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from uhura_settings import setting  # noqa: E402
PUBLIC = setting("UHURA_PUBLIC_URL")
DIRECT = setting("UHURA_DIRECT_URL")
RPC = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
CALL = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_documents", "arguments": {}}}
HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "User-Agent": "uhura-attack"}
fails = checks = 0


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


opener = urllib.request.build_opener(NoRedirect)


def http(method, url, body=None, headers=None, form=False):
    data = None
    h = dict(HDR)
    if body is not None:
        data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
        if form:
            h["Content-Type"] = "application/x-www-form-urlencoded"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        r = opener.open(req, timeout=20)
        return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def check(name, ok, detail=""):
    global fails, checks
    checks += 1
    fails += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + name + (" | " + str(detail)[:150] if detail else ""), flush=True)


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def jwt(key, claims, alg="HS256"):
    head = b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    payload = b64(json.dumps(claims).encode())
    if alg == "none":
        return f"{head}.{payload}."
    sig = hmac.new(key, f"{head}.{payload}".encode(), hashlib.sha256).digest()
    return f"{head}.{payload}.{b64(sig)}"


HOST = setting("UHURA_SSH_HOST")
HOME = setting("UHURA_HOME", "/opt/uhura")
assert PUBLIC and DIRECT and HOST, "set UHURA_PUBLIC_URL, UHURA_DIRECT_URL and UHURA_SSH_HOST (deploy/uhura.env)"


def sundae(cmd, stdin=None):
    """A command on Uhura's host."""
    return subprocess.run(["ssh", HOST, cmd], input=stdin, capture_output=True, text=True, timeout=60)


# --- 1. Public surface from an address that is not a vendor's ---
DIRECT_ONLY = "--direct" in sys.argv
if not DIRECT_ONLY:
    for method, path, body in [("POST", "/mcp", RPC), ("GET", "/mcp", None), ("POST", "/register", {"client_name": "x", "redirect_uris": ["https://evil.test/cb"]}),
                               ("POST", "/token", {"grant_type": "authorization_code"}), ("GET", "/status", None), ("GET", "/mcp/../status", None)]:
        s, _, _ = http(method, PUBLIC + path, body)
        check(f"public {method} {path} blocked at the droplet", s == 403, s)
    s, _, b = http("GET", PUBLIC + "/.well-known/oauth-protected-resource/mcp")
    check("public discovery reachable, points at Uhura itself", s == 200 and urllib.parse.urlparse(PUBLIC).netloc in b, s)
    s, h, b = http("POST", PUBLIC + "/mcp", RPC, {"X-Forwarded-For": "160.79.104.1", "X-Real-IP": "160.79.104.1", "Forwarded": "for=160.79.104.1"})
    check("forged forwarding headers do not fake a vendor address", s == 403, s)
s, _, b = http("GET", (DIRECT if DIRECT_ONLY else PUBLIC) + "/.well-known/oauth-authorization-server")
meta = json.loads(b) if s == 200 else {}
check("metadata: S256 only, no client-supplied URLs", meta.get("code_challenge_methods_supported") == ["S256"] and not meta.get("client_id_metadata_document_supported"), meta.get("code_challenge_methods_supported"))

# --- 2. The bearer check, straight at the container ---
s, h, b = http("POST", DIRECT + "/mcp", RPC)
check("direct: no token gets 401", s == 401 and "resource_metadata" in h.get("www-authenticate", h.get("WWW-Authenticate", "")), s)
s, _, b = http("POST", DIRECT + "/mcp", CALL)
check("direct: tool call without token refused", s == 401 and "marker" not in b.lower(), s)
env = sundae("grep -E '^UHURA_JWT_KEY=' %s/.env | cut -d= -f2-" % HOME)
real_key = env.stdout.strip()
assert len(real_key) > 30, "could not read the signing key for the forgery tests"
now = int(time.time())
good = {"iss": PUBLIC + "/", "aud": PUBLIC + "/mcp", "sub": "x", "client_id": "x", "scope": "openid", "exp": now + 3600, "iat": now, "jti": "forged"}
forgeries = {
    "garbage": "garbage",
    "alg none": jwt(b"", good, "none"),
    "wrong signing key": jwt(b"wrong-key" * 8, good),
    "expired, wrong key": jwt(b"wrong-key" * 8, {**good, "exp": now - 10}),
    "literal key used raw": jwt(real_key.encode(), good),
    "literal key, wrong audience": jwt(real_key.encode(), {**good, "aud": "https://evil.test/mcp"}),
}
for name, token in forgeries.items():
    s, _, b = http("POST", DIRECT + "/mcp", CALL, {"Authorization": "Bearer " + token})
    check(f"direct: forged token ({name}) refused", s == 401 and "marker" not in b.lower(), s)
s, _, b = http("POST", DIRECT + "/mcp", CALL, {"X-Hai-User": "owner", "X-Forwarded-User": "owner@example.com", "X-Hai-Email": "owner@example.com"})
check("direct: trusted-looking identity headers mean nothing", s == 401, s)

# --- 3. Client registration and return addresses ---
evil = ["https://evil.test/cb", "https://claude.ai.evil.test/api/mcp/auth_callback", "https://claude.ai@evil.test/api/mcp/auth_callback",
        "https://chatgpt.com@evil.test/connector/oauth/x", "http://chatgpt.com/connector/oauth/x", "http://localhost:9999/cb", "javascript:alert(1)"]
for uri in evil:
    s, _, b = http("POST", DIRECT + "/register", {"client_name": "attack", "redirect_uris": [uri], "token_endpoint_auth_method": "none", "grant_types": ["authorization_code"], "response_types": ["code"]})
    if s >= 400:
        check(f"evil return address refused at registration: {uri}", True, s)
        continue
    cid = json.loads(b)["client_id"]
    q = urllib.parse.urlencode({"client_id": cid, "redirect_uri": uri, "response_type": "code", "state": "s", "code_challenge": "x" * 43, "code_challenge_method": "S256"})
    s2, h2, _ = http("GET", DIRECT + "/authorize?" + q)
    loc = h2.get("location", h2.get("Location", ""))
    check(f"evil return address cannot receive a redirect: {uri}", not loc.startswith(uri), (s2, loc[:70]))

# --- 4. The container itself ---
probe = (r"""
import os, socket, subprocess
STACK_DIR = %r
PROBES = %r
""" % (setting("UHURA_STACK_DIR", "/opt/stack"),
          [(h, int(p)) for h, p in (x.rsplit(":", 1) for x in setting("UHURA_EGRESS_PROBES").split(",") if x.strip())])) + r"""
def t(name, fn):
    try:
        print(name, 'ALLOWED', str(fn())[:60])
    except Exception as e:
        print(name, 'denied', type(e).__name__)
t('uid', lambda: (_ for _ in ()).throw(PermissionError()) if os.getuid()==0 else (_ for _ in ()).throw(ZeroDivisionError()))
t('write_app', lambda: open('/app/x','w').write('x'))
t('write_published', lambda: open('/published/x.md','w').write('x'))
t('docker_sock', lambda: open('/var/run/docker.sock').read(1))
t('host_opt', lambda: os.listdir(STACK_DIR))
t('ssh_key', lambda: os.listdir(os.path.expanduser('~/.ssh')))
t('library', lambda: os.listdir('/docs'))
t('mknod', lambda: os.mknod('/tmp/n', 0o600|0o020000, os.makedev(1,3)))
t('arrnet_hai', lambda: socket.create_connection(('orchestrator',8099),3))
t('arrnet_onboard', lambda: socket.create_connection(('onboarding-app',8466),3))
for host, port in [('172.17.0.1',8099),('172.18.0.1',8099),('169.254.169.254',80)] + PROBES:
    t(f'lan_{host}:{port}', lambda h=host, p=port: socket.create_connection((h,p),3))
t('change_firewall', lambda: subprocess.run(['iptables','-F'],check=True))
c = socket.create_connection(('oauth2.googleapis.com',443),5); c.close(); print('CONTROL google reachable')
"""
r = sundae("docker exec -i uhura python -", probe)
print(r.stdout.rstrip())
check("control: Google still reachable, so the blocks are not just a dead network", "CONTROL google reachable" in r.stdout, r.stderr[-80:])
for line in r.stdout.splitlines():
    name = line.split()[0]
    if name == "CONTROL":
        continue
    if name == "uid":
        check("container not root", "ZeroDivisionError" in line, line)
    else:
        check(f"container: {name} denied", " denied " in line, line)
assert r.stdout.strip(), "the container probe returned nothing, so its checks did not run"
s, _, b = http("GET", DIRECT + "/status")
check("inbound still works from the LAN while outbound is blocked", s == 200 and '"up":true' in b, s)

# --- 5. The off switch ---
if not DIRECT_ONLY:
    subprocess.run(["ssh", "do-pihole", "touch /etc/caddy/uhura.off"], capture_output=True, timeout=30)
    time.sleep(1)
    s_off, _, _ = http("GET", PUBLIC + "/.well-known/oauth-authorization-server")
    subprocess.run(["ssh", "do-pihole", "rm -f /etc/caddy/uhura.off"], capture_output=True, timeout=30)
    time.sleep(1)
    s_on, _, _ = http("GET", PUBLIC + "/.well-known/oauth-authorization-server")
    check("off switch closes it and removal reopens it", s_off == 503 and s_on == 200, (s_off, s_on))

# --- 6. Audit holds no contents or secrets ---
a = sundae("sudo -n cat " + HOME + "/state/audit.jsonl 2>/dev/null | wc -l; docker logs uhura 2>&1 | grep -c " + real_key[:16])
print("audit rows, key in logs:", a.stdout.split())
check("signing key never appears in container logs", a.stdout.split()[-1] == "0", a.stdout.split())

print(f"\nchecks={checks} fails={fails}")
assert checks > (20 if DIRECT_ONLY else 28), "too few checks ran"
sys.exit(1 if fails else 0)
