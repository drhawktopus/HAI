"""Build the Strata hipBLASLt tuner using the engine's exact ROCm and calibrate the gfx1100 table.

Usage: python tune_hipblaslt_order.py <out_dir> <plan.json>

Constraint: Solution IDs are specific to the hipBLASLt build; build in an isolated venv with the exact matching ROCm. Do not install into Strata's tree or other environments (trainer/ComfyUI)."""
import json, os, re, subprocess, sys, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def run(cmd, log, env=None, cwd=None, shell=False):
    """One step; its output appended to `log`; raises on a non-zero exit with the tail of what it said."""
    print("$ %s" % (cmd if isinstance(cmd, str) else " ".join(cmd)), flush=True)
    with open(log, "a", encoding="utf-8", errors="replace") as lf:
        lf.write("\n$ %s\n" % (cmd if isinstance(cmd, str) else " ".join(cmd)))
        lf.flush()
        p = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, env=env, cwd=cwd, shell=shell)
    if p.returncode != 0:
        tail = open(log, encoding="utf-8", errors="replace").read()[-1500:]
        raise RuntimeError("step failed (rc %s): %s\n%s" % (p.returncode, cmd, tail))


def hipblaslt_version(rocm):
    h = os.path.join(rocm, "include", "hipblaslt", "hipblaslt-version.h")
    if not os.path.exists(h):
        h = os.path.join(rocm, "include", "hipblaslt-version.h")
    t = open(h, encoding="utf-8", errors="replace").read()
    v = dict(re.findall(r"#define\s+HIPBLASLT_VERSION_(MAJOR|MINOR|PATCH)\s+(\d+)", t))
    return int(v["MAJOR"]) * 100000 + int(v["MINOR"]) * 100 + int(v["PATCH"])


def cases(table):
    """tools/hip/<arch>-hipblaslt-*.txt rows -> tune_hipblaslt --case args (the doc's awk: NR>2, fields 1,5,2,3,4)."""
    out = []
    for i, line in enumerate(open(table, encoding="utf-8")):
        f = line.split()
        if i < 2 or len(f) < 5:
            continue
        out += ["--case", ",".join((f[0], f[4], f[1], f[2], f[3]))]
    assert out, "no cases read from %s - the shape list is broken, not the card" % table
    return out


def main():
    out, plan = sys.argv[1], json.load(open(sys.argv[2], encoding="utf-8"))
    os.makedirs(out, exist_ok=True)
    res = {"plan": plan, "at": time.strftime("%Y-%m-%d %H:%M:%S")}

    def save():
        json.dump(res, open(os.path.join(out, "result.json"), "w", encoding="utf-8"), indent=1)
    blog, tlog = os.path.join(out, "build.log"), os.path.join(out, "tune.log")
    src, work, arch = plan["strata_src"], plan["work"], plan["arch"]
    venv, bdir = os.path.join(work, ".rocm-win"), os.path.join(work, "build-hip-win")
    vpy = os.path.join(venv, "Scripts", "python.exe")
    try:
        # 1. the engine's exact ROCm, in its own venv (skipped when the stamp already says so)
        want = "%s libraries,devel,device-%s" % (plan["rocm_version"], arch)
        stamp = os.path.join(venv, "strata-rocm.txt")
        if not (os.path.exists(stamp) and open(stamp, encoding="utf-8").read().strip() == want):
            t0 = time.time()
            if not os.path.exists(vpy):
                run([plan["python"], "-m", "venv", venv], blog)
            run([vpy, "-m", "pip", "install", "--disable-pip-version-check", "--index-url", plan["rocm_index"],
                 "rocm[libraries,devel,device-%s]==%s" % (arch, plan["rocm_version"])], blog)
            run([vpy, "-m", "pip", "install", "--disable-pip-version-check", "cmake", "ninja"], blog)
            run([os.path.join(venv, "Scripts", "rocm-sdk.exe"), "init"], blog)
            open(stamp, "w", encoding="utf-8").write(want)
            res["install_s"] = round(time.time() - t0)
        rocm = subprocess.run([os.path.join(venv, "Scripts", "rocm-sdk.exe"), "path", "--root"], capture_output=True,
                              text=True).stdout.strip()
        res["rocm_root"] = rocm
        got = hipblaslt_version(rocm)
        res["hipblaslt_version"] = got
        save()
        print("ROCm %s at %s, hipBLASLt %d" % (plan["rocm_version"], rocm, got), flush=True)
        if got != int(plan["want_hipblaslt"]):
            raise RuntimeError("the kit's hipBLASLt is %d, the engine's is %s - a table from it would not be valid "
                               "for the engine" % (got, plan["want_hipblaslt"]))
        # 2. build ONLY tune_hipblaslt, with Strata's own Windows settings (tools/hip/build_windows.bat steps 2-3)
        rf = rocm.replace("\\", "/")
        vcvars = os.path.join(r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools", "VC", "Auxiliary",
                              "Build", "vcvars64.bat")
        env = dict(os.environ, HIP_PLATFORM="amd", HIP_PATH=rocm, ROCM_PATH=rocm,
                   PATH=os.pathsep.join([os.path.join(rocm, "bin"), os.path.join(rocm, "lib", "llvm", "bin"),
                                         os.path.join(venv, "Scripts"), os.environ.get("PATH", "")]))
        t0 = time.time()
        if not os.path.exists(os.path.join(bdir, "build.ninja")):
            conf = ('call "%s" >nul && cmake -G Ninja -S "%s" -B "%s" -DCMAKE_BUILD_TYPE=Release -DSTRATA_ENABLE_HIP=ON '
                    '-DSTRATA_ENABLE_CUDA=OFF -DSTRATA_BUILD_TESTS=OFF -DSTRATA_PREFILL_MMQ=ON -DSTRATA_NATIVE_EXPERTS=ON '
                    '-DSTRATA_PORTABLE=ON "-DCMAKE_HIP_ARCHITECTURES=%s" "-DCMAKE_C_COMPILER=%s/lib/llvm/bin/clang.exe" '
                    '"-DCMAKE_CXX_COMPILER=%s/lib/llvm/bin/clang++.exe" "-DCMAKE_HIP_COMPILER=%s/lib/llvm/bin/clang++.exe" '
                    '"-DCMAKE_HIP_COMPILER_ROCM_ROOT=%s" "-DCMAKE_PREFIX_PATH=%s" '
                    '"-DCMAKE_HIP_FLAGS=--rocm-path=%s --rocm-device-lib-path=%s/lib/llvm/amdgcn/bitcode"'
                    % (vcvars, src, bdir, arch, rf, rf, rf, rf, rf, rf, rf))
            run(conf, blog, env=env, shell=True)
        run('call "%s" >nul && cmake --build "%s" --target tune_hipblaslt' % (vcvars, bdir), blog, env=env, shell=True)
        exe = os.path.join(bdir, "tune_hipblaslt.exe")
        assert os.path.exists(exe), "the build reported success but %s is absent" % exe
        res["build_s"] = round(time.time() - t0)
        save()
        # 3. calibrate on the card: the shipped gfx1100 shapes (the engine's dense GEMMs), timed by the tuner itself.
        # It RUNS on the engine's own ROCm (engine\rocm\bin + its kernel pack): the exact libraries Strata loads, so the
        # table's solution ids are the engine's by construction. (10-18 first run: on the kit's folders rocBLAS found
        # no gfx1100 kernels - hipErrorInvalidDeviceFunction - TheRock's device code sits in separate kernel packs.)
        # Run 2 (PATH = engine\rocm\bin) failed the same way, while strata.exe runs hipBLAS fine on this card - so the
        # tuner runs EXACTLY as strata.exe does: from a COPY of the engine folder (strata.exe's own layout: the HIP
        # runtime beside the exe, rocm\bin + rocm\.kpack under it), with the tuner exe placed beside them. Strata's
        # install is not touched. plan["engine_dir"] names the engine; the copy is made once.
        import shutil
        run_dir = os.path.join(work, "engine-run")
        if not os.path.exists(os.path.join(run_dir, "strata.exe")):
            shutil.copytree(plan["engine_dir"], run_dir, dirs_exist_ok=True)
        shutil.copy2(exe, os.path.join(run_dir, "tune_hipblaslt.exe"))
        exe = os.path.join(run_dir, "tune_hipblaslt.exe")
        clean_path = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                                     if "rocm" not in p.lower() and "hip" not in p.lower())
        run_env = dict(os.environ, PATH=os.pathsep.join([run_dir, os.path.join(run_dir, "rocm", "bin"), clean_path]))
        for k in ("HIP_PATH", "ROCM_PATH", "HIP_PLATFORM"):
            run_env.pop(k, None)
        res["run_dir"] = run_dir
        table = os.path.join(out, "%s-hipblaslt-%d.txt" % (arch, got))
        t0 = time.time()
        run([exe] + cases(os.path.join(src, "tools", "hip", plan["cases_from"])) + ["--tuning-out", table], tlog,
            env=run_env, cwd=run_dir)
        res["tune_s"] = round(time.time() - t0)
        lines = open(table, encoding="utf-8").read().splitlines()
        res["table"] = {"path": table, "rows": len(lines) - 2, "header": lines[:2]}
        hdr = lines[1].split() if len(lines) > 1 else []
        if hdr[1:3] != [arch, str(got)]:
            raise RuntimeError("the table's header %r does not name %s %d" % (lines[:2], arch, got))
        res["result"] = "ok"
        print("table: %d rows, header %r, tune %ss" % (res["table"]["rows"], lines[1], res["tune_s"]), flush=True)
    except Exception as e:                                                   # noqa: BLE001
        res["result"] = "FAILED: %s" % (str(e)[:2000],)
        print(res["result"], flush=True)
        save()
        return 1
    save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
