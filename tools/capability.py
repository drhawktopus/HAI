"""Read a device's FULL capability list to a file and cite it.

Capabilities must be cited from complete enumerations, not truncated terminal output
(e.g., `head`), which can hide entries or misrepresent hardware support. This function
always writes the full list to an artifact file to prevent inference errors from
incomplete views.

    python capability.py gandalf codecs
    python capability.py mothership gpu

The tablet addresses are a setting (FELLOWSHIP_ROSTER); an unconfigured box has no tablets to
read, and says so rather than guessing an address.
"""
import os
import subprocess
import sys
import time

import lab_settings as S

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capability-dumps")   # beside this tool

TAB_KEY = S.setting("TABLET_SSH_KEY", "")     # the cluster key's path is a lab setting; empty = ssh's own default key
TABLETS = S.tablet_hosts()

# name -> (how to run it, the command). Every one returns the WHOLE enumeration.
SOURCES = {
    # Enumerate per-file to preserve provenance; a bare glob-cat merges
    # unrelated vendor configs (e.g. GSI generics), hiding which file is live.
    "codecs": ("tablet",
               'for f in /vendor/etc/media_codecs*.xml; do echo "### FILE: $f"; cat "$f"; done'),
    # What the device ACTUALLY offers at runtime beats any XML on disk. Prefer this for a
    # capability VERDICT; use "codecs" only to see how it is configured.
    "codecs_runtime": ("tablet", "cmd media.codec list 2>/dev/null || dumpsys media.metrics"),
    "props": ("tablet", "getprop"),
    "cpu": ("tablet", "cat /proc/cpuinfo"),
    "mem": ("tablet", "cat /proc/meminfo"),
    "gpu": ("ssh", "nvidia-smi -q"),
    "disks": ("ssh", "wmic logicaldisk get caption,size,freespace"),
    "models": ("http", "http://{ip}:11434/api/tags"),
}


def _run(argv, timeout=90):
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as exc:
        return 1, f"{exc.__class__.__name__}: {exc}"


def capability(host, source):
    if source not in SOURCES:
        raise SystemExit(f"unknown source {source!r}; known: {', '.join(SOURCES)}")
    how, cmd = SOURCES[source]

    if how == "tablet":
        ip = TABLETS.get(host, host)
        argv = ["ssh", "-i", TAB_KEY, "-p", "8022",
                "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=20", ip, cmd]
    elif how == "ssh":
        argv = ["ssh", host, cmd]
    else:
        raise SystemExit("http sources: use probe.py or curl; not wrapped yet")

    rc, out = _run(argv)

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(OUT_DIR, f"{host}-{source}-{stamp}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {host} {source}  rc={rc}  {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# command: {cmd}\n#\n")
        f.write(out)

    lines = out.splitlines()
    print(f"host      : {host}")
    print(f"source    : {source}  (rc={rc})")
    print(f"lines     : {len(lines)}")
    print(f"ARTIFACT  : {path}")
    print()
    if rc != 0:
        print("⚠ NON-ZERO EXIT. An error result and an empty result are DIFFERENT evidence -- an")
        print("  error means the question did not get asked, which proves nothing about the device.")
    elif not lines:
        print("⚠ EMPTY but successful. That means the key is valid and UNCONFIGURED, which is still")
        print("  not the same as the capability being absent. Ask what the device SUPPORTS.")
    else:
        print(f"Cite the artifact above, not this summary. Grep it -- do NOT pipe it into head:")
        print(f"    grep -i <term> \"{path}\"")
    return path, out


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python capability.py <host> <source>")
        print("sources: " + ", ".join(SOURCES))
        sys.exit(2)
    capability(sys.argv[1], sys.argv[2])
