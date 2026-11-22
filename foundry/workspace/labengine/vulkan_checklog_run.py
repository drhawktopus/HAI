"""Diagnose Vulkan statefulness by detecting ops that yield different errors on repeated identical requests.

Usage:
    vulkan_checklog_run.py <diagnostic source dir (UNC)> <model> <num_ctx> {ORDER_OUT}

Process:
    1. Build Vulkan backend with GGML_VULKAN_CHECK_RESULTS=ON to log CPU vs GPU errors per op.
    2. Copy engine tree and swap in the built DLL (serving tree remains untouched).
    3. Run `ollama serve`, send the same short request three times.
    4. Compare op errors between ask 1 and asks 2/3. A change indicates state leakage.

Control: GGML_VK_DISABLE_COOPMAT (no state leakage expected).
Outputs: checklog.json, serve logs, build log. Temp artifacts are cleaned up."""
import json
import os
import re
import shutil
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_lab_engine as B  # noqa: E402
import engine_resume_probe as E  # noqa: E402
import engine_history_probe as H  # noqa: E402
import engine_switch_search as S  # noqa: E402

LINE = re.compile(r"LABHASH (\d+) (\S+) name=(.*?) ne=(\S+) src0=(\S+) src1=(\S+) in=(\S+) out=(\d+)")


def ops(text):
    """(op, name, ne, src0, src1, inputs' fingerprints, output's fingerprint) per checked op, in the order they ran."""
    return [(m.group(2), m.group(3), m.group(4), m.group(5), m.group(6), m.group(7), m.group(8)) for m in LINE.finditer(text)]


def compare(a, b):
    """Two asks' op lists -> the ops whose OUTPUT changed. `clean` = changed while every fingerprinted input matched:
    that op read something that is not one of its inputs. The first clean one is where the history comes in."""
    if [x[:5] for x in a] != [x[:5] for x in b]:
        return {"aligned": False, "len": [len(a), len(b)]}
    moved = [{"i": i, "op": x[0], "name": x[1], "ne": x[2], "src0": x[3], "src1": x[4], "inputs_same": x[5] == y[5],
              "in_first": x[5], "in_again": y[5]}
             for i, (x, y) in enumerate(zip(a, b)) if x[6] != y[6]]
    clean = [m for m in moved if m["inputs_same"]]
    by_op = {}
    for m in clean:
        k = "%s src0=%s src1=%s" % (m["op"], m["src0"].split(":")[0], m["src1"].split(":")[0])
        by_op[k] = by_op.get(k, 0) + 1
    return {"aligned": True, "ops": len(a), "moved": len(moved), "clean": len(clean),
            "first_moved": moved[0] if moved else None, "first_clean": clean[0] if clean else None,
            "moved_by_op": by_op, "largest": clean[:12]}


def main(argv):
    src_unc, model, num_ctx, out = argv[0], argv[1], int(argv[2]), argv[3]
    os.makedirs(out, exist_ok=True)
    rep = {"box": socket.gethostname(), "model": model, "num_ctx": num_ctx, "source": src_unc, "arms": []}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    src = os.path.join(B.WORK, "src-checklog-" + stamp)
    tree = None
    try:
        # 1. the diagnostic Vulkan backend
        B.log("copy %s -> %s" % (src_unc, src))
        shutil.copytree(src_unc, src)
        env = B.vs_env()
        logf = os.path.join(out, "build.log")
        bdir = os.path.join(src, "build-vulkan")
        # The checker links the CPU backend into the Vulkan one, which a backend-DLL build cannot do (first order,
        # 15:39: LNK1181 ggml-cpu.lib). So this set is built WITHOUT backend DLLs: one self-contained runner whose
        # Vulkan and CPU backends are linked in. The last -D on cmake's line wins; the CPU-variants pass needs backend
        # DLLs and fails to configure, and B.build falls through to its single AVX2 backend by itself.
        rep["cpu_mode"] = B.build(src, bdir, ["-DGGML_VULKAN=ON", "-DGGML_VULKAN_CHECK_RESULTS=ON", "-DGGML_BACKEND_DL=OFF"],
                                  env, logf)
        lib = B.assemble(bdir, os.path.join(out, "amd"), "vulkan", "ggml-vulkan.dll", [])
        dll = os.path.join(lib, "vulkan", "ggml-vulkan.dll")
        assert os.path.isfile(dll), "the build delivered no ggml-vulkan.dll"
        assert b"LABHASH" in open(dll, "rb").read(), "the built DLL does not carry the checker's log line"
        # 2. a copy of the lab's engine tree whose whole runner set is the diagnostic one
        exe, base_env = S.launcher_settings(open(S.LAUNCHER, encoding="utf-8", errors="replace").read())
        home = os.path.dirname(exe)
        tree = home + "-checklog-" + stamp
        B.log("copy %s -> %s" % (home, tree))
        shutil.copytree(home, tree, ignore=shutil.ignore_patterns("cuda_v*", "rocm*"))
        tlib = os.path.join(tree, "lib", "ollama")
        assert os.path.isfile(os.path.join(tlib, "vulkan", "ggml-vulkan.dll")), "the engine tree has no lib\\ollama\\vulkan\\ggml-vulkan.dll"
        for name in os.listdir(tlib):                    # the serving set's per-CPU backend DLLs: this runner links its own
            if name.lower().startswith("ggml-cpu-") and name.lower().endswith(".dll"):
                os.remove(os.path.join(tlib, name))
        for name in os.listdir(lib):
            if os.path.isfile(os.path.join(lib, name)):
                shutil.copy2(os.path.join(lib, name), os.path.join(tlib, name))
        shutil.copy2(dll, os.path.join(tlib, "ggml-vulkan.dll"))            # beside the runner, which imports it
        shutil.copy2(dll, os.path.join(tlib, "vulkan", "ggml-vulkan.dll"))  # and where ollama looks for the Vulkan library
        rep["runner_set"] = sorted(n for n in os.listdir(tlib) if n.lower().endswith((".dll", ".exe")))
        dexe = os.path.join(tree, "ollama.exe")
        E.OLLAMA = S.LAB_OLLAMA
        E.evict(model)                                   # the lab's serve lets its model go; nothing else of it is touched
        E.OLLAMA = "http://127.0.0.1:%d" % S.PORT

        class A:                                         # the ask's settings: short, the checker recomputes every op on the CPU
            n, temperature, seed, probs = 4, 0.6, 7, 0
        # 3. the runs
        for tag, arm_env in (("coopmat-off", {"GGML_VK_DISABLE_COOPMAT": "1"}), ("coopmat-on", {})):
            log_path = os.path.join(out, "serve-%s.log" % tag)
            p, log = S.serve(dexe, base_env, dict(arm_env, GGML_VK_LOG_HASH="1"), log_path)
            row = {"arm": tag, "env": arm_env}
            marks, answers = [], []
            try:
                port, lines = E.load(model, num_ctx)
                P = E.post("http://127.0.0.1:%d/tokenize" % port, {"content": H.P_TEXT, "add_special": True})["tokens"]
                for _ in range(3):
                    marks.append(os.path.getsize(log_path))
                    answers.append(H.ask(port, P, A)["tokens"])
                marks.append(os.path.getsize(log_path))
                row["answers_same"] = [answers[1] == answers[0], answers[2] == answers[0]]
            except Exception as e:                       # one arm's runner dying is a result; the other arm still runs
                row["died"] = repr(e)[:300]
            finally:
                S.stop(p, log)
            raw = open(log_path, "rb").read()
            marks = (marks + [len(raw)] * 4)[:4]
            row["ops_logged_in_all"] = len(ops(raw.decode("utf-8", "replace")))
            row["last_step"] = (re.findall(r"LABSTEP [^\r\n]*", raw.decode("utf-8", "replace")) or [None])[-1]
            asks = [ops(raw[marks[i]:marks[i + 1]].decode("utf-8", "replace")) for i in range(3)]
            row["ops_per_ask"] = [len(x) for x in asks]
            row["library"] = sorted(set(re.findall(r"library=(\w+)", raw.decode("utf-8", "replace"))))
            row["ask2_vs_ask1"] = compare(asks[0], asks[1])
            row["ask3_vs_ask1"] = compare(asks[0], asks[2])
            rep["arms"].append(row)
            with open(os.path.join(out, "checklog.json"), "w", encoding="utf-8") as f:
                json.dump(rep, f, indent=1)
            c = row["ask2_vs_ask1"]
            print(tag, "ops per ask", row["ops_per_ask"], "| moved", c.get("moved"), "of", c.get("ops"), "| by op", c.get("moved_by_op"),
                  "| first", c.get("first_moved"), flush=True)
        arm = {r["arm"]: r for r in rep["arms"]}
        on, off = arm["coopmat-on"]["ask2_vs_ask1"], arm["coopmat-off"]["ask2_vs_ask1"]
        if arm["coopmat-on"].get("died") or arm["coopmat-off"].get("died"):
            rep["verdict"] = "RUNNER DIED: coopmat-off %s (ops logged %s, last %s) | coopmat-on %s (ops logged %s, last %s)" % (
                arm["coopmat-off"].get("died", "ran"), arm["coopmat-off"]["ops_logged_in_all"], arm["coopmat-off"]["last_step"],
                arm["coopmat-on"].get("died", "ran"), arm["coopmat-on"]["ops_logged_in_all"], arm["coopmat-on"]["last_step"])
        elif not arm["coopmat-on"]["ops_per_ask"][0]:
            rep["verdict"] = "VOID - the checker logged no op (the diagnostic DLL was not the one loaded)"
        elif not on.get("aligned") or not off.get("aligned"):
            rep["verdict"] = "VOID - two identical asks did not run the same list of ops (on %s, off %s)" % (on, off)
        elif off.get("moved"):
            rep["verdict"] = "coopmat OFF also has %s changed ops (first %s) - read both arms; coopmat on: %s changed, %s with matching inputs, first clean %s" % (
                off.get("moved"), off.get("first_moved"), on.get("moved"), on.get("clean"), on.get("first_clean"))
        elif not on.get("moved"):
            rep["verdict"] = "NOT REPRODUCED under the diagnostic build: no op's output changed in either arm (this build waits after every op - a hint by itself)"
        else:
            rep["verdict"] = "coopmat on: %s of %s ops change between identical asks, %s of them with matching inputs (%s); first changed: %s; first with matching inputs: %s" % (
                on.get("moved"), on.get("ops"), on.get("clean"), on.get("moved_by_op"), on.get("first_moved"), on.get("first_clean"))
    finally:
        for d in (tree, src):
            if d and os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
        with open(os.path.join(out, "checklog.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
    print("VERDICT:", rep.get("verdict"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:5]))                # the runner appends the order name: ignore it
