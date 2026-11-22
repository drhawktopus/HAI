#!/usr/bin/env python3
"""Keeps rigby model files in the page cache to avoid cold reads on the SATA SSD.

Reads only evicted chunks while idle: no active order and sufficient available memory.
Never pins RAM or starves active inference."""
import ctypes
import json
import mmap
import os
import sys
import time

# Settings, from the environment (the service unit loads /etc/default/tolkien-host): Tolkien's state folder on this host, the
# models folder the tablet-foundry container mounts as /models, and the model files to keep warm (comma-separated).
ENVFILE = "/etc/default/tolkien-host"
STATE = os.environ.get("MODEL_WARMER_STATE", "")
MODELS = os.environ.get("MODEL_WARMER_MODELS", "")
WARM = tuple(f.strip() for f in os.environ.get("MODEL_WARMER_FILES", "").split(",") if f.strip())
RECORD = "model_warmer.json"
LANE_CLAIMS = ("CURRENT_ORDER.json", "CURRENT_ORDER.rigby.json")       # rigby_card_keeper.LANE_CLAIMS
PERIOD_S = 120          # the card keeper's beat (rigby_card_keeper.PERIOD_S): the timer runs at it, the record declares it
CHUNK = 64 << 20        # the unit read back: a chunk is read only when some page in it is not cached
UNITS = {
    "rigby-model-warmer.service": (
        "[Unit]\n"
        "Description=rigby model warmer - read the models rigby leans on back into the page cache while idle\n"
        "Documentation=file:///usr/local/bin/rigby_model_warmer.py\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "Nice=19\n"
        "IOSchedulingClass=idle\n"
        "EnvironmentFile=-/etc/default/tolkien-host\n"
        "ExecStart=/usr/bin/python3 /usr/local/bin/rigby_model_warmer.py\n"),
    "rigby-model-warmer.timer": (
        "[Unit]\n"
        "Description=rigby model warmer tick (cached share per model; reads back only when both lanes are idle)\n\n"
        "[Timer]\n"
        "OnBootSec=%ds\n"
        "OnUnitActiveSec=%ds\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n" % (PERIOD_S, PERIOD_S)),
}

_PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def cached_chunks(path, chunk=CHUNK):
    """[(offset, length, cached?)] for the whole file, from mincore over a PRIVATE, never-touched map (it reads the shared
    page cache's state and costs no memory). A chunk counts as cached only when every page in it is."""
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    size = os.path.getsize(path)
    fd = os.open(path, os.O_RDONLY)
    try:
        m = mmap.mmap(fd, size, flags=mmap.MAP_PRIVATE, prot=mmap.PROT_READ | mmap.PROT_WRITE)
        try:
            n = (size + _PAGE - 1) // _PAGE
            vec = (ctypes.c_ubyte * n)()
            buf = ctypes.c_char.from_buffer(m)
            try:
                if libc.mincore(ctypes.c_void_p(ctypes.addressof(buf)), ctypes.c_size_t(size), vec) != 0:
                    raise OSError(ctypes.get_errno(), "mincore failed")
            finally:
                del buf
        finally:
            m.close()
    finally:
        os.close(fd)
    per = chunk // _PAGE
    out = []
    for off in range(0, size, chunk):
        first = off // _PAGE
        out.append((off, min(chunk, size - off), all(vec[i] & 1 for i in range(first, min(first + per, n)))))
    return out


def mem_available():
    """MemAvailable in bytes, or None when /proc/meminfo cannot be read (could not tell = do not read)."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def decide(missing_bytes, lanes_busy, available):
    """(action, why). action: "none" (all cached) | "warm" (read back the missing chunks) | "wait" (not now, said why)."""
    if missing_bytes <= 0:
        return "none", "fully cached"
    gb = missing_bytes / 1e9
    if lanes_busy:
        return "wait", "%.1f GB not cached; a lane is busy (%s) - an order reads its own model" % (gb, ", ".join(lanes_busy))
    if available is None:
        return "wait", "%.1f GB not cached; MemAvailable unreadable - could not tell is never room" % gb
    if missing_bytes > available:
        return "wait", "%.1f GB not cached but only %.1f GB available - reading it would push out what is in use" % (
            gb, available / 1e9)
    return "warm", "%.1f GB not cached, both lanes idle, %.1f GB available - reading it back" % (gb, available / 1e9)


def warm(path, chunks, read=None):
    """Read back every chunk that is not cached; -> (bytes read, seconds). `read(fd, off, length)` is injectable for
    tests; the default seeks and reads, dropping the bytes (the page cache keeps them)."""
    def _pread(fd, off, length):
        os.lseek(fd, off, os.SEEK_SET)
        done = 0
        while done < length:
            b = os.read(fd, min(8 << 20, length - done))
            if not b:
                break
            done += len(b)
        return done
    read = read or _pread
    t0, total = time.time(), 0
    fd = os.open(path, os.O_RDONLY)
    try:
        for off, length, cached in chunks:
            if not cached:
                total += read(fd, off, length)
    finally:
        os.close(fd)
    return total, time.time() - t0


def record(files, action, why, now):
    return {"checked": now, "period_s": PERIOD_S, "files": files, "last": "%s: %s" % (action, why)}


def write_units(dest):
    for name, text in UNITS.items():
        path = os.path.join(dest, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print("wrote " + path)


def main(argv):
    if "--write-units" in argv:
        rest = argv[argv.index("--write-units") + 1:]
        write_units(rest[0] if rest else "/etc/systemd/system")
        return 0
    dry = "--dry" in argv
    if not (STATE and MODELS and WARM):
        print("model warmer: MODEL_WARMER_STATE / MODEL_WARMER_MODELS / MODEL_WARMER_FILES unset (%s) - no action" % ENVFILE)
        return 2
    now = time.time()
    busy = [c for c in LANE_CLAIMS if os.path.exists(os.path.join(STATE, c))]
    files, lines = {}, []
    for name in WARM:
        path = os.path.join(MODELS, name)
        if not os.path.isfile(path):
            files[name] = {"error": "not in %s" % MODELS}
            lines.append("model warmer: %s - not in %s" % (name, MODELS))
            continue
        chunks = cached_chunks(path)
        size = sum(c[1] for c in chunks)
        missing = sum(c[1] for c in chunks if not c[2])
        action, why = decide(missing, busy, mem_available())
        entry = {"size_gb": round(size / 1e9, 2), "cached_pct": round(100.0 * (size - missing) / size, 1) if size else 0.0,
                 "action": action}
        if action == "warm" and not dry:
            n, secs = warm(path, chunks)
            after = sum(c[1] for c in cached_chunks(path) if not c[2])
            entry.update({"read_gb": round(n / 1e9, 2), "read_s": round(secs, 1),
                          "cached_pct_after": round(100.0 * (size - after) / size, 1) if size else 0.0})
            why += " - read %.1f GB in %.0f s, %.1f%% cached after" % (n / 1e9, secs, entry["cached_pct_after"])
        files[name] = entry
        lines.append("model warmer: %s %s - %s%s" % (name, action, why, " (dry)" if dry else ""))
    for line in lines:
        print(line)
    if dry:
        return 0
    actions = sorted({f.get("action", "error") for f in files.values()})
    tmp = os.path.join(STATE, RECORD + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record(files, "/".join(actions), "; ".join(lines), now), f)
    os.replace(tmp, os.path.join(STATE, RECORD))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
