"""Verify the hostops broker's host-admin path from INSIDE the hostops container (stdlib only).

Usage (from Sundae):   docker exec -i hostops python - < verify_broker.py
Runbook: RUNBOOK-HAI-Host-Admin-SSH.md § "Verify the whole path".

Checks, each printed PASS/FAIL, exit 1 on any FAIL:
  health          GET  /health                          -> 200
  list            GET  /host/list                       -> 200 with >= 1 registered host (positive control)
  read            POST /host/exec whoami on first host  -> 200, exit_code 0, non-empty output
  refuse          POST /host/exec 'rm -rf /tmp/x'       -> 400 mentioning destructive (negative control)
  unknown         POST /host/exec on a made-up host     -> 404
The token comes from the container's own HOSTOPS_TOKEN / HOSTOPS_TOKEN_FILE, exactly as the broker reads it.
"""
import json
import os
import sys
import urllib.error
import urllib.request

PORT = int(os.getenv("HOSTOPS_PORT", "9120"))
BASE = "http://127.0.0.1:%d" % PORT
TOKEN = ""
for src in (os.getenv("HOSTOPS_TOKEN", ""), os.getenv("HOSTOPS_TOKEN_FILE", "")):
    if not src:
        continue
    if os.path.exists(src):
        TOKEN = open(src, encoding="utf-8").read().strip()
    else:
        TOKEN = src.strip()
    if TOKEN:
        break


def call(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"x-hostops-token": TOKEN, "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            return e.code, {}


failed = 0


def report(name, ok, detail):
    global failed
    failed += 0 if ok else 1
    print("%-8s %s  %s" % (name, "PASS" if ok else "FAIL", detail))


st, d = call("GET", "/health")
report("health", st == 200, "HTTP %s" % st)

st, d = call("GET", "/host/list")
hosts = [h.get("name") for h in (d.get("hosts") or [])] if isinstance(d, dict) else []
report("list", st == 200 and len(hosts) >= 1, "HTTP %s hosts=%s" % (st, hosts))

if hosts:
    st, d = call("POST", "/host/exec", {"host": hosts[0], "command": "whoami", "destructive": False})
    report("read", st == 200 and d.get("exit_code") == 0 and bool((d.get("output") or "").strip()),
           "HTTP %s exit=%s output=%r" % (st, d.get("exit_code"), (d.get("output") or "").strip()[:40]))
    st, d = call("POST", "/host/exec", {"host": hosts[0], "command": "rm -rf /tmp/x", "destructive": False})
    report("refuse", st == 400 and "destructive" in (d.get("error") or ""), "HTTP %s %s" % (st, (d.get("error") or "")[:70]))
else:
    report("read", False, "no registered host to exercise")
    report("refuse", False, "no registered host to exercise")

st, d = call("POST", "/host/exec", {"host": "no-such-host-zz", "command": "whoami", "destructive": False})
report("unknown", st == 404, "HTTP %s %s" % (st, (d.get("error") or "")[:60]))

print("VERDICT:", "BROKER OK" if failed == 0 else "%d FAILED" % failed)
sys.exit(1 if failed else 0)
