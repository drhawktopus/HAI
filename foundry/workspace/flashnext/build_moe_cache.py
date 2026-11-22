r"""Build llama.cpp PR #27861 (Vulkan GPU MoE cache) as a compute order.

Constraint: The build must run in a compute order to reserve CPU cores; running it concurrently with active measurements will corrupt the data.

Usage: python build_moe_cache.py <ORDER_OUT> [order name]

Pre-installs cmake and ninja. Requires system-level LunarG Vulkan SDK and VS 2022 Build Tools.
Output: C:\ProgramData\HAI\tools\llama-moecache-<sha>\ and <ORDER_OUT>\build.log.
Idempotent: fetches if a clone exists."""
import glob, os, shutil, subprocess, sys, time

OUT = sys.argv[1]
SRC = r"D:\Models\llama.cpp\src-moe-cache"
PR = 27861
VCVARS = r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
TOOLS = r"C:\ProgramData\HAI\tools"
os.makedirs(OUT, exist_ok=True)
log = open(os.path.join(OUT, "build.log"), "a", encoding="utf-8")


def say(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    log.write(line + "\n")
    log.flush()


def run(argv, cwd=None, env=None, shell=False):
    say("$ " + (argv if shell else " ".join(argv)))
    try:
        p = subprocess.Popen(argv, cwd=cwd, env=env, shell=shell, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace")
    except OSError as e:                       # a launch failure must still leave a verdict line for the chain
        say("FAILED to launch: %s" % e)
        sys.exit(2)
    tail = []
    for l in p.stdout:
        log.write(l)
        tail.append(l.rstrip())
        if len(tail) > 30:
            tail.pop(0)
    p.wait()
    log.flush()
    if p.returncode != 0:
        say("FAILED rc=%s; last lines:\n  " % p.returncode + "\n  ".join(tail))
        sys.exit(p.returncode)
    return tail


sdk = os.environ.get("VULKAN_SDK") or (sorted(glob.glob(r"C:\VulkanSDK\*")) or [None])[-1]
assert sdk and os.path.exists(os.path.join(sdk, "Bin", "glslc.exe")), "Vulkan SDK not found (install it first): %r" % sdk
say("Vulkan SDK: " + sdk)
run([sys.executable, "-m", "pip", "install", "--quiet", "--user", "cmake", "ninja"])
scripts = subprocess.run([sys.executable, "-c", "import sysconfig;print(sysconfig.get_path('scripts', 'nt_user'))"],
                         capture_output=True, text=True).stdout.strip()
env = dict(os.environ, VULKAN_SDK=sdk, PATH=";".join([scripts, os.path.join(sdk, "Bin"), os.environ.get("PATH", "")]))
# Windows resolves a child's executable through the PARENT's PATH, not the env passed to it (01:07: FileNotFoundError
# on a bare "cmake"): resolve the pip-installed tools to absolute paths.
cmake = shutil.which("cmake", path=env["PATH"])
ninja = shutil.which("ninja", path=env["PATH"])
assert cmake and ninja, "cmake/ninja not found on %s" % env["PATH"][:200]
say("cmake = %s | ninja = %s" % (cmake, ninja))
run([cmake, "--version"], env=env)

if not os.path.isdir(os.path.join(SRC, ".git")):
    os.makedirs(os.path.dirname(SRC), exist_ok=True)
    run(["git", "clone", "--filter=blob:none", "https://github.com/ggml-org/llama.cpp", SRC])
run(["git", "fetch", "origin", "pull/%d/head" % PR], cwd=SRC)           # into FETCH_HEAD: reruns with moe-cache
run(["git", "checkout", "-f", "-B", "moe-cache", "FETCH_HEAD"], cwd=SRC)  # checked out would refuse a ':branch' fetch
sha = subprocess.run(["git", "rev-parse", "--short=9", "HEAD"], cwd=SRC, capture_output=True, text=True).stdout.strip()
say("PR head: " + sha)
dst = os.path.join(TOOLS, "llama-moecache-" + sha)

# MSVC + Ninja + Vulkan: one cmd that sources vcvars64 then configures and builds (16 cores).
# The PR's ggml-cpu.c uses POSIX flockfile/funlockfile (LNK2019 under MSVC, 01:25); MSVC's CRT spells them
# _lock_file/_unlock_file - the review thread's workaround, applied as defines rather than a source patch.
msvc_shim = "/Dflockfile=_lock_file /Dfunlockfile=_unlock_file"
cfg = ('call "%s" && "%s" -S "%s" -B "%s\\build-vk" -G Ninja -DCMAKE_MAKE_PROGRAM="%s" -DCMAKE_BUILD_TYPE=Release '
       '-DCMAKE_C_FLAGS="%s" -DCMAKE_CXX_FLAGS="%s" '
       '-DGGML_VULKAN=ON -DGGML_NATIVE=ON -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF '
       '-DLLAMA_BUILD_SERVER=ON && "%s" --build "%s\\build-vk" --config Release --target llama-server -j 16'
       ) % (VCVARS, cmake, SRC, SRC, ninja, msvc_shim, msvc_shim, cmake, SRC)
run(cfg, cwd=SRC, env=env, shell=True)

bindir = os.path.join(SRC, "build-vk", "bin")
exe = os.path.join(bindir, "llama-server.exe")
assert os.path.exists(exe), "llama-server.exe not produced"
os.makedirs(dst, exist_ok=True)
for f in os.listdir(bindir):
    if f.lower().endswith((".exe", ".dll")):
        shutil.copy2(os.path.join(bindir, f), dst)
say("installed -> " + dst + ": " + ", ".join(sorted(os.listdir(dst))))
devs = subprocess.run([os.path.join(dst, "llama-server.exe"), "--list-devices"], capture_output=True, text=True, cwd=dst).stdout
say("devices: " + " | ".join(l.strip() for l in devs.splitlines() if l.strip()))
assert "Vulkan0" in devs, "the built server sees no Vulkan device"
h = subprocess.run([os.path.join(dst, "llama-server.exe"), "--help"], capture_output=True, text=True, cwd=dst).stdout
flags = [l.strip() for l in h.splitlines() if "moe-expert-cache" in l]
say("cache flags: " + (" || ".join(flags) if flags else "NONE FOUND"))
assert flags, "built server has no --moe-expert-cache flag - wrong branch?"
probe = subprocess.run([os.path.join(dst, "llama-server.exe"), "-m", "nonexistent.gguf", "-v", "--port", "8097"],
                       capture_output=True, text=True, cwd=dst)
si = next((l.strip() for l in (probe.stdout + probe.stderr).splitlines() if "system_info" in l), "")
say("system_info: " + si[si.find("CPU :"):][:120])
assert "AVX2 = 1" in si, "CPU backend built without AVX2"
say("DONE " + dst)
