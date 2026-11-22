"""Board-level health readings shared by every GPU box's guard (LAB-TOOL-GAPS #2 fan control, #6 hotspot): ONE
derivation, imported by p20_health_daemon (BigHonker, Softserve) and p20_stall_rescue (Mothership). Pure functions plus
two read-only probes; nothing here acts."""
import subprocess

# EXACT image names: AsusFanControlService.exe runs on every box and is NOT ours (matching a name that merely
# mentions fan control is the 12a error).
FANCONTROL_IMAGES = ("fancontrol.exe", "fancontrol.service.exe")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fan_processes(tasklist_csv):
    """Image names in `tasklist /FO CSV /NH` output that are FanControl itself."""
    out = []
    for ln in (tasklist_csv or "").splitlines():
        name = ln.split(",", 1)[0].strip().strip('"')
        if name.lower() in FANCONTROL_IMAGES:
            out.append(name)
    return out


def last_row(path):
    """(header, newest row) of a CSV, reading only the file's tail; (None, None) when unreadable."""
    try:
        with open(path, "rb") as f:
            header = f.readline().decode("utf-8", "replace").strip().split(",")
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 16384))
            lines = [x for x in f.read().decode("utf-8", "replace").splitlines() if x.strip()]
        return header, (lines[-1].split(",") if lines else None)
    except OSError:
        return None, None


def check_fans(tasklist_csv, header, row):
    """(ok, note): FanControl running AND case fans reporting rpm. ok None = could not tell (never a verdict)."""
    if tasklist_csv is None:
        return None, "fan control: could not list processes"
    procs = fan_processes(tasklist_csv)
    if not procs:
        return False, "FAN CONTROL NOT RUNNING - case fans on motherboard defaults (RUNBOOK-GPU-Box-Fan-Control)"
    if not header or not row:
        return True, "fan control running (%s); telemetry unreadable, case fans not checked" % procs[0]
    rec = dict(zip(header, row))
    rpm_cols = [c for c in header if c.startswith("mb_fan") and c.endswith("_rpm")]
    if not rpm_cols:
        return True, "fan control running (%s); this logger records no case-fan rpm" % procs[0]
    spinning = sum(1 for c in rpm_cols if (_num(rec.get(c)) or 0) > 0)
    return True, "fan control running (%s); %d of %d case-fan headers reading rpm" % (procs[0], spinning, len(rpm_cols))


def hotspot_note(header, row):
    """Report-tier line: hotspot and the core->hotspot gap from the newest telemetry row, or why it is absent."""
    if not header or not row:
        return "hotspot: telemetry unreadable"
    rec = dict(zip(header, row))
    hot, core = _num(rec.get("hotspot_c")), _num(rec.get("temp_c"))
    if hot is None:
        return "hotspot: not logged on this box"
    return "hotspot %.0f C (core %s, gap %s)" % (hot, "%.0f" % core if core is not None else "?",
                                                 "%.0f" % (hot - core) if core is not None else "?")


def tasklist():
    """`tasklist /FO CSV /NH` text, or None when it could not run."""
    try:
        return subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                              timeout=20).stdout
    except Exception:                                                # noqa: BLE001
        return None
