#!/usr/bin/env bash
# Split inference: llama.cpp with async RPC (upstream PR #18626, d0132a6) for pipelined reading across the tablet
# head's CPU, its GT 730 and the tablets. Builds in a SEPARATE worktree; the live ~/llama.cpp builds Tolkien mounts are untouched.
set -euo pipefail
BUILD_HOME="${BUILD_HOME:-$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)}"   # the home of whoever ran it (run_remote runs it through sudo, so $HOME is root's)
OUT=$BUILD_HOME/llama-build-rpcasync
WT=$BUILD_HOME/llama.cpp-rpcasync
NDK=$BUILD_HOME/android-ndk-r27c
mkdir -p "$OUT"
cd $BUILD_HOME/llama.cpp
git fetch --quiet origin master
COMMIT=$(git rev-parse origin/master)
if [ ! -d "$WT" ]; then git worktree add --detach "$WT" "$COMMIT"; fi
cd "$WT"
git checkout --quiet --detach "$COMMIT"
git log -1 --format='%H %ci %s' > "$OUT/commit.txt"
echo "commit: $(cat "$OUT/commit.txt")"
grep -q "rpc_dispatcher" ggml/src/ggml-rpc/ggml-rpc.cpp || { echo "ABORT: rpc_dispatcher (PR #18626) not in this source"; exit 2; }
grep -n "/\* .async *= \*/\|/\* .events *= \*/" ggml/src/ggml-rpc/ggml-rpc.cpp | tee "$OUT/rpc_caps.txt"

COMMON="-DCMAKE_BUILD_TYPE=Release -DGGML_OPENMP=ON -DGGML_RPC=ON -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF"
echo "== x86 CPU + RPC"
cmake -S . -B build-x86 $COMMON -DBUILD_SHARED_LIBS=ON -DGGML_NATIVE=ON > "$OUT/cmake-x86.log" 2>&1
cmake --build build-x86 -j 8 > "$OUT/make-x86.log" 2>&1
echo "== Vulkan + RPC"
cmake -S . -B build-vulkan $COMMON -DBUILD_SHARED_LIBS=ON -DGGML_NATIVE=ON -DGGML_VULKAN=ON > "$OUT/cmake-vulkan.log" 2>&1
cmake --build build-vulkan -j 8 > "$OUT/make-vulkan.log" 2>&1
echo "== android arm64 + RPC (static, as the 08-22 build)"
cmake -S . -B build-android $COMMON -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" -DANDROID_ABI=arm64-v8a \
  -DANDROID_PLATFORM=android-24 > "$OUT/cmake-android.log" 2>&1
cmake --build build-android -j 8 > "$OUT/make-android.log" 2>&1
for d in build-x86 build-vulkan build-android; do
  echo "== $d/bin: $(ls "$d/bin" | grep -E 'rpc|llama-bench$|llama-server$' | tr '\n' ' ')"
done
echo DONE
