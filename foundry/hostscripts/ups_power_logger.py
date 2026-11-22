"""Mothership whole-box power logger: reads the CyberPower GX1500U over USB HID (Windows' own hid.dll, SHARED handle -
PowerPanel keeps the device; no install, no shutdown-policy change) every 5 s and appends to
<FOUNDRY_ROOT>\\var\\state\\telemetry\\ups_power.csv, beside gpu_telemetry.csv, so per-order whole-box joules join by time.

Built for #34 (register B10 "Owed (energy)"): the menu's gpu_j is the CARD meter only and undercounts
CPU-heavy runs (Flash-Next on Strata runs the CPU at ~95 %). Run by the "UPS Power Logger" SYSTEM task
(install_ups_logger_task.ps1: AtStartup + 15-min repetition + IgnoreNew + RestartCount), so it self-heals.

Liveness = the CSV's own newest row. A failed read writes NOTHING (a gap is honest; a fake 0 W row would not be) and
reopens the device on the next tick. The UPS reports whatever is plugged into its battery outlets - on Mothership that
is the tower (plus whatever else shares those outlets; see the runbook before treating the number as tower-only)."""
import ctypes, os, sys, time
from datetime import datetime
from ctypes import wintypes as W

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lab_settings as S   # noqa: E402  the per-box values file beside this script (FOUNDRY_ROOT)
_ROOT = S.setting("FOUNDRY_ROOT", "")
if not _ROOT:
    sys.exit("ups_power_logger: FOUNDRY_ROOT is not set in the lab.env beside this script")
CSV = os.path.join(_ROOT, "var", "state", "telemetry", "ups_power.csv")
PERIOD_S = 5
HDR = "ts,epoch,active_w,apparent_va,load_pct,volts,runtime_s"
FIELDS = {(0x84, 0x34): "active_w", (0x84, 0x33): "apparent_va", (0x84, 0x35): "load_pct", (0x84, 0x30): "volts",
          (0x85, 0x68): "runtime_s"}
VID_PID = "vid_0764&pid_0601"

hid, sa = ctypes.WinDLL("hid"), ctypes.WinDLL("setupapi")
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = W.HANDLE
sa.SetupDiGetClassDevsW.restype = ctypes.c_void_p


class GUID(ctypes.Structure):
    _fields_ = [("D1", W.DWORD), ("D2", W.WORD), ("D3", W.WORD), ("D4", ctypes.c_ubyte * 8)]


class IFDATA(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("Guid", GUID), ("Flags", W.DWORD), ("Reserved", ctypes.c_void_p)]


class CAPS(ctypes.Structure):
    _fields_ = [("Usage", W.USHORT), ("UsagePage", W.USHORT), ("InputReportByteLength", W.USHORT),
                ("OutputReportByteLength", W.USHORT), ("FeatureReportByteLength", W.USHORT),
                ("Reserved", W.USHORT * 17), ("NumberLinkCollectionNodes", W.USHORT),
                ("NumberInputButtonCaps", W.USHORT), ("NumberInputValueCaps", W.USHORT),
                ("NumberInputDataIndices", W.USHORT), ("NumberOutputButtonCaps", W.USHORT),
                ("NumberOutputValueCaps", W.USHORT), ("NumberOutputDataIndices", W.USHORT),
                ("NumberFeatureButtonCaps", W.USHORT), ("NumberFeatureValueCaps", W.USHORT),
                ("NumberFeatureDataIndices", W.USHORT)]


class VCAPS(ctypes.Structure):          # HIDP_VALUE_CAPS (NotRange arm)
    _fields_ = [("UsagePage", W.USHORT), ("ReportID", ctypes.c_ubyte), ("IsAlias", ctypes.c_ubyte),
                ("BitField", W.USHORT), ("LinkCollection", W.USHORT), ("LinkUsage", W.USHORT),
                ("LinkUsagePage", W.USHORT), ("IsRange", ctypes.c_ubyte), ("IsStringRange", ctypes.c_ubyte),
                ("IsDesignatorRange", ctypes.c_ubyte), ("IsAbsolute", ctypes.c_ubyte), ("HasNull", ctypes.c_ubyte),
                ("Reserved", ctypes.c_ubyte), ("BitSize", W.USHORT), ("ReportCount", W.USHORT),
                ("Reserved2", W.USHORT * 5), ("UnitsExp", W.ULONG), ("Units", W.ULONG),
                ("LogicalMin", W.LONG), ("LogicalMax", W.LONG), ("PhysicalMin", W.LONG), ("PhysicalMax", W.LONG),
                ("Usage", W.USHORT), ("Reserved1", W.USHORT), ("StringIndex", W.USHORT), ("Reserved3", W.USHORT),
                ("DesignatorIndex", W.USHORT), ("Reserved4", W.USHORT), ("DataIndex", W.USHORT), ("Reserved5", W.USHORT)]


def find_path():
    g = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(g))
    hdev = sa.SetupDiGetClassDevsW(ctypes.byref(g), None, None, 0x12)   # DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    i = 0
    try:
        while True:
            ifd = IFDATA(cbSize=ctypes.sizeof(IFDATA))
            if not sa.SetupDiEnumDeviceInterfaces(ctypes.c_void_p(hdev), None, ctypes.byref(g), i, ctypes.byref(ifd)):
                return None
            need = W.DWORD()
            sa.SetupDiGetDeviceInterfaceDetailW(ctypes.c_void_p(hdev), ctypes.byref(ifd), None, 0,
                                                ctypes.byref(need), None)
            buf = ctypes.create_string_buffer(need.value)
            ctypes.cast(buf, ctypes.POINTER(W.DWORD))[0] = 8                # cbSize of the detail struct on x64
            sa.SetupDiGetDeviceInterfaceDetailW(ctypes.c_void_p(hdev), ctypes.byref(ifd), buf, need, None, None)
            p = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            if VID_PID in p.lower():
                return p
            i += 1
    finally:
        sa.SetupDiDestroyDeviceInfoList(ctypes.c_void_p(hdev))


class Ups:
    def __init__(self):
        path = find_path()
        if not path:
            raise OSError("no CyberPower %s HID interface" % VID_PID)
        h = k32.CreateFileW(path, 0x80000000 | 0x40000000, 0x1 | 0x2, None, 3, 0, None)   # R/W, shared
        if h in (None, W.HANDLE(-1).value):
            h = k32.CreateFileW(path, 0, 0x1 | 0x2, None, 3, 0, None)                      # query-only
        if h in (None, W.HANDLE(-1).value):
            raise OSError("CreateFileW failed (%d)" % ctypes.get_last_error())
        self.h, self.pp = h, ctypes.c_void_p()
        if not hid.HidD_GetPreparsedData(h, ctypes.byref(self.pp)):
            k32.CloseHandle(h)
            raise OSError("no preparsed data")
        caps = CAPS()
        hid.HidP_GetCaps(self.pp, ctypes.byref(caps))
        self.n = caps.FeatureReportByteLength
        cnt = W.USHORT(caps.NumberFeatureValueCaps)
        vc = (VCAPS * cnt.value)()
        hid.HidP_GetValueCaps(2, vc, ctypes.byref(cnt), self.pp)               # HidP_Feature = 2
        self.caps = {}
        for v in vc[:cnt.value]:
            name = FIELDS.get((v.UsagePage, v.Usage))
            if name and name not in self.caps:
                self.caps[name] = (v.UsagePage, v.Usage, v.ReportID, v.LinkCollection)
        if "active_w" not in self.caps:
            self.close()
            raise OSError("UPS exposes no ActivePower usage")

    def read(self):
        out = {}
        for name, (page, usage, rid, link) in self.caps.items():
            rep = (ctypes.c_ubyte * self.n)()
            rep[0] = rid
            if not hid.HidD_GetFeature(self.h, rep, self.n):
                continue
            val = W.ULONG()
            if hid.HidP_GetUsageValue(2, page, link, usage, ctypes.byref(val), self.pp, rep, self.n) == 0x110000:
                out[name] = val.value
        if "active_w" not in out:
            raise OSError("ActivePower unread")
        return out

    def close(self):
        try:
            hid.HidD_FreePreparsedData(self.pp)
        finally:
            k32.CloseHandle(self.h)


def open_csv():
    os.makedirs(os.path.dirname(CSV), exist_ok=True)
    if os.path.exists(CSV):
        with open(CSV, encoding="ascii", errors="replace") as f:
            first = f.readline().strip()
        if first != HDR:                     # schema change: keep the old file, start a clean one
            os.replace(CSV, CSV.replace(".csv", "_old_%s.csv" % datetime.now().strftime("%Y%m%d_%H%M%S")))
    new = not os.path.exists(CSV)
    f = open(CSV, "a", encoding="ascii", newline="")
    if new:
        f.write(HDR + "\n")
        f.flush()
    return f


def main():
    f, ups = open_csv(), None
    while True:
        t0 = time.time()
        try:
            if ups is None:
                ups = Ups()
            r = ups.read()
            f.write("%s,%.1f,%s\n" % (datetime.now().astimezone().isoformat(timespec="seconds"), t0, ",".join(
                str(r.get(k, "")) for k in HDR.split(",")[2:])))
            f.flush()
        except Exception as e:                # no row on a failed read; reopen next tick
            print("%s read failed: %r" % (datetime.now().isoformat(timespec="seconds"), e), flush=True)
            if ups is not None:
                try:
                    ups.close()
                except Exception:
                    pass
                ups = None
        time.sleep(max(0.5, PERIOD_S - (time.time() - t0)))


if __name__ == "__main__":
    main()
