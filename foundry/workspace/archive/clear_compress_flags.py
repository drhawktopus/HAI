"""Clear the NTFS compress-new-files flag on DIRECTORIES only (FSCTL_SET_COMPRESSION = NONE on each directory
handle). Existing files are NOT rewritten - they keep whatever state they have; new files written afterwards are
stored uncompressed. Windows-managed trees are skipped. Reparse points are not followed.

    python clear_compress_flags.py <root> [<root> ...] [--dry-run]
"""
import ctypes
import os
import stat
import sys
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                            wintypes.DWORD, wintypes.HANDLE]
k32.CreateFileW.restype = wintypes.HANDLE
k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
FSCTL_SET_COMPRESSION = 0x9C040
GENERIC_RW = 0x80000000 | 0x40000000
SHARE_ALL = 0x7
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
INVALID = wintypes.HANDLE(-1).value
COMPRESSED = 0x800
SKIP = {"xbox games", "xboxgames", "xboxlibrary", "xbox", "windowsapps", "wpsystem", "system volume information",
        "$recycle.bin", "recovery", "config.msi", "wudownloadcache", "program files", "modifiablewindowsapps"}


def flagged(p):
    try:
        return bool(os.stat(p, follow_symlinks=False).st_file_attributes & COMPRESSED)
    except OSError:
        return False


def clear(p):
    h = k32.CreateFileW("\\\\?\\" + p, GENERIC_RW, SHARE_ALL, None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
    if h == INVALID or h is None:
        return ctypes.get_last_error()
    try:
        fmt = ctypes.c_ushort(0)
        ret = wintypes.DWORD(0)
        ok = k32.DeviceIoControl(h, FSCTL_SET_COMPRESSION, ctypes.byref(fmt), 2, None, 0, ctypes.byref(ret), None)
        return 0 if ok else ctypes.get_last_error()
    finally:
        k32.CloseHandle(h)


def main():
    dry = "--dry-run" in sys.argv
    roots = [a for a in sys.argv[1:] if not a.startswith("--")]
    assert roots, "no roots given"
    seen = changed = skipped = 0
    errors = []
    for root in roots:
        root = root + "\\" if len(root) == 2 and root[1] == ":" else root
        stack = [root]
        while stack:
            d = stack.pop()
            seen += 1
            if flagged(d):
                rc = 0 if dry else clear(d)
                if rc:
                    errors.append((d, rc))
                else:
                    changed += 1
            try:
                it = list(os.scandir(d))
            except OSError:
                continue
            for e in it:
                try:
                    if not e.is_dir(follow_symlinks=False):
                        continue
                    if e.stat(follow_symlinks=False).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                        continue
                except OSError:
                    continue
                if e.name.lower() in SKIP:
                    skipped += 1
                    continue
                stack.append(e.path)
    print("%s: %d dirs seen, %d flags %s, %d Windows-managed trees skipped, %d errors"
          % (", ".join(roots), seen, changed, "WOULD clear" if dry else "cleared", skipped, len(errors)))
    for d, rc in errors[:15]:
        print("  error %d: %s" % (rc, d))


if __name__ == "__main__":
    main()
