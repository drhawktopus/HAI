r"""Build the "mix": upstream llama.cpp b11160 + Unsloth MTP (unslothai/llama.cpp#144) + expert-cache (ggml-org/llama.cpp#27861), Vulkan backend.

Toolchain: Vulkan SDK, pip cmake/ninja, VS 2022 Build Tools (see build_moe_cache.py for the MSVC flockfile shim).

Usage:
    python build_mix.py <ORDER_OUT> [order name]

Output: C:\\ProgramData\\HAI\\tools\\llama-mix-<sha>\\ (llama-server.exe + ggml-vulkan.dll), <ORDER_OUT>\\build.log.

Patching:
`--patch <file.py>` (repeatable) applies lab patches to the merged source before build. Each patched build gets a unique SHA/folder.

Repack Constraint:
Unpatched builds MUST use `--no-repack`; the cache expects raw experts. Using `patches\\moecache_unrepack.py` de-interleaves repacked IQ4_NL experts, allowing `--repack` to remain enabled."""
import glob, os, shutil, subprocess, sys, time

OUT = sys.argv[1]
PATCHES = [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--patch" and i + 1 < len(sys.argv)]
SRC = r"D:\Models\llama.cpp\src-mix"
BASE_TAG = "b11160"
MTP_PR = ("https://github.com/unslothai/llama.cpp", 144, "ca1426903fabe9af26cd1")   # Unsloth's release notes: MTP for Qwen3.8-Flash-Next
CACHE_PR = ("https://github.com/ggml-org/llama.cpp", 27861, None)
VCVARS = r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
TOOLS = r"C:\ProgramData\HAI\tools"
os.makedirs(OUT, exist_ok=True)
log = open(os.path.join(OUT, "build.log"), "a", encoding="utf-8")


def say(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    log.write(line + "\n")
    log.flush()


def run(argv, cwd=None, env=None, shell=False, ok_rc=(0,)):
    say("$ " + (argv if shell else " ".join(argv)))
    try:
        p = subprocess.Popen(argv, cwd=cwd, env=env, shell=shell, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace")
    except OSError as e:
        say("FAILED to launch: %s" % e)
        sys.exit(2)
    tail = []
    for l in p.stdout:
        log.write(l)
        tail.append(l.rstrip())
        if len(tail) > 40:
            tail.pop(0)
    p.wait()
    log.flush()
    if p.returncode not in ok_rc:
        if not shell and argv[:2] == ["git", "merge"] or (not shell and "merge" in argv):
            c = subprocess.run(["git", "diff", "--name-only", "--diff-filter=U"], cwd=cwd, capture_output=True, text=True).stdout
            say("CONFLICT files: " + (c.strip().replace("\n", ", ") or "(none listed)"))
        say("FAILED rc=%s; last lines:\n  " % p.returncode + "\n  ".join(tail))
        sys.exit(p.returncode or 1)
    return tail


sdk = os.environ.get("VULKAN_SDK") or (sorted(glob.glob(r"C:\VulkanSDK\*")) or [None])[-1]
assert sdk and os.path.exists(os.path.join(sdk, "Bin", "glslc.exe")), "Vulkan SDK not found: %r" % sdk
scripts = subprocess.run([sys.executable, "-c", "import sysconfig;print(sysconfig.get_path('scripts', 'nt_user'))"],
                         capture_output=True, text=True).stdout.strip()
env = dict(os.environ, VULKAN_SDK=sdk, PATH=";".join([scripts, os.path.join(sdk, "Bin"), os.environ.get("PATH", "")]))
cmake = shutil.which("cmake", path=env["PATH"])
ninja = shutil.which("ninja", path=env["PATH"])
assert cmake and ninja, "cmake/ninja not on %s" % env["PATH"][:200]
say("Vulkan SDK %s | cmake %s | ninja %s" % (sdk, cmake, ninja))

if not os.path.isdir(os.path.join(SRC, ".git")):
    os.makedirs(os.path.dirname(SRC), exist_ok=True)
    run(["git", "clone", "--filter=blob:none", CACHE_PR[0], SRC])
run(["git", "-c", "user.email=lab@softserve", "-c", "user.name=lab", "fetch", "origin", "tag", BASE_TAG, "--no-tags"], cwd=SRC, ok_rc=(0, 128))
run(["git", "fetch", "origin", "refs/tags/%s:refs/tags/%s" % (BASE_TAG, BASE_TAG)], cwd=SRC, ok_rc=(0, 128))
run(["git", "checkout", "-f", "-B", "mix", BASE_TAG], cwd=SRC)
# Both PRs add source files to src/CMakeLists.txt (09:02: CONFLICT there, nowhere else). A source list is the one
# place where "take both sides" is the right merge; the union driver does exactly that, scoped to that file.
with open(os.path.join(SRC, ".git", "info", "attributes"), "w", encoding="utf-8") as f:
    f.write("src/CMakeLists.txt merge=union\n")
run(["git", "merge", "--abort"], cwd=SRC, ok_rc=(0, 128))                 # clear a half-merge left by a failed run
# MTP PR from Unsloth's fork at the commit their release notes pin
run(["git", "fetch", MTP_PR[0], "pull/%d/head" % MTP_PR[1]], cwd=SRC)
mtp_sha = MTP_PR[2] or "FETCH_HEAD"
run(["git", "-c", "user.email=lab@softserve", "-c", "user.name=lab", "merge", "--no-edit", "-m", "mix: MTP (unsloth #144)", mtp_sha],
    cwd=SRC)
# expert-cache PR from upstream
run(["git", "fetch", CACHE_PR[0], "pull/%d/head" % CACHE_PR[1]], cwd=SRC)
run(["git", "-c", "user.email=lab@softserve", "-c", "user.name=lab", "merge", "--no-edit", "-m", "mix: expert cache (ggml-org #27861)", "FETCH_HEAD"],
    cwd=SRC)
for p in PATCHES:                                                           # our own patches, each its own commit
    run([sys.executable, p, SRC], cwd=SRC)
    run(["git", "-c", "user.email=lab@softserve", "-c", "user.name=lab", "commit", "-am", "lab patch: " + os.path.basename(p)],
        cwd=SRC, ok_rc=(0, 1))
sha = subprocess.run(["git", "rev-parse", "--short=9", "HEAD"], cwd=SRC, capture_output=True, text=True).stdout.strip()
say("mix head: " + sha + (" (patches: %s)" % ", ".join(os.path.basename(p) for p in PATCHES) if PATCHES else ""))
dst = os.path.join(TOOLS, "llama-mix-" + sha)

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
say("installed -> " + dst)
devs = subprocess.run([os.path.join(dst, "llama-server.exe"), "--list-devices"], capture_output=True, text=True, cwd=dst).stdout
say("devices: " + " | ".join(l.strip() for l in devs.splitlines() if l.strip()))
assert "Vulkan0" in devs, "no Vulkan device"
h = subprocess.run([os.path.join(dst, "llama-server.exe"), "--help"], capture_output=True, text=True, cwd=dst).stdout
has_cache = "moe-expert-cache" in h
has_mtp = "draft-mtp" in h or "spec-type" in h
say("flags: expert cache=%s | MTP=%s" % (has_cache, has_mtp))
assert has_cache and has_mtp, "the mix is missing a feature"
probe = subprocess.run([os.path.join(dst, "llama-server.exe"), "-m", "nonexistent.gguf", "-v", "--port", "8097"],
                       capture_output=True, text=True, cwd=dst)
si = next((l.strip() for l in (probe.stdout + probe.stderr).splitlines() if "system_info" in l), "")
say("system_info: " + si[si.find("CPU :"):][:120])
assert "AVX2 = 1" in si, "CPU backend built without AVX2"
say("DONE " + dst)
