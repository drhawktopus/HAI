r"""#39 Phase 1 launch probe: does the recompiled Jade Empire start and draw on this card, and can the card bridge
OpenGL to Vulkan (the FSR 4 route)?

Usage: launch_probe.py <game_dir> <out_dir> [idle_s] [JE_<key>=<value> ...] [exe=<name in game_dir>]
                       [desktop_free=<who said so>] [order]

Runs inside the factory runner, which on the AMD box is the console user's interactive desktop session. Steps:
  1 gl     - a hidden window + OpenGL context through the real driver: vendor, renderer, version, every GL and WGL
             extension, and the interop ones FSR 4 over Vulkan would need. Positive control: a renderer of
             "GDI Generic" means the probe ran outside the desktop session, so the result is INCONCLUSIVE, never
             "missing". vulkaninfo --summary too, when the driver ships it.
  2 launch - refuses while someone is using the desktop (input in the last 5 min); else starts jade.exe with its
             control channel (JE_CTL) in out_dir\ctl and its whole log in out_dir\jade.log.
  3 drive  - the channel's own commands: frame waits (timed, so the frame rate is measured), screenshots, then
             quit. Each shot is taken two ways with a lit-pixel share: the port's back-buffer read, and the whole
             desktop as composited with the game window cropped from that same grab (the first launch's
             back-buffer reads were all black and nothing else could say whether the game or the read was).
             The display is held on for the run, and each desktop grab is checked for freshness against the
             previous one: a sleeping display hands back its last composed frame (launch 2).
             A crash is the process ending; a hang is no frame presented for idle_s while a command waits
             (demonstrated idleness, not a run clock).
  4 after  - the process has exited (or is ended by its own PID if it ignored quit), the stock exe is unchanged,
             and JadeEmpire.ini is put back to its state before the launch if the game changed it.
Deliverables in out_dir: gl_info.json, vulkaninfo.txt, jade.log, ctl\, shots\, launch.json, progress\*.done.
"""
import ctypes
import ctypes.wintypes as w
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import zlib

STOCK_EXE_SHA = "0d25f18c"  # prefix of the stock JadeEmpire.exe sha256 the build order recorded
INTEROP = ["GL_EXT_memory_object", "GL_EXT_memory_object_win32", "GL_EXT_semaphore", "GL_EXT_semaphore_win32",
           "GL_NV_draw_vulkan_image", "WGL_NV_DX_interop2"]
CONTROL_EXTS = ["GL_ARB_framebuffer_object", "GL_ARB_vertex_buffer_object"]


def done(out, step, data=None):
    os.makedirs(os.path.join(out, "progress"), exist_ok=True)
    with open(os.path.join(out, "progress", step + ".done"), "w") as f:
        json.dump(data or {}, f)
    print("STEP", step, "done", flush=True)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---------- step 1: the driver's OpenGL ----------

class PFD(ctypes.Structure):
    _fields_ = [("nSize", w.WORD), ("nVersion", w.WORD), ("dwFlags", w.DWORD), ("iPixelType", w.BYTE),
                ("cColorBits", w.BYTE), ("cRedBits", w.BYTE), ("cRedShift", w.BYTE), ("cGreenBits", w.BYTE),
                ("cGreenShift", w.BYTE), ("cBlueBits", w.BYTE), ("cBlueShift", w.BYTE), ("cAlphaBits", w.BYTE),
                ("cAlphaShift", w.BYTE), ("cAccumBits", w.BYTE), ("cAccumRedBits", w.BYTE),
                ("cAccumGreenBits", w.BYTE), ("cAccumBlueBits", w.BYTE), ("cAccumAlphaBits", w.BYTE),
                ("cDepthBits", w.BYTE), ("cStencilBits", w.BYTE), ("cAuxBuffers", w.BYTE), ("iLayerType", w.BYTE),
                ("bReserved", w.BYTE), ("dwLayerMask", w.DWORD), ("dwVisibleMask", w.DWORD),
                ("dwDamageMask", w.DWORD)]


def gl_info():
    user32, gdi32, gl = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.opengl32
    H = ctypes.c_void_p
    user32.CreateWindowExW.restype = H
    user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, H, H, H, H]
    user32.GetDC.restype, user32.GetDC.argtypes = H, [H]
    user32.ReleaseDC.argtypes = [H, H]
    user32.DestroyWindow.argtypes = [H]
    gdi32.ChoosePixelFormat.argtypes = [H, ctypes.POINTER(PFD)]
    gdi32.SetPixelFormat.argtypes = [H, ctypes.c_int, ctypes.POINTER(PFD)]
    gl.wglCreateContext.restype, gl.wglCreateContext.argtypes = H, [H]
    gl.wglMakeCurrent.argtypes = [H, H]
    gl.wglDeleteContext.argtypes = [H]
    gl.wglGetProcAddress.restype, gl.wglGetProcAddress.argtypes = H, [ctypes.c_char_p]
    gl.glGetString.restype, gl.glGetString.argtypes = ctypes.c_char_p, [ctypes.c_uint]

    hwnd = user32.CreateWindowExW(0, "STATIC", "jade39-glprobe", 0x00CF0000, 0, 0, 64, 64, None, None, None, None)
    if not hwnd:
        return {"error": "CreateWindowExW failed (%d)" % ctypes.GetLastError()}
    hdc = user32.GetDC(hwnd)
    pfd = PFD()
    pfd.nSize, pfd.nVersion = ctypes.sizeof(PFD), 1
    pfd.dwFlags = 0x4 | 0x20 | 0x1  # DRAW_TO_WINDOW | SUPPORT_OPENGL | DOUBLEBUFFER
    pfd.cColorBits, pfd.cDepthBits = 32, 24
    fmt = gdi32.ChoosePixelFormat(hdc, ctypes.byref(pfd))
    if not fmt or not gdi32.SetPixelFormat(hdc, fmt, ctypes.byref(pfd)):
        return {"error": "no pixel format (%d)" % ctypes.GetLastError()}
    ctx = gl.wglCreateContext(hdc)
    if not ctx or not gl.wglMakeCurrent(hdc, ctx):
        return {"error": "wglCreateContext/MakeCurrent failed (%d)" % ctypes.GetLastError()}
    s = lambda e: (gl.glGetString(e) or b"").decode("ascii", "replace")
    info = {"vendor": s(0x1F00), "renderer": s(0x1F01), "version": s(0x1F02)}
    gl_exts = sorted(set(s(0x1F03).split()))
    wgl_exts = []
    p = gl.wglGetProcAddress(b"wglGetExtensionsStringARB")
    if p:
        fn = ctypes.WINFUNCTYPE(ctypes.c_char_p, H)(p)
        wgl_exts = sorted(set((fn(hdc) or b"").decode("ascii", "replace").split()))
    gl.wglMakeCurrent(None, None)
    gl.wglDeleteContext(ctx)
    user32.ReleaseDC(hwnd, hdc)
    user32.DestroyWindow(hwnd)
    every = set(gl_exts) | set(wgl_exts)
    info.update({
        "gl_extension_count": len(gl_exts), "wgl_extension_count": len(wgl_exts),
        "interop": {e: e in every for e in INTEROP},
        "positive_control": {e: e in every for e in CONTROL_EXTS},
        "gl_extensions": gl_exts, "wgl_extensions": wgl_exts,
    })
    software = "gdi generic" in info["renderer"].lower() or "microsoft" in info["vendor"].lower()
    controls_ok = all(info["positive_control"].values())
    info["verdict"] = ("INCONCLUSIVE - software renderer, the probe is not on the driver" if software
                       else "INCONCLUSIVE - the positive-control extensions are missing, the list is unreadable"
                       if not controls_ok else "measured")
    return info


def vulkan_summary(out):
    exe = shutil.which("vulkaninfo") or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32",
                                                     "vulkaninfo.exe")
    if not os.path.exists(exe):
        return "vulkaninfo not present on this box"
    r = subprocess.run([exe, "--summary"], capture_output=True, text=True, errors="replace")
    with open(os.path.join(out, "vulkaninfo.txt"), "w", encoding="utf-8") as f:
        f.write(r.stdout + "\n" + r.stderr)
    return "rc=%d, %d lines" % (r.returncode, len(r.stdout.splitlines()))


# ---------- step 2-3: launch and drive ----------

def desktop_idle_s():
    class LII(ctypes.Structure):
        _fields_ = [("cbSize", w.UINT), ("dwTime", w.DWORD)]
    lii = LII()
    lii.cbSize = ctypes.sizeof(LII)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return None
    return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000.0


def write_png(dst, wd, ht, rgb):
    raw = b"".join(b"\x00" + rgb[y * wd * 3:(y + 1) * wd * 3] for y in range(ht))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    with open(dst, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", wd, ht, 8, 2, 0, 0, 0)) +
                chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def lit_fraction(rgb):
    """Share of pixels with any channel above 16, sampled every 7th pixel: 0.0 is a black frame."""
    n = len(rgb) // 3
    if not n:
        return 0.0
    idx = range(0, n, 7)
    lit = sum(1 for i in idx if max(rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2]) > 16)
    return round(lit / len(idx), 4)


def _gdi_grab(src_dc, x, y, wd, ht, hwnd=None):
    """BitBlt (or PrintWindow when hwnd is given) into a top-down 32-bit DIB; returns RGB bytes."""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    H = ctypes.c_void_p
    gdi32.CreateCompatibleDC.restype, gdi32.CreateCompatibleDC.argtypes = H, [H]
    gdi32.CreateCompatibleBitmap.restype, gdi32.CreateCompatibleBitmap.argtypes = H, [H, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype, gdi32.SelectObject.argtypes = H, [H, H]
    gdi32.BitBlt.argtypes = [H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, H, ctypes.c_int,
                             ctypes.c_int, w.DWORD]
    gdi32.GetDIBits.argtypes = [H, H, w.UINT, w.UINT, ctypes.c_void_p, ctypes.c_void_p, w.UINT]
    gdi32.DeleteObject.argtypes, gdi32.DeleteDC.argtypes = [H], [H]
    user32.PrintWindow.argtypes = [H, H, w.UINT]
    mem = gdi32.CreateCompatibleDC(src_dc)
    bmp = gdi32.CreateCompatibleBitmap(src_dc, wd, ht)
    old = gdi32.SelectObject(mem, bmp)
    if hwnd:
        ok = user32.PrintWindow(hwnd, mem, 3)  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
    else:
        ok = gdi32.BitBlt(mem, 0, 0, wd, ht, src_dc, x, y, 0x00CC0020 | 0x40000000)  # SRCCOPY | CAPTUREBLT
    hdr = struct.pack("<IiiHHIIiiII", 40, wd, -ht, 1, 32, 0, 0, 0, 0, 0, 0)
    bi = ctypes.create_string_buffer(hdr, 44)
    buf = ctypes.create_string_buffer(wd * ht * 4)
    got = gdi32.GetDIBits(mem, bmp, 0, ht, buf, bi, 0)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    if not ok or got != ht:
        raise OSError("capture failed (ok=%s rows=%s err=%d)" % (ok, got, ctypes.GetLastError()))
    bgra = buf.raw
    rgb = bytearray(wd * ht * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    return bytes(rgb)


def window_of(pid):
    user32 = ctypes.windll.user32
    found = []
    proto = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    def cb(hwnd, _):
        owner = w.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True
    user32.EnumWindows(proto(cb), 0)
    return found[0] if found else None


def client_rect_on_screen(pid):
    """The game window's client area in desktop pixels, or None."""
    user32 = ctypes.windll.user32
    hwnd = window_of(pid)
    if not hwnd:
        return None
    r, pt = w.RECT(), w.POINT(0, 0)
    user32.GetClientRect(hwnd, ctypes.byref(r))
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, r.right - r.left, r.bottom - r.top


def capture_screen(dst, crop=None, crop_dst=None):
    """The whole desktop as composited (the positive control), plus a crop of the game window from the SAME grab.
    PrintWindow returns junk for an OpenGL window (launch 2), so the window is cut from the desktop instead."""
    user32 = ctypes.windll.user32
    x, y, wd, ht = (user32.GetSystemMetrics(i) for i in (76, 77, 78, 79))
    user32.GetDC.restype, user32.GetDC.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
    dc = user32.GetDC(None)
    try:
        rgb = _gdi_grab(dc, x, y, wd, ht)
    finally:
        user32.ReleaseDC(None, ctypes.c_void_p(dc))
    write_png(dst, wd, ht, rgb)
    out = {"size": "%dx%d" % (wd, ht), "lit": lit_fraction(rgb), "sha": hashlib.sha256(rgb).hexdigest()[:16]}
    if crop and crop_dst:
        cx, cy, cw, ch = crop[0] - x, crop[1] - y, crop[2], crop[3]
        cx, cy = max(0, cx), max(0, cy)
        cw, ch = min(cw, wd - cx), min(ch, ht - cy)
        if cw > 0 and ch > 0:
            sub = b"".join(rgb[((cy + r) * wd + cx) * 3:((cy + r) * wd + cx + cw) * 3] for r in range(ch))
            write_png(crop_dst, cw, ch, sub)
            out["window"] = {"rect": list(crop), "lit": lit_fraction(sub), "sha": hashlib.sha256(sub).hexdigest()[:16]}
        else:
            out["window"] = {"error": "window rect %r is off the desktop" % (crop,)}
    return out


def keep_display_on(on):
    """ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED while the game runs: launch 2's desktop grab was a
    frozen frame (its taskbar clock read 6:30 AM at 16:03) because the display was asleep. Not input."""
    flags = 0x80000000 | (0x2 | 0x1 if on else 0)
    return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))


def ppm_to_png(src, dst):
    with open(src, "rb") as f:
        data = f.read()
    parts, pos = [], 0
    while len(parts) < 4:
        while data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            pos = data.index(b"\n", pos) + 1
            continue
        end = pos
        while not data[end:end + 1].isspace():
            end += 1
        parts.append(data[pos:end])
        pos = end
    pos += 1
    if parts[0] != b"P6":
        raise ValueError("not a P6 ppm: %r" % parts[0])
    wd, ht = int(parts[1]), int(parts[2])
    px = data[pos:pos + wd * ht * 3]
    write_png(dst, wd, ht, px)
    return {"size": "%dx%d" % (wd, ht), "lit": lit_fraction(px)}


GL_ERR = re.compile(r"^\d+: glGetError\(\) returned (GL_\w+)")
GL_CALL = re.compile(r"^\s+call: (gl\w+)\(")
GL_AT = re.compile(r"^\s+in:\s+(.*)$")


def gl_error_summary(lines):
    """vendor:OpenGL's GL_DEBUG prints each glGetError hit as three lines - 'N: glGetError() returned GL_X',
    '   call: glFoo(args)', '   in:   file(line:col)' (wrappers.odin debug_helper). Groups them by error, function
    and place, since the arguments differ per call; returns ([[count, key], ...] most frequent first, total hits)."""
    counts, total = {}, 0
    for i, l in enumerate(lines):
        m = GL_ERR.match(l)
        if not m:
            continue
        total += 1
        call = GL_CALL.match(lines[i + 1]) if i + 1 < len(lines) else None
        at = GL_AT.match(lines[i + 2]) if i + 2 < len(lines) else None
        key = "%s %s @ %s" % (m.group(1), call.group(1) if call else "?",
                              os.path.basename(at.group(1)) if at else "?")
        counts[key] = counts.get(key, 0) + 1
    return sorted(([n, k] for k, n in counts.items()), reverse=True)[:40], total


class Channel:
    def __init__(self, ctl, proc, idle_s):
        self.ctl, self.proc, self.idle_s, self.n, self.seen = ctl, proc, idle_s, 0, 0

    def _lines(self):
        try:
            with open(os.path.join(self.ctl, "out"), "r", errors="replace") as f:
                return [l.rstrip("\n") for l in f if l.endswith("\n")]
        except OSError:
            return []

    def send(self, cmd):
        """Returns (reply, seconds) or raises RuntimeError naming the fate: exited / hung."""
        self.n += 1
        cid = "c%d" % self.n
        t0 = time.time()
        with open(os.path.join(self.ctl, "in"), "a", newline="\n") as f:
            f.write("%s %s\n" % (cid, cmd))
        last_change, count = time.time(), len(self._lines())
        while True:
            lines = self._lines()
            for l in lines:
                if l.split(" ", 1)[0] == cid:
                    return l.split(" ", 1)[1] if " " in l else "", time.time() - t0
            if len(lines) != count:
                count, last_change = len(lines), time.time()
            if self.proc.poll() is not None:
                raise RuntimeError("exited rc=%s during %r" % (self.proc.returncode, cmd))
            if time.time() - last_change > self.idle_s:
                raise RuntimeError("hung: no reply to %r for %.0f s" % (cmd, self.idle_s))
            time.sleep(0.05)


def main():
    try:  # per-monitor DPI aware, so desktop grabs are real pixels and window rects line up with them
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    game, out = sys.argv[1], sys.argv[2]
    idle_s = float(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].replace(".", "").isdigit() else 120.0
    os.makedirs(out, exist_ok=True)
    report = {"game": game, "idle_s": idle_s, "started": time.strftime("%Y-%m-%dT%H:%M:%S")}

    # 1 gl
    info = gl_info()
    info["vulkaninfo"] = vulkan_summary(out)
    with open(os.path.join(out, "gl_info.json"), "w") as f:
        json.dump(info, f, indent=1)
    report["gl"] = {k: info.get(k) for k in ("vendor", "renderer", "version", "verdict", "interop", "error",
                                              "gl_extension_count", "wgl_extension_count", "vulkaninfo")}
    print("GL:", json.dumps(report["gl"]), flush=True)
    done(out, "gl", report["gl"])

    # 2 launch
    exe_name = next((a.split("=", 1)[1] for a in sys.argv[4:] if a.startswith("exe=")), "jade.exe")
    exe = os.path.join(game, exe_name)
    report["exe"] = exe_name
    stock = os.path.join(game, "JadeEmpire.exe")
    ini = os.path.join(game, "JadeEmpire.ini")
    stock_before = sha256(stock)
    ini_before = open(ini, "rb").read() if os.path.exists(ini) else None
    if not stock_before.startswith(STOCK_EXE_SHA):
        report["verdict"] = "ALERT - JadeEmpire.exe is not the stock exe (%s)" % stock_before[:16]
    idle = desktop_idle_s()
    report["desktop_idle_s"] = idle
    # desktop_free=<who> on the order: a person said the desktop is free, so recent input is recorded, not refused.
    report["desktop_free_by"] = next((a.split("=", 1)[1] for a in sys.argv[4:] if a.startswith("desktop_free=")), None)
    if "verdict" not in report and idle is not None and idle < 300 and not report["desktop_free_by"]:
        report["verdict"] = "DESKTOP_IN_USE - input %.0f s ago; not launching a game over someone's work" % idle
    if "verdict" in report:
        json.dump(report, open(os.path.join(out, "launch.json"), "w"), indent=1)
        print(report["verdict"], flush=True)
        return 1
    ctl = os.path.join(out, "ctl")
    shots = os.path.join(out, "shots")
    os.makedirs(ctl, exist_ok=True)
    os.makedirs(shots, exist_ok=True)
    report["display_kept_on"] = keep_display_on(True)
    env = dict(os.environ, JE_CTL=ctl, JE_MARK_DIR=os.path.join(out, "marks"), JE_STICK_LOG="1")
    # The port's own settings as JE_<key>=value args (they win over the ini, docs/recompiler.md s11): one arm per
    # order, so a comparison changes one thing.
    report["port_env"] = {k: v for k, v in (a.split("=", 1) for a in sys.argv[4:] if a.startswith("JE_") and "=" in a)}
    env.update(report["port_env"])
    log = open(os.path.join(out, "jade.log"), "wb")
    t_launch = time.time()
    proc = subprocess.Popen([exe, game], cwd=game, env=env, stdout=log, stderr=subprocess.STDOUT)
    report["pid"] = proc.pid
    while not os.path.exists(os.path.join(ctl, "in")):
        if proc.poll() is not None:
            break
        if time.time() - t_launch > idle_s:
            break
        time.sleep(0.1)
    report["channel_open_s"] = round(time.time() - t_launch, 2) if os.path.exists(os.path.join(ctl, "in")) else None
    done(out, "launch", {"pid": proc.pid, "channel_open_s": report["channel_open_s"]})

    # 3 drive
    ch = Channel(ctl, proc, idle_s)
    steps, prev_sha = [], None
    # No keys: the intro movies are off by default (INTRO=0), and Escape at the title opens the quit confirmation.
    plan = [("wait 60", None), ("shot", "01_first_frames"), ("wait 300", None), ("shot", "02_after_10s"),
            ("wait 300", None), ("shot", "03_after_20s"), ("wait 600", None), ("shot", "04_after_40s"),
            ("area", None), ("quit", None)]
    fate = "ok"
    try:
        if report["channel_open_s"] is None:
            raise RuntimeError("exited rc=%s before the channel opened" % proc.poll() if proc.poll() is not None
                               else "hung: the channel never opened in %.0f s" % idle_s)
        for cmd, shot in plan:
            if shot:
                ppm = os.path.join(shots, shot + ".ppm")
                cmd = "shot " + ppm.replace("\\", "/")
            reply, secs = ch.send(cmd)
            row = {"cmd": cmd, "reply": reply, "s": round(secs, 3)}
            if cmd.startswith("wait ") and secs > 0:
                row["fps"] = round(int(cmd.split()[1]) / secs, 1)
            if shot:
                # Three instruments per shot: the port's own back-buffer read, the window as Windows composites
                # it, and the whole desktop (positive control: wallpaper and taskbar are never all black).
                for key, fn in (("backbuffer", lambda: ppm_to_png(ppm, ppm[:-4] + ".png")),
                                ("desktop", lambda: capture_screen(os.path.join(shots, shot + "_desktop.png"),
                                                                   client_rect_on_screen(proc.pid),
                                                                   os.path.join(shots, shot + "_window.png")))):
                    try:
                        row[key] = fn()
                    except Exception as exc:
                        row[key] = {"error": repr(exc)}
                if os.path.exists(ppm):
                    os.remove(ppm)
                # Freshness: a desktop identical to the previous shot's is a frozen frame, not evidence.
                sha = (row.get("desktop") or {}).get("sha")
                row["desktop_fresh"] = None if prev_sha is None or sha is None else sha != prev_sha
                prev_sha = sha or prev_sha
            steps.append(row)
            print("CTL", json.dumps(row), flush=True)
            if cmd == "quit":
                break
    except RuntimeError as exc:
        fate = str(exc)
    report["steps"], report["fate"] = steps, fate
    fps = [r["fps"] for r in steps if "fps" in r]
    report["fps_measured"] = fps
    done(out, "drive", {"fate": fate, "fps": fps})

    # 4 after
    ended_by = "exit"
    t_quit = time.time()
    while proc.poll() is None and time.time() - t_quit < idle_s:
        time.sleep(0.2)
    if proc.poll() is None:
        ended_by = "terminated (pid %d ignored quit for %.0f s)" % (proc.pid, idle_s)
        proc.kill()
        proc.wait()
    log.close()
    keep_display_on(False)
    report["exit"] = {"rc": proc.returncode, "ended_by": ended_by, "ran_s": round(time.time() - t_launch, 1)}
    report["stock_exe_unchanged"] = sha256(stock) == stock_before
    ini_after = open(ini, "rb").read() if os.path.exists(ini) else None
    report["ini_changed"] = ini_after != ini_before
    if report["ini_changed"] and ini_before is not None:
        with open(os.path.join(out, "JadeEmpire.ini.before"), "wb") as f:
            f.write(ini_before)
        if ini_after is not None:
            with open(os.path.join(out, "JadeEmpire.ini.after"), "wb") as f:
                f.write(ini_after)
        with open(ini, "wb") as f:  # a probe leaves the player's settings as it found them
            f.write(ini_before)
        report["ini_restored"] = open(ini, "rb").read() == ini_before
    with open(os.path.join(out, "jade.log"), "r", errors="replace") as f:
        jl = f.read().splitlines()
    report["log_lines"] = len(jl)
    report["gl_errors"], report["gl_error_hits"] = gl_error_summary(jl)
    report["log_gl"] = [l for l in jl if l.startswith("d3d9: GL")][:2]
    report["log_tail"] = jl[-25:]
    report["verdict"] = "RAN" if fate == "ok" else "FAILED - " + fate
    with open(os.path.join(out, "launch.json"), "w") as f:
        json.dump(report, f, indent=1)
    done(out, "after", report["exit"])
    print("VERDICT", report["verdict"], json.dumps(report["exit"]), flush=True)
    return 0 if fate == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
