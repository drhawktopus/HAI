r"""Build Strata's image encoder (strata-vision) for Windows/AMD, install it, and verify it works.

    build_strata_vision.py {ORDER_OUT} <serving plan (json)> <mmproj source file>

Strata's setup refuses to build the CPU-only image encoder on Windows/AMD. This script builds it locally using the pinned llama.cpp source and installs it beside the engine.

Steps:
  1. Build tools\vision (CPU-only) using Strata's flags (-DSTRATA_VISION_CUDA=OFF -DSTRATA_PORTABLE=OFF).
  2. Install strata-vision.exe to {ORDER_OUT} and <strata source>\engine. Existing binaries are preserved with timestamps.
  3. Copy mmproj weights to the plan's gguf_dir; verify size and SHA-256 against the source.
  4. Verify: start the encoder, wait for READY, and confirm it successfully encodes test images. Any failure aborts the build.

Artifacts:
  - vision.json in {ORDER_OUT}: paths, hashes, and encoder logs."""
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "labengine"))
import build_lab_engine as B  # noqa: E402  the lab engine's compiler environment (vcvars64, VS's cmake + ninja)
import strata_arm as S        # noqa: E402  band_png: the test pictures
import strata_serve           # noqa: E402  _src: where this plan's Strata source lives


def log(msg):
    print("[strata-vision] " + msg, flush=True)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def first_shard(plan):
    return os.path.join(plan["gguf_dir"], sorted(plan["shards"])[0])


def start_encoder(exe, mmproj, model, threads, max_tokens, cwd, errlog):
    args = [exe, "--mmproj", mmproj, "--model", model, "--threads", str(threads), "--max-tokens", str(max_tokens)]
    return args, subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errlog, text=True,
                                  encoding="utf-8", bufsize=1, cwd=cwd)


def read_line(proc, seconds):
    """One line from the encoder, or None when it says nothing for `seconds` (it is then ended by the caller)."""
    got = []
    t = threading.Thread(target=lambda: got.append(proc.stdout.readline()), daemon=True)
    t.start()
    t.join(seconds)
    return got[0].strip() if got else None


def prove(exe, mmproj, model, out, threads, max_tokens):
    """The installed encoder, started the way serve\\server.py's Vision starts it. -> the proof record."""
    work = os.path.join(out, "prove")
    os.makedirs(work, exist_ok=True)
    rec = {"threads": threads, "max_tokens": max_tokens, "encodes": []}
    with open(os.path.join(out, "encoder.err.log"), "w", encoding="utf-8") as errlog:
        t0 = time.time()
        rec["args"], proc = start_encoder(exe, mmproj, model, threads, max_tokens, work, errlog)
        try:
            # the server waits VISION_READY_S for this line; the model's vocabulary and the 0.9 GB tower load first
            line = read_line(proc, 600)
            rec["ready_line"], rec["ready_s"] = line, round(time.time() - t0, 1)
            if not line or not line.startswith("READY"):
                rec["ok"] = False
                return rec
            for name, colours in (("rgb.png", S.VISION_ARMS[0][1]), ("bgr.png", S.VISION_ARMS[1][1])):
                with open(os.path.join(work, name), "wb") as f:
                    f.write(S.band_png(colours))
                t1 = time.time()
                proc.stdin.write("ENC %s %s.emb\n" % (name, name))
                proc.stdin.flush()
                reply = read_line(proc, 600)
                emb = os.path.join(work, name + ".emb")
                rec["encodes"].append({"picture": name, "reply": reply, "wall_s": round(time.time() - t1, 1),
                                       "embedding_bytes": os.path.getsize(emb) if os.path.exists(emb) else 0})
            rec["ok"] = all((e["reply"] or "").startswith("OK ") and e["embedding_bytes"] > 0 for e in rec["encodes"])
            return rec
        finally:
            try:
                proc.kill()
                proc.wait(timeout=10)
            except Exception:                                                # noqa: BLE001 - the order's verdict stands
                pass


def main(argv):
    out, plan_path, mmproj_src = argv[0], argv[1], argv[2]
    os.makedirs(out, exist_ok=True)
    plan = json.load(open(plan_path, encoding="utf-8"))
    res = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "plan": plan_path, "result": "started"}

    def save():
        json.dump(res, open(os.path.join(out, "vision.json"), "w", encoding="utf-8"), indent=1)

    try:
        src = strata_serve._src(plan)
        eng, llama, vsrc = os.path.join(src, "engine"), os.path.join(src, "third_party", "llama.cpp"), os.path.join(src, "tools", "vision")
        for need in (os.path.join(vsrc, "CMakeLists.txt"), os.path.join(vsrc, "strata_vision.cpp"),
                     os.path.join(llama, "CMakeLists.txt"), os.path.join(llama, "tools", "mtmd"),
                     os.path.join(eng, "strata.exe"), first_shard(plan), mmproj_src, B.VCVARS, B.CMAKE, B.NINJA):
            assert os.path.exists(need), "missing: %s" % need
        res.update(strata_src=src, llama=llama, mmproj_src=mmproj_src)
        env = B.vs_env()
        bdir, blog = os.path.join(out, "build-vision"), os.path.join(out, "build.log")
        t0 = time.time()
        rc = B.run([B.CMAKE, "-S", vsrc, "-B", bdir, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
                    "-DCMAKE_MAKE_PROGRAM=" + B.NINJA, "-DLLAMA_DIR=" + llama, "-DSTRATA_VISION_CUDA=OFF",
                    "-DSTRATA_PORTABLE=OFF"], env, blog)
        assert rc == 0, "cmake configure failed (rc %s) - build.log" % rc
        log("configured in %.0f s" % (time.time() - t0))
        rc = B.run([B.CMAKE, "--build", bdir, "--target", "strata-vision"], env, blog)
        assert rc == 0, "the build failed (rc %s) - build.log" % rc
        built = os.path.join(bdir, "bin", "strata-vision.exe")
        assert os.path.isfile(built), "the build ended without %s" % built
        res.update(build_s=round(time.time() - t0, 1), exe_sha256=sha256(built), exe_bytes=os.path.getsize(built))
        log("built in %.0f s: %d bytes" % (res["build_s"], res["exe_bytes"]))
        shutil.copy2(built, os.path.join(out, "strata-vision.exe"))             # the delivered product

        target = os.path.join(eng, "strata-vision.exe")
        if os.path.exists(target) and sha256(target) != res["exe_sha256"]:
            kept = target + ".before-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(target, kept)
            res["kept_previous"] = kept
        shutil.copy2(built, target)
        assert sha256(target) == res["exe_sha256"], "the installed encoder differs from the built one"
        res["installed"] = target

        mmproj = os.path.join(plan["gguf_dir"], os.path.basename(mmproj_src))
        if not (os.path.exists(mmproj) and os.path.getsize(mmproj) == os.path.getsize(mmproj_src)):
            log("copying the vision weights (%d bytes) ..." % os.path.getsize(mmproj_src))
            shutil.copy2(mmproj_src, mmproj)
        want, got = sha256(mmproj_src), sha256(mmproj)
        assert want == got, "the vision weights on this box differ from the source (%s vs %s)" % (got[:12], want[:12])
        res.update(mmproj=mmproj, mmproj_sha256=got, mmproj_bytes=os.path.getsize(mmproj))
        save()

        threads = max(1, (os.cpu_count() or 8) // 2)                           # setup.py's own choice for the CPU encoder
        res["proof"] = prove(target, mmproj, first_shard(plan), out, threads, 300)
        res["vision_section"] = {"exe": target, "mmproj": mmproj, "model": first_shard(plan), "gpu": False,
                                 "max_tokens": 300, "threads": threads}
        res["result"] = "ok" if res["proof"].get("ok") else "THE ENCODER DID NOT PROVE ITSELF: %s" % json.dumps(res["proof"])[:500]
        log("proof: ready %r in %s s; encodes %s" % (res["proof"].get("ready_line"), res["proof"].get("ready_s"),
                                                     [(e["reply"], e["wall_s"]) for e in res["proof"].get("encodes", [])]))
    except Exception as e:                                                      # noqa: BLE001 - the order must say why
        res["result"] = "ERROR %s: %s" % (type(e).__name__, e)
    save()
    log(res["result"])
    return res["result"]


if __name__ == "__main__":
    sys.exit(0 if main(sys.argv[1:4]) == "ok" else 1)        # the runner appends the order name: ignore it
