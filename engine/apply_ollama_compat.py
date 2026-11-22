"""Put ollama's llama.cpp COMPAT LAYER into a lab-engine tree.

    python apply_ollama_compat.py <llama.cpp tree> <ollama src>/llama/compat

ollama builds llama-server from upstream llama.cpp at its pin (0.40.5: b11669 = 620d2225c) plus llama/compat: one hook
patch (call sites in src/llama-model-loader.cpp and tools/mtmd/clip.cpp) plus compat sources linked into the `llama`
target, which translate ollama's published GGUFs in memory at load time. Without this layer llama-server cannot read
ollama-format blobs (`key not found in model: qwen3vl.rope.dimension_sections`). Same steps as ollama's own
llama/server/CMakeLists.txt:
  1. the hook patch, applied with `git apply` exactly as cmake/apply-git-patches.cmake does (skipped when it already
     reverse-applies, so re-running is safe);
  2. the compat sources copied to <tree>/ollama-compat/ (ollama keeps them out of _deps; a lab tree is shipped whole);
  3. a marked block at the end of the root CMakeLists.txt: target_sources(llama) + include dirs for llama and mtmd.
Prints what it did and exits non-zero on any failure."""
import glob
import os
import shutil
import subprocess
import sys

MARK = "# OLLAMA-COMPAT (lab engine): ollama's llama/compat layer"
BLOCK = MARK + """ - apply_ollama_compat.py; mirrors ollama 0.40.5 llama/server/CMakeLists.txt
if(TARGET llama)
    file(GLOB _ollama_compat_sources CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/ollama-compat/*.cpp)
    target_sources(llama PRIVATE ${_ollama_compat_sources})
    target_include_directories(llama PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/ollama-compat)
endif()
if(TARGET mtmd)
    target_include_directories(mtmd PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/ollama-compat)
endif()
"""


def git_apply(tree, patch, *flags):
    r = subprocess.run(["git", "apply", "--whitespace=nowarn"] + list(flags) + [patch], cwd=tree,
                       capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def main(tree, compat):
    tree, compat = os.path.abspath(tree), os.path.abspath(compat)     # git apply runs with cwd=tree
    patches = sorted(glob.glob(os.path.join(compat, "*.patch")))
    sources = sorted(glob.glob(os.path.join(compat, "*.cpp")) + glob.glob(os.path.join(compat, "*.h")))
    assert patches and len(sources) >= 4, "no compat layer at %s (patches %d, sources %d)" % (
        compat, len(patches), len(sources))
    for p in patches:
        # a tree copied from a Windows checkout has CRLF files and no .git, and there git apply does no line-ending
        # normalisation: the second form treats the CR as whitespace
        for flags in ((), ("--ignore-whitespace",)):
            rc, _ = git_apply(tree, p, "--reverse", "--check", *flags)
            if rc == 0:
                print("patch %s: already applied%s" % (os.path.basename(p), " (CRLF tree)" if flags else ""))
                break
            rc, out = git_apply(tree, p, *flags)
            if rc == 0:
                print("patch %s: applied%s" % (os.path.basename(p), " (CRLF tree, --ignore-whitespace)" if flags else ""))
                break
        else:
            raise SystemExit("patch %s FAILED: %s" % (os.path.basename(p), out[-600:]))
    dst = os.path.join(tree, "ollama-compat")
    os.makedirs(dst, exist_ok=True)
    for s in sources:
        shutil.copy2(s, dst)
    print("sources: %d -> %s" % (len(sources), dst))
    cm = os.path.join(tree, "CMakeLists.txt")
    with open(cm, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    if MARK in text:
        print("CMakeLists.txt: block already present")
    else:
        nl = "\r\n" if "\r\n" in text else "\n"
        with open(cm, "a", encoding="utf-8", newline="") as f:
            f.write(nl + BLOCK.replace("\n", nl))
        print("CMakeLists.txt: block appended")
    hooks = sum(open(os.path.join(tree, f), encoding="utf-8", errors="replace").read().count("llama_ollama_compat::")
                for f in ("src/llama-model-loader.cpp", "tools/mtmd/clip.cpp"))
    assert hooks > 0, "the hook patch left no llama_ollama_compat:: call sites"
    print("call sites: %d llama_ollama_compat:: references in the loader + clip" % hooks)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
