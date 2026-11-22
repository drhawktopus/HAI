r"""GPU health reporter: ask this box's own driver whether its card is alive, and tell the orchestrator.

Runs as a scheduled task on every GPU box; installed copy at C:\ProgramData\HAI\gpu_reporter.py.
Because the orchestrator cannot query remote drivers directly, this agent must push the verdict (POST /fleet/gpu-state).

NVIDIA boxes: nvidia-smi is the authoritative source. Windows status codes and network services can falsely report
health during GPU loss, so only the driver's explicit error state is treated as a failure.

AMD boxes: Uses Windows device manager status. This is a weaker signal (cannot detect hung GPUs like nvidia-smi)
but is the best available vendor-independent check. 

Every verdict includes the driver version to detect unannounced driver changes.

Fail-safe logic: Only explicit driver-reported failures are reported as LOST. Timeouts, missing tools, or ambiguous
answers are NOT reported to prevent false positives that could accidentally remove healthy hosts from the fleet.

The head's address is the HAI_URL setting; a box with no head configured reports nothing."""
import json
import os
import re
import socket
import subprocess
import sys
import urllib.request

try:
    import lab_settings as S          # the workspace's ONE reader
except ImportError:                  # noqa: BLE001 - this file also runs as the deployed copy in C:\ProgramData\HAI
    S = None


def _head():
    return S.head_url() if S else os.environ.get("HAI_URL", "").rstrip("/")


ORCH = _head() + "/fleet/gpu-state" if _head() else ""

# Phrases nvidia-smi emits when the device is gone. Matched case-insensitively.
LOST = ("gpu is lost",
        "no devices were found",
        "unable to determine the device handle",
        "driver/library version mismatch",
        "has fallen off the bus")

# Every AMD (vendor 1002) display adapter Windows knows, present or phantom, with its driver version when present.
AMD_PNP_PS = r"""
$d = @(Get-PnpDevice -Class Display -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -like 'PCI\VEN_1002*' })
$out = foreach ($x in $d) {
  $ver = $null
  if ($x.Present) { $ver = (Get-PnpDeviceProperty -InstanceId $x.InstanceId -KeyName DEVPKEY_Device_DriverVersion -ErrorAction SilentlyContinue).Data }
  [pscustomobject]@{ name = $x.FriendlyName; status = "$($x.Status)"; problem = [int]$x.Problem; present = [bool]$x.Present; driver = $ver; id = $x.InstanceId }
}
ConvertTo-Json -InputObject @($out) -Compress
"""


def nvidia_probe():
    """(healthy | None, detail) from nvidia-smi; None when it cannot be asked or is ambiguous."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,pstate,temperature.gpu,utilization.gpu",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return None, "nvidia-smi not installed"
    except subprocess.TimeoutExpired:
        return None, "nvidia-smi timed out - no verdict"
    blob = ((r.stdout or "") + " " + (r.stderr or "")).strip()
    low = blob.lower()
    for phrase in LOST:
        if phrase in low:
            return False, re.sub(r"\s+", " ", blob)[:300]
    if r.returncode != 0:
        # Non-zero without a known phrase is ambiguous: report nothing rather than guess.
        return None, "nvidia-smi rc=%d: %s" % (r.returncode, re.sub(r"\s+", " ", blob)[:160])
    if not blob:
        return None, "nvidia-smi returned nothing - no verdict"
    return True, re.sub(r"\s+", " ", blob)[:200]


def amd_verdict(devs):
    """(healthy | None, detail) from Windows' records of the AMD display adapters (AMD_PNP_PS rows)."""
    if not devs:
        return None, "no AMD display adapter known to Windows - no verdict"
    present = [d for d in devs if d.get("present")]
    ok = [d for d in present if d.get("status") == "OK" and not d.get("problem")]
    if ok:
        d = ok[0]
        return True, "%s ok, driver %s (windows-pnp)" % (d.get("name"), d.get("driver") or "?")
    bad = [d for d in present if d.get("status") == "Error" or d.get("problem")]
    if bad:
        d = bad[0]
        return False, "%s status %s, problem code %s, driver %s (windows-pnp)" % (
            d.get("name"), d.get("status"), d.get("problem"), d.get("driver") or "?")
    if not present:
        return False, "no AMD display adapter on the bus: Windows holds only phantom records (%s) (windows-pnp)" % (
            ", ".join(sorted({str(d.get("name")) for d in devs}))[:200])
    return None, "AMD adapter in an unclear state (%s) - no verdict" % (
        "; ".join("%s %s/%s" % (d.get("name"), d.get("status"), d.get("problem")) for d in present)[:200])


def amd_probe():
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", AMD_PNP_PS],
                           capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return None, "device query failed (%s) - no verdict" % type(e).__name__
    if r.returncode != 0 or not (r.stdout or "").strip():
        return None, "device query rc=%d - no verdict" % r.returncode
    try:
        devs = json.loads(r.stdout)
    except ValueError:
        return None, "device query returned non-JSON - no verdict"
    if isinstance(devs, dict):
        devs = [devs]
    return amd_verdict(devs)


def probe():
    """(healthy | None, detail, source)."""
    healthy, detail = nvidia_probe()
    if healthy is None and detail == "nvidia-smi not installed":
        healthy, detail = amd_probe()
        return healthy, detail, "windows-pnp"
    return healthy, detail, "nvidia-smi"


def main(argv):
    host = (argv[1] if len(argv) > 1 else socket.gethostname()).strip().lower()
    healthy, detail, source = probe()
    if healthy is None:
        print("NO VERDICT: %s" % detail)
        return 0
    body = json.dumps({"host": host, "healthy": healthy, "detail": detail, "source": source}).encode("utf-8")
    try:
        rq = urllib.request.Request(ORCH, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(rq, timeout=10) as resp:
            print("reported healthy=%s -> %s" % (healthy, resp.read().decode()[:120]))
    except Exception as e:
        print("report FAILED (%r) - local verdict was healthy=%s: %s" % (e, healthy, detail[:120]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
