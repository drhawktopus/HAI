"""[P20-EXIT] Make the Krea 2 ai-toolkit trainer (<FOUNDRY_AITK_KREA2>\\run.py) end its own process the moment the job
returns, instead of going through Python's interpreter shutdown.

Why: a Krea trainer can save its final checkpoint and full
resume state, then never exit - one thread left, spinning a CPU core, unkillable from outside (Stop-Process and an
elevated SYSTEM taskkill both "succeed" and the process stays), the card held, the lane held, and the only cure a
reboot. The 120 s exit watchdog in p20_apply_full_resume.py never fired: it is a daemon thread, and
daemon threads are stopped when the interpreter begins shutdown, so a hang INSIDE shutdown (the GPU runtime's teardown)
outlives it - one thread left is that picture. So the exit is moved ahead of shutdown: when main() returns (normal
finish, a STOP_NOW pause, which breaks the loop and takes the final save) or raises, flush the output and end the
process with TerminateProcess on itself. That skips atexit handlers, module teardown and every DLL's detach routine -
the runtime teardown never starts. Every file a run produces is already closed by then (the final save, the optimizer,
the p20_resume sidecar). The kernel releases the GPU context the way it does for any ended process - the lab's
documented cure for a held context.

Exit codes are kept: 0 when main() returns, SystemExit's own code, 1 (after printing the traceback, as Python does)
for an uncaught exception.

Usage on the box:     python p20_apply_hard_exit.py          (apply; idempotent)
                      python p20_apply_hard_exit.py --revert (restore the backup)
                      python p20_apply_hard_exit.py --target <path>  (a copy, for testing)"""
import os
import shutil
import sys


def _need(name):
    """A setting this setup script cannot run without (an environment variable): its value, or a refusal naming it."""
    v = os.environ.get(name, "")
    if not v:
        sys.exit("set %s (see this script's docstring)" % name)
    return v

MARK = b"[P20-EXIT]"
ANCHOR = b"if __name__ == '__main__':\r\n    main()\r\n"
TAIL = b"""def _p20_hard_exit(code):
    # [P20-EXIT] see setup/p20_apply_hard_exit.py: end the process before interpreter shutdown, whose
    # GPU-runtime teardown can hang past every kill.
    try:
        import logging
        logging.shutdown()
    except Exception:
        pass
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.flush()
        except Exception:
            pass
    if os.name == 'nt':
        try:
            import ctypes
            _k = ctypes.windll.kernel32
            _k.GetCurrentProcess.restype = ctypes.c_void_p
            _k.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint)
            _k.TerminateProcess(_k.GetCurrentProcess(), code & 0xFFFFFFFF)
        except Exception:
            pass
    os._exit(code)


if __name__ == '__main__':
    # [P20-EXIT] same exit codes as before; only the route out changes
    try:
        main()
        _p20_code = 0
    except SystemExit as _e:
        _p20_code = _e.code if isinstance(_e.code, int) else (0 if _e.code is None else 1)
        if _e.code is not None and not isinstance(_e.code, int):
            print(_e.code, file=sys.stderr)
    except BaseException:
        import traceback
        traceback.print_exc()
        _p20_code = 1
    print_acc("[P20-EXIT] job returned (exit code %d) - ending the process now, before interpreter shutdown"
              % _p20_code)
    _p20_hard_exit(_p20_code)
"""
TAIL = TAIL.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def main():
    args = sys.argv[1:]
    # --target names a copy (for testing); only the real tree needs the setting
    target = args[args.index("--target") + 1] if "--target" in args else os.path.join(_need("FOUNDRY_AITK_KREA2"), "run.py")
    backup = target + ".p20bak-exit"
    if "--revert" in args:
        if not os.path.exists(backup):
            print("no backup at %s; nothing to revert" % backup)
            return 1
        shutil.copy2(backup, target)
        print("reverted %s from %s" % (target, backup))
        return 0
    src = open(target, "rb").read()
    if MARK in src:
        print("already applied (%s present) - nothing to do" % MARK.decode())
        return 0
    n = src.count(ANCHOR)
    if n != 1:
        print("REFUSED: the anchor matched %d times (need exactly 1) - run.py is not the version this patch knows" % n)
        return 2
    for need in (b"import os", b"import sys", b"print_acc"):
        if need not in src:
            print("REFUSED: run.py lacks %r, which the patch uses" % need.decode())
            return 2
    out = src.replace(ANCHOR, TAIL, 1)
    shutil.copy2(target, backup)
    with open(target, "wb") as f:
        f.write(out)
    compile(out.decode("utf-8"), target, "exec")
    print("applied: %s (%d -> %d bytes), backup %s" % (target, len(src), len(out), backup))
    return 0


if __name__ == "__main__":
    sys.exit(main())
