"""Copy a folder's files to another drive in ONE read pass of the source (hash the bytes as they stream), then hash
the destination copy and compare. Built for big files coming off a slow disk (HDD -> NVMe): the source is read once.
The destination folder is created uncompressed. Nothing is removed.

    python copy_verify.py <src_dir> <dst_dir> <ORDER_OUT> [order name, appended by the factory]

Progress: one marker per GB copied, in <ORDER_OUT>\\progress. Deliverable: <ORDER_OUT>\\copy_manifest.json.
"""
import ctypes
import hashlib
import json
import os
import sys
import queue
import threading
import time
from ctypes import wintypes

CHUNK = 64 << 20
READ_AHEAD = 4                                   # chunks buffered ahead of the hash/write (4 x 64 MB)
COMPRESSED = 0x800


def uncompress_dir(p):
    """Clear the directory's compress-new-files flag (FSCTL_SET_COMPRESSION NONE) if it is set."""
    if not os.stat(p).st_file_attributes & COMPRESSED:
        return False
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    h = k32.CreateFileW("\\\\?\\" + p, 0xC0000000, 7, None, 3, 0x02000000, None)
    fmt, ret = ctypes.c_ushort(0), wintypes.DWORD(0)
    ok = k32.DeviceIoControl(h, 0x9C040, ctypes.byref(fmt), 2, None, 0, ctypes.byref(ret), None)
    k32.CloseHandle(h)
    assert ok, "could not clear compression on %s" % p
    return True


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def main():
    src, dst, out = os.path.normpath(sys.argv[1]), os.path.normpath(sys.argv[2]), sys.argv[3]
    assert os.path.isdir(src), "source folder missing: %s" % src
    prog = os.path.join(out, "progress")
    os.makedirs(prog, exist_ok=True)
    os.makedirs(dst, exist_ok=True)
    for p in (os.path.dirname(dst), dst):
        if uncompress_dir(p):
            print("cleared compress-new-files on %s" % p, flush=True)
    files = sorted(os.path.join(dp, f) for dp, dn, fn in os.walk(src) for f in fn)
    total = sum(os.path.getsize(f) for f in files)
    print("copy %d files, %.1f GB: %s -> %s" % (len(files), total / 1e9, src, dst), flush=True)
    manifest, done, gb_mark, t0 = [], 0, 0, time.time()
    for s in files:
        d = os.path.join(dst, os.path.relpath(s, src))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        h = hashlib.sha256()
        tmp = d + ".part"
        q = queue.Queue(maxsize=READ_AHEAD)
        err = []

        def reader(path=s):                                   # keeps the source disk streaming while we hash/write
            try:
                with open(path, "rb") as fi:
                    for b in iter(lambda: fi.read(CHUNK), b""):
                        q.put(b)
            except OSError as e:
                err.append(e)
            finally:
                q.put(None)
        th = threading.Thread(target=reader, daemon=True)
        th.start()
        with open(tmp, "wb") as fo:
            for b in iter(q.get, None):
                h.update(b)
                fo.write(b)
                done += len(b)
                while done >= (gb_mark + 1) * 1e9:
                    gb_mark += 1
                    open(os.path.join(prog, "%04d.done" % gb_mark), "w").close()
                    if gb_mark % 25 == 0:
                        print("copied %.0f/%.0f GB, %.0f MB/s" % (done / 1e9, total / 1e9,
                                                                done / 1e6 / max(1, time.time() - t0)), flush=True)
        th.join()
        if err:
            raise err[0]                                       # a failed read must never become a short copy
        if os.path.getsize(tmp) != os.path.getsize(s):
            raise IOError("short copy: %s" % s)
        os.replace(tmp, d)
        manifest.append({"src": s, "dst": d, "bytes": os.path.getsize(s), "sha256_src": h.hexdigest()})
    copy_s = time.time() - t0
    print("COPY: %.1f GB in %.0f s = %.0f MB/s; verifying the destination" % (total / 1e9, copy_s,
                                                                            total / 1e6 / max(1, copy_s)), flush=True)
    bad = []
    for m in manifest:
        m["sha256_dst"] = sha_file(m["dst"])
        if m["sha256_dst"] != m["sha256_src"]:
            bad.append(m["dst"])
    json.dump({"src": src, "dst": dst, "files": manifest, "mismatched": bad, "copy_s": round(copy_s),
               "at": time.strftime("%Y-%m-%d %H:%M:%S")}, open(os.path.join(out, "copy_manifest.json"), "w"), indent=1)
    if bad:
        print("MISMATCH: %s" % bad, flush=True)
        sys.exit(1)
    print("DONE: %d files %.1f GB copied and SHA-256 verified at %s" % (len(manifest), total / 1e9, dst), flush=True)


if __name__ == "__main__":
    main()
