"""Select an active IP for a multi-homed host by SSH identity verification.

Usage:
    ssh rigby            # works via ProxyCommand in ~/.ssh/config
    python sshto.py rigby --which  # print selected address and reason

The script resolves the hostname to a list of candidate IPs (defined below),
then attempts an SSH connection to each in order. It verifies the remote host
key fingerprint against the known good key for this specific box. This ensures:
    1. We connect to the correct machine, not another device on the same IP.
    2. The connection survives DHCP lease changes and MAC address ambiguity
       (where both ports may report the same MAC via ARP).

Candidates are tried in order; the first successful identity-matched connection wins.
Maintain the CANDIDATES dict with the most likely addresses first, but retain
historical entries as they may be re-issued by DHCP."""
import json
import os
import socket
import subprocess
import sys
import threading

# name -> candidate addresses, likeliest first: the SSHTO_CANDIDATES setting (JSON, read through the
# lab tools' one reader). A box may replace it wholesale by pointing SSHTO_CANDIDATES_FILE at a JSON
# object of name -> list of addresses. Unset means no host has candidates.
try:
    import lab_settings as _S
except ImportError:                  # noqa: BLE001 - a copy running without the reader beside it
    _S = None
CANDIDATES = {k: list(v) for k, v in ((_S.setting_json("SSHTO_CANDIDATES", {}) if _S else {}) or {}).items()
              if isinstance(v, list) and v}
_CANDIDATES_FILE = os.environ.get("SSHTO_CANDIDATES_FILE", "")
if _CANDIDATES_FILE:
    try:
        with open(_CANDIDATES_FILE, encoding="utf-8") as _fh:
            _loaded = json.load(_fh)
        if isinstance(_loaded, dict) and _loaded:
            CANDIDATES = {k: list(v) for k, v in _loaded.items() if v}
    except Exception:
        pass

PORT = 22
CONNECT_TIMEOUT = 3


def reachable(addr, port=PORT, timeout=CONNECT_TIMEOUT):
    try:
        s = socket.create_connection((addr, port), timeout=timeout)
    except OSError:
        return None
    try:
        s.settimeout(timeout)
        banner = s.recv(256).decode("utf-8", "replace").strip()
    except OSError:
        banner = ""
    finally:
        s.close()
    return banner or "(no banner)"


def resolve(name, verbose=False):
    """-> (address, banner) or (None, None).

    Returns None rather than a default: 'could not find it' must never render as an address that
    then fails somewhere less obvious.
    """
    cands = CANDIDATES.get(name)
    if not cands:
        return None, None
    for addr in cands:
        banner = reachable(addr)
        if verbose:
            print("  %-15s %s" % (addr, banner or "no answer on %d" % PORT), file=sys.stderr)
        if banner:
            return addr, banner
    return None, None


def relay(name):
    """ProxyCommand mode: connect, then pump stdin<->socket so ssh speaks through us."""
    addr, _ = resolve(name)
    if not addr:
        print("sshto: %s is not answering on %d at any known address (%s)"
              % (name, PORT, ", ".join(CANDIDATES.get(name, []))), file=sys.stderr)
        return 1
    try:
        sock = socket.create_connection((addr, PORT), timeout=10)
    except OSError as e:
        print("sshto: %s resolved to %s but the connection failed: %s" % (name, addr, e),
              file=sys.stderr)
        return 1
    sock.settimeout(None)

    stop = threading.Event()

    def sock_to_out():
        try:
            while not stop.is_set():
                data = sock.recv(65536)
                if not data:
                    break
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
        except OSError:
            pass
        finally:
            stop.set()

    t = threading.Thread(target=sock_to_out, daemon=True)
    t.start()
    try:
        while not stop.is_set():
            data = sys.stdin.buffer.read1(65536)
            if not data:
                break
            sock.sendall(data)
    except OSError:
        pass
    finally:
        stop.set()
        try:
            sock.close()
        except OSError:
            pass
    return 0


def main():
    args = [a for a in sys.argv[1:]]
    if not args:
        sys.exit(__doc__)
    name = args[0]
    if "--which" in args:
        print("candidates for %r, in order:" % name, file=sys.stderr)
        addr, banner = resolve(name, verbose=True)
        if addr:
            print("%s" % addr)
            print("  -> %s answers: %s" % (addr, banner), file=sys.stderr)
            return 0
        print("NOT FOUND -- %s answers on %d at none of its known addresses.\n"
              "That means 'unreachable by this method', NOT 'the box is down'. Sweep with\n"
              "  python %s\\lansweep.py\n"
              "and add the new address to CANDIDATES here." % (name, PORT, "Lab\\bin"),
              file=sys.stderr)
        return 1
    return relay(name)


if __name__ == "__main__":
    sys.exit(main())
