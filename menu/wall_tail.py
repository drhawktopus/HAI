"""Remote half of menu_wall.sync: run on a box as `python - <path>|<offset> ...` (the script arrives on stdin).

For each argument prints one header line `<path>\t<offset>\t<n bytes>\t<size>\n` followed by exactly <n bytes> raw bytes
read from <offset> to the end as the file stood when it was opened. `<glob>|LIST` lists that glob instead
(header `<glob>\tLIST\t<n bytes>\t0` then `<name>\t<size>` lines). Read-only: it opens files for reading and seeks;
it never reads anything before <offset>, so a growing log costs only its new bytes."""
import glob
import os
import sys

out = sys.stdout.buffer
for a in sys.argv[1:]:
    path, _, off = a.rpartition("|")
    if off == "LIST":
        body = "".join("%s\t%d\n" % (os.path.basename(p), os.path.getsize(p)) for p in sorted(glob.glob(path))).encode()
        out.write(("%s\tLIST\t%d\t0\n" % (path, len(body))).encode() + body)
        continue
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            start = int(off or 0)
            if start > size:                  # shorter than the copy: replaced or truncated - send it whole again
                start = 0
            f.seek(start)
            data = f.read(size - start)
    except OSError:
        size, start, data = -1, 0, b""
    out.write(("%s\t%d\t%d\t%d\n" % (path, start, len(data), size)).encode() + data)
out.flush()
