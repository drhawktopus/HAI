"""Long soak: catch the NEXT frodo dropout with a timestamp, instead of inferring its cause.

Two clean sweeps prove nothing about an INTERMITTENT fault -- absence of the event during a
60-second window is not absence of the event. The only way to characterise it is to be watching
when it happens, and to record what "happens" actually means: did it stop answering ARP (radio
gone), or answer ping but refuse:8801 (agent died)? Those are different faults with different
fixes, and from the outside they have looked identical so far.

Runs ON SUNDAE, detached, writing a log. That is deliberate: it must outlive any chat session,
and it is strictly READ-ONLY -- it pings and it writes its own log, it never touches the fleet,
the queue or any config. A session-side watcher may subscribe; it may not supervise.

Log line per STATE CHANGE only (not per poll), so the file stays readable for days and every line
is an event worth reading:
    T01:02:03 frodo UP->DOWN ping=fail port8801=fail (up for 4213s)
"""
import os
import socket
import subprocess
import sys
import time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # the cameras folder
from cam_settings import tablets

NODES = tablets()   # every network tablet in the Fellowship roster
LOG = "/tmp/link_soak.log"
PERIOD = 15


def ping_ok(ip):
    try:
        p = subprocess.run(["ping", "-c", "2", "-W", "2", ip],
                           capture_output=True, text=True, timeout=15)
        return p.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def port_ok(ip, port=8801):
    s = socket.socket()
    s.settimeout(4)
    try:
        s.connect((ip, port))
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        s.close()


def log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()


state = {}
since = {}
log("=== soak started, %d nodes, %ds period ===" % (len(NODES), PERIOD))
while True:
    for host, ip in sorted(NODES.items()):
        p = ping_ok(ip)
        q = port_ok(ip) if p else False
        # UP means the DETECTOR is reachable; ping-only is its own state, because "radio gone"
        # and "agent dead" are different faults and collapsing them loses the diagnosis.
        now = "UP" if (p and q) else ("PING_ONLY" if p else "DOWN")
        prev = state.get(host)
        if prev is None:
            state[host] = now
            since[host] = time.time()
            log("%-8s baseline %s" % (host, now))
            continue
        if now != prev:
            held = time.time() - since[host]
            log("%-8s %s->%-9s ping=%-4s port8801=%-4s  (was %s for %.0fs)"
                % (host, prev, now, "ok" if p else "fail", "ok" if q else "fail", prev, held))
            state[host] = now
            since[host] = time.time()
    time.sleep(PERIOD)
