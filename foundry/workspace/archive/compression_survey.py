"""READ-ONLY survey: what NTFS compression actually saves, per top-level folder of each drive.

    python compression_survey.py <ORDER_OUT> <drive> [<drive> ...] [order name, appended by the factory]

Per top-level entry: logical bytes, on-disk bytes (GetCompressedFileSizeW), saved %, how many files carry the
Compressed attribute, and whether the folder itself is flagged (new files inherit it). Reparse points are not
followed. Deliverables: <ORDER_OUT>\\survey.json and survey.md. One progress marker per top-level entry.
"""
import ctypes
import json
import os
import stat
import sys
import time
from ctypes import wintypes

_gcfs = ctypes.windll.kernel32.GetCompressedFileSizeW
_gcfs.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
_gcfs.restype = wintypes.DWORD
FILE_ATTRIBUTE_COMPRESSED = 0x800


def on_disk(path):
    hi = wintypes.DWORD(0)
    lo = _gcfs("\\\\?\\" + path, ctypes.byref(hi))
    if lo == 0xFFFFFFFF and ctypes.GetLastError() != 0:
        return None
    return (hi.value << 32) + lo


def is_link(e):
    try:
        return bool(e.stat(follow_symlinks=False).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return True


def survey(path):
    logical = disk = files = comp = errs = 0
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            it = list(os.scandir(d))
        except OSError:
            errs += 1
            continue
        for e in it:
            if is_link(e):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                errs += 1
                continue
            files += 1
            logical += st.st_size
            if st.st_file_attributes & FILE_ATTRIBUTE_COMPRESSED:
                comp += 1
                od = on_disk(e.path)
                disk += od if od is not None else st.st_size
                errs += od is None
            else:
                disk += st.st_size
    return logical, disk, files, comp, errs


def flagged(path):
    try:
        return bool(os.stat(path).st_file_attributes & FILE_ATTRIBUTE_COMPRESSED)
    except OSError:
        return None


def main():
    out = sys.argv[1]
    root_of = lambda a: a + "\\" if len(a) == 2 and a[1] == ":" else a
    drives = [a for a in sys.argv[2:] if os.path.isdir(root_of(a))]      # the factory's trailing order name is not a dir
    assert drives, "no drives/folders given"
    prog = os.path.join(out, "progress")
    os.makedirs(prog, exist_ok=True)
    entries = [(d, e) for d in drives for e in os.scandir(root_of(d)) if e.is_dir(follow_symlinks=False) and not is_link(e)]
    print("surveying %d top-level folders on %s" % (len(entries), ", ".join(drives)), flush=True)
    rows, t0 = [], time.time()
    for n, (drv, e) in enumerate(entries, 1):
        lg, dk, nf, nc, er = survey(e.path)
        rows.append({"drive": drv, "path": e.path, "folder_flagged": flagged(e.path), "logical": lg, "on_disk": dk,
                     "saved": lg - dk, "saved_pct": round(100.0 * (lg - dk) / lg, 1) if lg else 0.0,
                     "files": nf, "compressed_files": nc, "errors": er})
        open(os.path.join(prog, "%04d.done" % n), "w").close()
        print("%d/%d %-50s %8.1f GB  saved %6.1f GB (%4.1f%%)" % (n, len(entries), e.path[:50], lg / 1e9,
              (lg - dk) / 1e9, rows[-1]["saved_pct"]), flush=True)
    roots = {d: flagged(root_of(d)) for d in drives}
    json.dump({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "root_flagged": roots, "rows": rows,
               "seconds": round(time.time() - t0)}, open(os.path.join(out, "survey.json"), "w", encoding="utf-8"),
              indent=1)
    md = ["# NTFS compression survey %s" % time.strftime("%Y-%m-%d %H:%M"), "",
          "Root compress-new-files flag: " + ", ".join("%s %s" % (d, "ON" if f else "off") for d, f in roots.items()),
          "", "| folder | flagged | logical GB | on disk GB | saved GB | saved % | compressed files |", "|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["drive"], -r["saved"])):
        md.append("| %s | %s | %.1f | %.1f | %.1f | %.1f | %d/%d |" % (r["path"], "yes" if r["folder_flagged"] else "no",
                  r["logical"] / 1e9, r["on_disk"] / 1e9, r["saved"] / 1e9, r["saved_pct"], r["compressed_files"],
                  r["files"]))
    open(os.path.join(out, "survey.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    print("DONE: %d folders in %.0f s" % (len(rows), time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
