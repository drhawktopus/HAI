"""Diagnose Vulkan coopmat history retention by building and testing lab-specific engine switches.

Usage: vulkan_labsw_run.py <diagnostic_source_dir> <model> <num_ctx> <ORDER_OUT> [arms]

This script isolates the specific coopmat path component responsible for repeating engine history. It builds the
Vulkan backend from the diagnostic source tree using the lab's switch flags, copies the engine tree to a temporary
directory to avoid modifying the live serving tree, and runs engine_switch_search trials. Each trial uses a fresh
'ollama serve' instance to ensure clean state. The script outputs switch_search.json, serve logs, and build logs.
Temporary build and engine directories are removed upon completion."""
import json
import os
import shutil
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_lab_engine as B  # noqa: E402
import engine_resume_probe as E  # noqa: E402
import engine_switch_search as S  # noqa: E402

ARMS = "|LAB_VK_NO_CM1_INT=1|LAB_VK_NO_QUANT_Y=1|LAB_VK_NO_QUANT_Y=1,LAB_VK_NO_F16_B=1|LAB_VK_NO_Y_CACHE=1|GGML_VK_DISABLE_COOPMAT=1"
MARK = b"LAB_VK_NO_Y_CACHE"


class Ask:
    n, temperature, seed, probs = 160, 0.6, 7, 8


def main(argv):
    src_unc, model, num_ctx, out = argv[0], argv[1], int(argv[2]), argv[3]
    arms = argv[4] if len(argv) > 4 and ("=" in argv[4] or argv[4].startswith("|")) else ARMS   # the runner appends the order name
    os.makedirs(out, exist_ok=True)
    a = Ask()
    a.model, a.num_ctx, a.out = model, num_ctx, out
    if "LAB_VK_CPU_OPS" in arms:                         # ops pushed to the CPU copy their weights across every token: slow
        a.n = 64
    if "LAB_VK_FA_HASH" in arms:                         # every attention call is logged: keep the asks short
        a.n = 24
    if "LAB_VK_ALL_HASH" in arms:                        # every op of every graph is logged: two tokens are enough
        a.n = 2
    rep = {"box": socket.gethostname(), "model": model, "num_ctx": num_ctx, "source": src_unc, "arms": [], "verdict": None}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    src = os.path.join(B.WORK, "src-labsw-" + stamp)
    tree = None
    try:
        B.log("copy %s -> %s" % (src_unc, src))
        shutil.copytree(src_unc, src)
        env = B.vs_env()
        logf = os.path.join(out, "build.log")
        bdir = os.path.join(src, "build-vulkan")
        rep["cpu_mode"] = B.build(src, bdir, ["-DGGML_VULKAN=ON"], env, logf)
        lib = B.assemble(bdir, os.path.join(out, "amd"), "vulkan", "ggml-vulkan.dll", [])
        dll = os.path.join(lib, "vulkan", "ggml-vulkan.dll")
        assert os.path.isfile(dll), "the build delivered no ggml-vulkan.dll"
        assert "--no-mark" in argv or MARK in open(dll, "rb").read(), "the built DLL does not carry the lab switches"
        exe, base_env = S.launcher_settings(open(S.LAUNCHER, encoding="utf-8", errors="replace").read())
        home = os.path.dirname(exe)
        tree = home + "-labsw-" + stamp
        B.log("copy %s -> %s" % (home, tree))
        shutil.copytree(home, tree, ignore=shutil.ignore_patterns("cuda_v*", "rocm*"))
        tlib = os.path.join(tree, "lib", "ollama")
        assert os.path.isfile(os.path.join(tlib, "vulkan", "ggml-vulkan.dll")), "the engine tree has no lib\\ollama\\vulkan\\ggml-vulkan.dll"
        # the whole set, as an engine install lays it down: same source and flags as the serving one, plus the switches
        for name in os.listdir(lib):
            if os.path.isfile(os.path.join(lib, name)):
                shutil.copy2(os.path.join(lib, name), os.path.join(tlib, name))
        shutil.copy2(dll, os.path.join(tlib, "vulkan", "ggml-vulkan.dll"))
        assert "--no-mark" in argv or MARK in open(os.path.join(tlib, "vulkan", "ggml-vulkan.dll"), "rb").read()
        dexe = os.path.join(tree, "ollama.exe")
        rep["exe"] = dexe
        E.OLLAMA = S.LAB_OLLAMA
        E.evict(model)                                   # the lab's serve lets its model go; nothing else of it is touched
        E.OLLAMA = "http://127.0.0.1:%d" % S.PORT
        for i, spec in enumerate(arms.split("|")):
            arm_env = dict(x.split("=", 1) for x in spec.split(",") if x)
            try:
                S.trial(a, dexe, base_env, arm_env, "arm%d" % i, rep)
            except Exception as e:                       # one arm failing to load is a result; the others still run
                rep["arms"].append({"arm": "arm%d" % i, "env": arm_env, "died": repr(e)[:300], "repeats": None})
                print("arm%d" % i, arm_env, "DIED", repr(e)[:200], flush=True)
        rep["verdict"] = "ARMS: " + "; ".join("%s -> %s" % (json.dumps(r["env"]), "DIED" if r.get("died") else (
            "REPEATS" if r["repeats"] else "does not repeat") + " %s tok/s" % r.get("tok_s")) for r in rep["arms"])
        # --speed=<tag>,<tag>: the long read on the INSTALLED trees of those lab revisions and on this build, interleaved
        # (installed, NEW, installed, NEW, installed ...), a fresh serve and load each, three reads, the first a warm-up
        speed = next((x.split("=", 1)[1] for x in argv if x.startswith("--speed=")), None)
        if speed:
            stem = home[:home.rindex("-lab") + 4]
            order = []
            for t in speed.split(","):
                order += [(t, os.path.join(stem + t, "ollama.exe")), ("NEW", dexe)]
            order.append((speed.split(",")[0], order[0][1]))
            rep["speed"] = []
            for i, (tag, exe_i) in enumerate(order):
                p, log = S.serve(exe_i, base_env, {}, os.path.join(out, "serve-speed-%d-%s.log" % (i, tag)))
                row = {"i": i, "tag": tag}
                try:
                    port, _lines = E.load(model, num_ctx)
                    reads = [S.long_read(a, port) for _ in range(3)]
                    row["reads"] = [(round(r["read_tok_s"], 1), round(r["write_tok_s"], 1)) for r in reads[1:]]
                except Exception as e:
                    row["died"] = repr(e)[:300]
                finally:
                    S.stop(p, log)
                rep["speed"].append(row)
                print("speed", tag, row.get("died") or row["reads"], flush=True)
            rep["verdict"] += " | SPEED (read, write tok/s): " + "; ".join("%s %s" % (r["tag"], r.get("died") or r["reads"]) for r in rep["speed"])
    finally:
        for d in (tree, src):
            if d and os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
        with open(os.path.join(out, "switch_search.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
    print("VERDICT:", rep.get("verdict"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:8]))
