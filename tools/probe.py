"""Probe host reachability via multiple transports.

A single failed probe indicates unreachability *by that specific method*, not
global host failure. The function only returns exit code 1 (failure) if *all*
supported transports fail. Exit code 0 means at least one transport succeeded.

If all probes fail, the status is reported as INCONCLUSIVE rather than DOWN,
indicating a potential issue with the probing mechanism or network path rather
than the host itself being down.

Usage:
    python probe.py <host>
    python probe.py <host> --class gpu"""
import json
import socket
import subprocess
import sys
import urllib.request

import lab_settings as S

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# The addresses are settings; the class says which transports are meaningful for that box.
# A name in no table cannot be measured, and that is said rather than guessed.
def host_table():
    """name -> (address, class) for every box this tool may probe: the fleet, the tablets,
    the appliances and the head. Empty tables mean nothing is linked, so nothing is probed."""
    classes = S.setting_json("HOST_CLASSES", {})
    out = {}
    for name, addr in S.fleet_hosts().items():
        out[name] = (addr, classes.get(name, "gpu"))
    for name, addr in S.tablet_hosts().items():
        out[name] = (addr, "tablet")
    for name, addr in S.setting_json("APPLIANCE_HOSTS", {}).items():
        out[name] = (addr, "appliance")
    head = S.head_url()
    if head:
        host = head.split("//", 1)[-1].split("/")[0]
        # The head box is known by a name in the lab; HEAD_NAME names it so a person can probe
        # it by the name they already use.
        out[S.setting("HEAD_NAME", "head")] = (host.split(":")[0], "head")
    return out


def head_base():
    """The head's own URL, for the transport that needs it whole."""
    return S.head_url()


# class -> [(label, kind, detail)]. The ports here are service ports, not addresses; the head's
# own check is built from HAI_URL so its port comes from the setting too.
ORCH_PORT = S.setting("ORCH_PORT", "8099")
OLLAMA_PORT = S.setting("OLLAMA_PORT", "11434")

TRANSPORTS = {
    "gpu": [("ssh", "tcp", 22), ("ollama", "http", "http://{ip}:%s/api/version" % OLLAMA_PORT),
            ("smb", "tcp", 445), ("icmp", "ping", None)],
    "linux": [("ssh", "tcp", 22), ("orchestrator", "http", "http://{ip}:%s/health" % ORCH_PORT),
              ("icmp", "ping", None)],
    "tablet": [("ssh-8022", "tcp", 8022), ("adb", "adb", None), ("icmp", "ping", None)],
    "appliance": [("dns", "tcp", 53), ("http", "tcp", 80), ("icmp", "ping", None)],
    "node": [("ssh", "tcp", 22), ("icmp", "ping", None)],     # a plain Linux node with no service of its own (rigby)
}


def transports(klass, ip):
    """The transports meaningful for a class. The head's address and port come from HAI_URL,
    so its check uses that URL rather than a guessed port."""
    if klass == "head":
        base = head_base()
        return [("orchestrator", "http", base + "/health" if base else "http://{ip}/health"),
                ("ssh", "tcp", 22), ("icmp", "ping", None)]
    return TRANSPORTS.get(klass, TRANSPORTS["gpu"])


def tcp(ip, port, timeout=4):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True, f"port {port} open"
    except OSError as exc:
        return False, f"port {port}: {exc.__class__.__name__}"


def http(ip, url, timeout=6):
    try:
        with urllib.request.urlopen(url.format(ip=ip), timeout=timeout) as r:
            body = r.read(120).decode("utf-8", "replace").strip().replace("\n", " ")
            return True, f"HTTP {r.status} {body[:70]}"
    except Exception as exc:
        return False, f"{exc.__class__.__name__}"


def ping(ip, _d=None):
    flag = "-n" if sys.platform.startswith("win") else "-c"
    try:
        r = subprocess.run(["ping", flag, "2", "-w", "2000", ip],
                           capture_output=True, text=True, timeout=15)
        # Windows ping returns exit code 0 for 'Destination host unreachable';
        # check for 'ttl=' in output to verify a real echo reply was received.
        ok = r.returncode == 0 and "ttl=" in (r.stdout or "").lower()
        return ok, "replies" if ok else ("no echo reply (%s)" % ("unreachable" if r.returncode == 0 else "timed out"))
    except Exception as exc:
        return False, exc.__class__.__name__


def adb(ip, _d=None):
    for exe in (S.setting("ADB_EXE", ""), "adb"):      # the lab's adb (a setting), else the one on PATH
        if not exe:
            continue
        try:
            r = subprocess.run([exe, "-s", f"{ip}:5555", "shell", "echo", "ok"],
                               capture_output=True, text=True, timeout=20)
            if r.returncode == 0 and "ok" in (r.stdout or ""):
                return True, "adb shell responds"
            return False, (r.stderr or r.stdout or "no response").strip()[:60]
        except FileNotFoundError:
            continue
        except Exception as exc:
            return False, exc.__class__.__name__
    return False, "adb not found"


RUNNERS = {"tcp": tcp, "http": http, "ping": ping, "adb": adb}


def addresses(host):
    """[(ip, class)] to probe for a name, or [] when nothing can be measured. A moving host with multiple NICs must be probed on every candidate address in sshto.CANDIDATES to avoid false negatives from a single IP failing."""
    table = host_table()
    if host in table:
        return [table[host]]
    try:
        import sshto
        cands = sshto.CANDIDATES.get(host)
    except Exception:                                          # noqa: BLE001 - no sshto beside us: the tables only
        cands = None
    if cands:
        return [(ip, "node") for ip in cands]
    try:
        return [(socket.gethostbyname(host), "gpu")]
    except OSError:
        return []


def probe(host, host_class=None, quiet=False):
    targets = addresses(host)
    if not targets:
        if not quiet:
            print(f"VERDICT: NOT PROBED. '{host}' is in no table here and does not resolve - NOTHING was measured, "
                  f"so this says nothing about the machine. Use its address, or add it to FLEET_HOSTS / "
                  f"FELLOWSHIP_ROSTER / APPLIANCE_HOSTS, or sshto.CANDIDATES.")
        return {}
    results = {}
    for ip, klass in targets:
        if host_class:
            klass = host_class
        tag = f"@{ip}" if len(targets) > 1 else ""
        if not quiet:
            print(f"probing {host} ({ip}) as class '{klass}'")
        for label, kind, detail in transports(klass, ip):
            ok, note = RUNNERS[kind](ip, detail)
            results[label + tag] = {"ok": ok, "note": note}
            if not quiet:
                print(f"  {'ANSWERED' if ok else 'no       '}  {label + tag:<22} {note}")
    answered = [k for k, v in results.items() if v["ok"]]
    if not quiet:
        print()
        if answered:
            print(f"VERDICT: {host} is UP -- answered on: {', '.join(answered)}")
        else:
            print(f"VERDICT: INCONCLUSIVE. {host} answered NONE of "
                  f"{', '.join(results)}.\n"
                  f"That is 'unreachable by every method tried', which is still not proof the "
                  f"machine is dead or faulty. Check power/console before saying so.")
    return results


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    klass = None
    if "--class" in sys.argv:
        klass = sys.argv[sys.argv.index("--class") + 1]
    if not args:
        print("usage: python probe.py <host> [--class gpu|linux|tablet|appliance]")
        print("hosts: " + ", ".join(host_table()))
        sys.exit(2)
    res = probe(args[0], klass)
    sys.exit(2 if not res else 0 if any(v["ok"] for v in res.values()) else 1)   # 2 = nothing was measured
