"""Move folders/files to an archive drive in two sequential phases, then remove the sources only after EVERY file
verified. Any error or mismatch = nothing is removed (rc 1); a re-run skips what robocopy finds identical.

  1. COPY   robocopy streams every item to its destination (one sequential write), while a thread hashes the
            SOURCES (a different drive, no contention).
  2. VERIFY one sequential read of the destination, each file compared to its source hash; a mismatch is recopied
            once and re-hashed.
  3. REMOVE the sources, only when all files verified.

    python archive_move.py <plan.json> <ORDER_OUT> [order name, appended by the factory]

plan.json, either form:
  {"moves": [{"src": "<folder>", "dst": "<archive folder>"}, ...], "readme_dir": "<archive folder>", "readme": "<text>"}
  {"src_root": "<the foundry's output folder>", "dst_root": "<archive folder>", "items": ["<subfolder>", ...], "readme": "..."}
Every destination must be under ALLOWED_DST; no source may be a drive root. Progress: one marker per copied item,
then one per verified file, in <ORDER_OUT>\\progress (progress_total = items + files). Deliverable:
<ORDER_OUT>\\manifest.json (every file: src, dst, bytes, sha256; plus byte-identical duplicate groups), also copied
beside the README.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time

CHUNK = 64 << 20
ALLOWED_DST = ("b:\\archive\\", "b:\\foundry-output\\")


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def pairs_of(plan):
    if "moves" in plan:
        return [(os.path.normpath(m["src"]), os.path.normpath(m["dst"])) for m in plan["moves"]]
    sr, dr = os.path.normpath(plan["src_root"]), os.path.normpath(plan["dst_root"])
    return [(os.path.join(sr, i), os.path.join(dr, i)) for i in plan["items"]]


def files_of(src, dst):
    """(src_file, dst_file) for every file of one move."""
    if os.path.isfile(src):
        return [(src, dst)]
    return [(os.path.join(dp, f), os.path.join(dst, os.path.relpath(os.path.join(dp, f), src)))
            for dp, dn, fn in os.walk(src) for f in fn]


def robocopy(src, dst):
    """Copy one item (folder or file) with robocopy; rc 0-7 = success, >= 8 = failure."""
    if os.path.isdir(src):
        argv = ["robocopy", src, dst, "/E", "/J", "/COPY:DT", "/R:2", "/W:5", "/NP", "/NFL", "/NDL", "/NJH"]
    else:
        if os.path.basename(src) != os.path.basename(dst):
            raise ValueError("a single-file move keeps its name: %s -> %s" % (src, dst))
        argv = ["robocopy", os.path.dirname(src), os.path.dirname(dst), os.path.basename(src), "/J", "/COPY:DT",
                "/R:2", "/W:5", "/NP", "/NFL", "/NDL", "/NJH"]
    return subprocess.run(argv, capture_output=True, text=True).returncode


def main():
    plan_path, order_out = sys.argv[1], sys.argv[2]
    plan = json.load(open(plan_path, encoding="utf-8"))
    pairs = pairs_of(plan)
    assert pairs, "empty plan"
    for s, d in pairs:
        assert (d.lower() + "\\").startswith(ALLOWED_DST), "destination not under %s: %s" % (ALLOWED_DST, d)
        assert os.path.splitdrive(s)[1].strip("\\"), "a source may not be a drive root: %s" % s
        assert os.path.exists(s), "source not found: %s" % s
    readme_dir = os.path.normpath(plan.get("readme_dir") or plan.get("dst_root") or os.path.dirname(pairs[0][1]))
    prog = os.path.join(order_out, "progress")
    os.makedirs(prog, exist_ok=True)
    todo = [fp for s, d in pairs for fp in files_of(s, d)]
    total_b = sum(os.path.getsize(s) for s, _ in todo)
    print("plan: %d items, %d files, %.1f GB (progress total %d)" % (len(pairs), len(todo), total_b / 1e9,
                                                                    len(pairs) + len(todo)), flush=True)

    src_hash, hash_err = {}, []

    def hash_sources():
        for s, _ in todo:
            try:
                src_hash[s] = sha_file(s)
            except OSError as e:
                hash_err.append((s, str(e)))
    hasher = threading.Thread(target=hash_sources, daemon=True)
    hasher.start()

    t0, bad = time.time(), []
    for n, (s, d) in enumerate(pairs, 1):
        try:
            rc = robocopy(s, d)
        except ValueError as e:
            rc = 16
            print(str(e), flush=True)
        if rc >= 8:
            bad.append((s, "robocopy rc %d" % rc))
            print("COPY FAILED %s (robocopy rc %d)" % (s, rc), flush=True)
        open(os.path.join(prog, "c%03d.done" % n), "w").close()
        print("copied %d/%d items (%s), %.0f s" % (n, len(pairs), s, time.time() - t0), flush=True)
    copy_s = time.time() - t0
    print("COPY phase: %.1f GB in %.0f s = %.0f MB/s" % (total_b / 1e9, copy_s, total_b / 1e6 / max(1, copy_s)),
          flush=True)
    hasher.join()
    bad += hash_err

    manifest, t1, done_b = [], time.time(), 0
    for n, (s, d) in enumerate(todo, 1):
        if s not in src_hash:
            continue
        try:
            h_dst = sha_file(d) if os.path.exists(d) else None
            if h_dst != src_hash[s]:                             # missing or wrong: copy that file fresh, once
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(s, d)
                h_dst = sha_file(d)
        except OSError as e:
            bad.append((s, "%s: %s" % (type(e).__name__, e)))
            continue
        if h_dst != src_hash[s]:
            bad.append((s, "hash mismatch"))
            print("MISMATCH %s" % s, flush=True)
            continue
        size = os.path.getsize(s)
        manifest.append({"src": s, "dst": d, "bytes": size, "sha256": h_dst})
        done_b += size
        open(os.path.join(prog, "v%06d.done" % n), "w").close()
        if n % 50 == 0 or n == len(todo):
            print("verified %d/%d files, %.1f GB, %.0f MB/s" % (n, len(todo), done_b / 1e9,
                                                             done_b / 1e6 / max(1, time.time() - t1)), flush=True)

    groups = {}
    for m in manifest:
        groups.setdefault(m["sha256"], []).append(m["dst"])
    dups = [{"sha256": h, "bytes": next(m["bytes"] for m in manifest if m["sha256"] == h), "copies": sorted(ds)}
            for h, ds in groups.items() if len(ds) > 1]
    json.dump({"moves": [{"src": s, "dst": d} for s, d in pairs], "files": manifest, "duplicates": dups,
               "failed": bad, "copy_s": round(copy_s), "verify_s": round(time.time() - t1),
               "at": time.strftime("%Y-%m-%d %H:%M:%S")},
              open(os.path.join(order_out, "manifest.json"), "w", encoding="utf-8"), indent=1)
    if bad or len(manifest) != len(todo):
        print("NOT removing any source: %d problems, %d/%d files verified" % (len(bad), len(manifest), len(todo)),
              flush=True)
        sys.exit(1)
    stamp = time.strftime("%Y%m%d-%H%M")
    os.makedirs(readme_dir, exist_ok=True)
    shutil.copy2(os.path.join(order_out, "manifest.json"), os.path.join(readme_dir, "manifest-%s.json" % stamp))
    lines = [plan.get("readme", "").rstrip(), "",
             "Manifest: manifest-%s.json (%d files, %.1f GB, SHA-256 verified both sides before the sources were "
             "removed)." % (stamp, len(manifest), done_b / 1e9)]
    if dups:
        lines += ["", "Byte-identical duplicates (%d groups, %.1f GB reclaimable if one copy of each is kept):"
                  % (len(dups), sum(g["bytes"] * (len(g["copies"]) - 1) for g in dups) / 1e9)]
        for g in dups:
            lines += ["- %.2f GB x%d: %s" % (g["bytes"] / 1e9, len(g["copies"]), " | ".join(g["copies"]))]
    with open(os.path.join(readme_dir, "README.md"), "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n")
    for s, _ in pairs:
        shutil.rmtree(s) if os.path.isdir(s) else os.remove(s)
    print("DONE: %d files %.1f GB verified; %d duplicate groups; %d sources removed" % (len(manifest), done_b / 1e9,
                                                                                         len(dups), len(pairs)), flush=True)


if __name__ == "__main__":
    main()
