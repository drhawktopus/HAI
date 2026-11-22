"""Build the LAB ENGINE for GPU factories (engine parity with rigby).

    build_lab_engine.py <source dir (UNC)> {ORDER_OUT}

Source: ollama 0.40.5 / llama.cpp commit 620d222 + lab patches #0, #3, #2
(pending token, MTP carry + next-step resume, sampler random state).

Constraints:
- One compiler (MSVC, VS 2022 Build Tools, Ninja) per set; do not mix compilers.
- GPU sets: nvidia (CUDA 13.0, sm_86), amd (Vulkan SDK 1.4.363).
- CPU: per-microarch variants if MSVC builds them, else single AVX2+FMA backend.
- Pre-ship checks: assert AVX2=1 in system_info and verify patch markers in built libs.
- Deliverables: sets + logs + sha256 manifest. This script installs NOTHING."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

VCVARS = r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
VS_CMAKE = (r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions"
            r"\Microsoft\CMake\CMake\bin")
VS_NINJA = (r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions"
            r"\Microsoft\CMake\Ninja")
CUDA = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.0"
# Use full paths: Windows subprocess resolves bare names against the parent process's PATH, not the child env's.
# A bare "cmake" fails with FileNotFoundError if the build tools are only on the child's PATH.
CMAKE = os.path.join(VS_CMAKE, "cmake.exe")
NINJA = os.path.join(VS_NINJA, "ninja.exe")
NVCC = os.path.join(CUDA, "bin", "nvcc.exe")
VULKAN_SDK = r"C:\VulkanSDK\1.4.363.0"
WORK = r"C:\labbuild"
MARKERS = (b"lab_resume", b"lab_rng", b"lab_get_carry")   # lab-only names: stock has `pending_token_idx`
COMPAT_MARK = b"llama_ollama_compat"                         # ollama's llama/compat namespace (exported names)
COMMON = ["-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=ON", "-DGGML_BACKEND_DL=ON",
          "-DGGML_NATIVE=OFF", "-DLLAMA_CURL=OFF", "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_EXAMPLES=OFF",
          "-DLLAMA_BUILD_SERVER=ON"]
CPU_VARIANTS = ["-DGGML_CPU_ALL_VARIANTS=ON"]
CPU_SINGLE = ["-DGGML_CPU_ALL_VARIANTS=OFF", "-DGGML_AVX=ON", "-DGGML_AVX2=ON", "-DGGML_FMA=ON", "-DGGML_F16C=ON"]


def log(msg):
    print("[labengine] " + msg, flush=True)


def vs_env():
    """The VS x64 developer environment, read from vcvars64.bat itself (cl, link, the SDK paths)."""
    out = subprocess.run(["cmd", "/c", "call", VCVARS, "&&", "set"], capture_output=True, text=True,
                         errors="replace").stdout
    env = dict(os.environ)
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    env["PATH"] = os.pathsep.join([VS_CMAKE, VS_NINJA, os.path.join(CUDA, "bin"), os.path.join(VULKAN_SDK, "Bin"),
                                   env.get("PATH", "")])
    env["CUDA_PATH"] = CUDA
    env["VULKAN_SDK"] = VULKAN_SDK
    return env


def run(argv, env, logf):
    with open(logf, "a", encoding="utf-8", errors="replace") as f:
        f.write("\n$ " + " ".join(argv) + "\n")
        f.flush()
        return subprocess.run(argv, env=env, stdout=f, stderr=subprocess.STDOUT).returncode


def build(src, bdir, backend_flags, env, logf):
    """Configure + build; CPU variants first, a single AVX2 backend if MSVC cannot build them. -> the CPU mode used."""
    for mode, cpu in (("variants", CPU_VARIANTS), ("avx2", CPU_SINGLE)):
        shutil.rmtree(bdir, ignore_errors=True)
        rc = run([CMAKE, "-S", src, "-B", bdir, "-DCMAKE_MAKE_PROGRAM=" + NINJA] + COMMON + cpu + backend_flags,
                 env, logf)
        if rc == 0:
            rc = run([CMAKE, "--build", bdir, "-j", str(os.cpu_count() or 8)], env, logf)
        log("%s: cpu %s -> rc %d" % (os.path.basename(bdir), mode, rc))
        if rc == 0:
            return mode
    raise SystemExit("build failed for %s - see %s" % (bdir, logf))


def assemble(bdir, out, backend_dir, backend_dll, extra_dlls):
    """lib/ollama = every runtime DLL + llama-server.exe from bin; the GPU backend goes into its ollama subfolder."""
    bin_dir = os.path.join(bdir, "bin")
    lib = os.path.join(out, "lib", "ollama")
    os.makedirs(os.path.join(lib, backend_dir), exist_ok=True)
    for name in os.listdir(bin_dir):
        p = os.path.join(bin_dir, name)
        if name == backend_dll:
            shutil.copy2(p, os.path.join(lib, backend_dir, name))
        elif name.endswith(".dll") or name == "llama-server.exe":
            shutil.copy2(p, os.path.join(lib, name))
    for p in extra_dlls:
        shutil.copy2(p, os.path.join(lib, backend_dir, os.path.basename(p)))
    return lib


def check(lib, backend_dir, env, logf):
    """system_info from the built server (AVX2 = 1 asserted) + marker counts per library."""
    server = os.path.join(lib, "llama-server.exe")
    e = dict(env)
    e["GGML_BACKEND_PATH"] = ""
    r = subprocess.run([server, "-m", "nonexistent.gguf", "-v"], capture_output=True, text=True, errors="replace",
                       env=e, cwd=lib)
    info = [l for l in (r.stdout + r.stderr).splitlines() if "system_info" in l or "CPU :" in l]
    with open(logf, "a", encoding="utf-8") as f:
        f.write("\n".join(info) + "\n")
    assert any("AVX2 = 1" in l for l in info), "the CPU backend has no AVX2 - every number on it would be void"
    marks = {}
    for root, _d, files in os.walk(lib):
        for n in files:
            if n.endswith(".dll") or n.endswith(".exe"):
                data = open(os.path.join(root, n), "rb").read()
                c = sum(data.count(m) for m in MARKERS)
                if c:
                    marks[os.path.relpath(os.path.join(root, n), lib)] = c
    assert marks, "no lab patch marker in any built library - the patches did not reach the build"
    # apply_ollama_compat.py is required; without it, llama-server cannot parse ollama-format blobs
    llama = os.path.join(lib, "llama.dll")
    compat = open(llama, "rb").read().count(COMPAT_MARK) if os.path.exists(llama) else 0
    assert compat, "llama.dll carries no ollama compat layer - ollama-format blobs would not load"
    marks["llama.dll:ollama_compat"] = compat
    return info, marks


def manifest(root):
    rows = {}
    for d, _s, files in os.walk(root):
        for n in files:
            p = os.path.join(d, n)
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            rows[os.path.relpath(p, root).replace("\\", "/")] = h.hexdigest()
    return rows


def main(argv):
    src_unc, out = argv[0], argv[1]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(out, exist_ok=True)
    logf = os.path.join(out, "build.log")
    src = os.path.join(WORK, "src-%s-%s" % (os.path.basename(src_unc.rstrip("\\/")).replace("llama.cpp-", ""), stamp))
    log("copy %s -> %s" % (src_unc, src))
    def skip(d, names):
        # .git and build FOLDERS only - the 3rd order's "build*" pattern also dropped common\build-info.cpp.in (a
        # source file), and cmake failed configuring it
        return [n for n in names if n == ".git" or (n.startswith("build") and os.path.isdir(os.path.join(d, n)))]
    shutil.copytree(src_unc, src, ignore=skip)
    env = vs_env()
    result = {"source": src_unc, "stamp": stamp, "sets": {}}
    cublas = [os.path.join(CUDA, "bin", "x64", n) if os.path.exists(os.path.join(CUDA, "bin", "x64", n))
              else os.path.join(CUDA, "bin", n) for n in ("cublas64_13.dll", "cublasLt64_13.dll", "cudart64_13.dll")]
    for name, flags, bdir_name, backend_dir, backend_dll, extra in (
            ("nvidia", ["-DGGML_CUDA=ON", "-DCMAKE_CUDA_ARCHITECTURES=86", "-DCMAKE_CUDA_COMPILER=" + NVCC],
             "build-cuda", "cuda_v13",
             "ggml-cuda.dll", cublas),
            ("amd", ["-DGGML_VULKAN=ON"], "build-vulkan", "vulkan", "ggml-vulkan.dll", [])):
        bdir = os.path.join(src, bdir_name)
        try:                                                     # each set on its own: one failing never hides the other
            mode = build(src, bdir, flags, env, logf)
            lib = assemble(bdir, os.path.join(out, name), backend_dir, backend_dll,
                           [p for p in extra if os.path.exists(p)])
            info, marks = check(lib, backend_dir, env, logf)
        except (SystemExit, AssertionError, OSError) as exc:
            result["sets"][name] = {"failed": str(exc)}
            log("%s: FAILED %s" % (name, exc))
            continue
        result["sets"][name] = {"cpu": mode, "system_info": info, "markers": marks,
                                "files": manifest(os.path.join(out, name))}
        log("%s: %d files, markers %s" % (name, len(result["sets"][name]["files"]), marks))
    with open(os.path.join(out, "engine.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    shutil.rmtree(src, ignore_errors=True)                       # cleanup: the build tree is a byproduct
    failed = [n for n, s in result["sets"].items() if "failed" in s]
    log("done: %s%s" % (out, (" - FAILED sets: %s" % failed) if failed else ""))
    if failed:
        raise SystemExit("set(s) failed: %s - see build.log / engine.json" % failed)


if __name__ == "__main__":
    main(sys.argv[1:3])                                          # the runner appends the order name: ignore it
