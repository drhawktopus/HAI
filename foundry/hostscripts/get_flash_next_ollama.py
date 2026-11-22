"""Download ollama's own qwen3.8-flash-next:125b-a6b-q4_K_M, WHOLE, into a folder in ollama's store layout (a disk with
room for the whole model; linking it into the model store is a separate step).
Big layer: WORKERS parallel HTTP range requests, each 64 MB piece written in place into one sparse file (no second
copy: the disk need hold the model only once); per-piece resume via a done-list; every response must be 206 with the
exact length. Every layer is SHA-256-verified against its digest before it is renamed into place; the manifest is
written last. Setting: FLASHNEXT_OLLAMA_ROOT (the folder), in the lab.env beside this script.
Log: <that folder>\\download.log (DONE / FAILED on the last line)."""
import hashlib, json, os, subprocess, sys, threading, time, urllib.request

import lab_settings

ROOT = lab_settings.setting("FLASHNEXT_OLLAMA_ROOT", "")
if not ROOT:
    sys.exit("FLASHNEXT_OLLAMA_ROOT is not set: the folder the model downloads into")
REPO, TAG = "library/qwen3.8-flash-next", "125b-a6b-q4_K_M"
REG = "https://registry.ollama.ai/v2/%s" % REPO
LOG = os.path.join(ROOT, "download.log")
PIECE = 64 << 20
WORKERS = 1                           # one stream: ollama's CDN caps the client anyway
os.makedirs(os.path.join(ROOT, "blobs"), exist_ok=True)
_lk = threading.Lock()


def log(m):
    with _lk, open(LOG, "a", encoding="utf-8") as f:
        f.write("%s %s\n" % (time.strftime("%H:%M:%S"), m))


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(16 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def fetch_range(dig, start, end):
    req = urllib.request.Request("%s/blobs/%s" % (REG, dig), headers={"Range": "bytes=%d-%d" % (start, end)})
    with urllib.request.urlopen(req, timeout=120) as r:
        if r.status != 206:
            raise RuntimeError("server ignored the range request (HTTP %d)" % r.status)
        data = r.read()
    if len(data) != end - start + 1:
        raise RuntimeError("short piece %d-%d: %d bytes" % (start, end, len(data)))
    return data


def parallel_blob(dig, size, part):
    donef = part + ".done.json"
    done = set()
    for p in (donef, donef + ".tmp"):                  # the .tmp can be the newer list if a replace was refused
        try:
            done |= set(json.load(open(p)))
        except (OSError, ValueError):
            pass
    if not os.path.exists(part):
        open(part, "wb").close()
        subprocess.run(["fsutil", "sparse", "setflag", part], capture_output=True)
        with open(part, "r+b") as f:
            f.truncate(size)
    n = (size + PIECE - 1) // PIECE
    todo = [i for i in range(n) if i not in done]
    log("%s: %d pieces, %d already done, %d workers" % (dig[:19], n, len(done), WORKERS))
    errors, t0, got = [], time.time(), [0]

    def save_done():
        with open(donef + ".tmp", "w") as df:
            json.dump(sorted(done), df)
        for attempt in range(20):                      # Windows refuses the replace while a scanner holds the file
            try:
                os.replace(donef + ".tmp", donef)
                return
            except PermissionError:
                time.sleep(0.25)
        # still refused: the .tmp holds the full list and the resume reads it, so carry on

    def worker():
        try:
            with open(part, "r+b") as f:
                while True:
                    with _lk:
                        if not todo or errors:
                            return
                        i = todo.pop(0)
                    s, e = i * PIECE, min(size, (i + 1) * PIECE) - 1
                    for attempt in range(1, 6):
                        try:
                            data = fetch_range(dig, s, e)
                            break
                        except Exception as ex:                      # noqa: BLE001
                            if attempt == 5:
                                with _lk:
                                    errors.append("piece %d: %s" % (i, ex))
                                return
                            time.sleep(2 * attempt)
                    f.seek(s)
                    f.write(data)
                    with _lk:
                        done.add(i)
                        got[0] += len(data)
                        save_done()
        except Exception as ex:                                      # noqa: BLE001
            with _lk:
                errors.append("worker died: %s: %s" % (type(ex).__name__, ex))

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(WORKERS)]
    for t in threads:
        t.start()
    while any(t.is_alive() for t in threads):
        time.sleep(30)
        el = time.time() - t0
        log("%s: %d/%d pieces, %.1f GB this run, %.0f MB/s" % (dig[:19], len(done), n, got[0] / 1e9, got[0] / 1e6 / max(el, 1)))
    if errors:
        raise RuntimeError("; ".join(errors[:3]))
    assert len(done) == n, "pieces missing: %d of %d" % (n - len(done), n)
    subprocess.run(["fsutil", "sparse", "setflag", part, "0"], capture_output=True)
    os.remove(donef)


try:
    req = urllib.request.Request("%s/manifests/%s" % (REG, TAG),
                                 headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"})
    raw = urllib.request.urlopen(req, timeout=60).read()
    man = json.loads(raw)
    layers = [man["config"]] + man["layers"]
    log("manifest: %d files, %.1f GB" % (len(layers), sum(l["size"] for l in layers) / 1e9))
    for l in layers:
        dig, size = l["digest"], l["size"]
        dst = os.path.join(ROOT, "blobs", dig.replace(":", "-"))
        if os.path.exists(dst) and os.path.getsize(dst) == size:
            log("have %s (%.2f GB)" % (dig[:19], size / 1e9))
            continue
        part = dst + ".partial"
        if size > 1 << 30:
            parallel_blob(dig, size, part)
        else:
            subprocess.run(["curl.exe", "-L", "--fail", "-sS", "--retry", "3", "-o", part, "%s/blobs/%s" % (REG, dig)])
        if os.path.getsize(part) != size:
            raise RuntimeError("%s size %d != %d" % (dig, os.path.getsize(part), size))
        log("hashing %s (%.2f GB)" % (dig[:19], size / 1e9))
        actual = sha256(part)
        if actual != dig:
            os.replace(part, part + ".BADHASH")
            raise RuntimeError("%s hash mismatch (%s) - kept as .BADHASH" % (dig, actual))
        os.replace(part, dst)
        log("verified %s (%.2f GB)" % (dig[:19], size / 1e9))
    mdir = os.path.join(ROOT, "manifests", "registry.ollama.ai", *REPO.split("/"))
    os.makedirs(mdir, exist_ok=True)
    with open(os.path.join(mdir, TAG), "wb") as f:
        f.write(raw)
    log("DONE: all %d files verified; manifest written" % len(layers))
except Exception as e:                                                   # noqa: BLE001
    log("FAILED: %s: %s" % (type(e).__name__, e))
