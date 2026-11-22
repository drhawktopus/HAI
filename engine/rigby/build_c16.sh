#!/usr/bin/env bash
# #36: the async-RPC build again with 16 chunks in flight (GGML_SCHED_MAX_COPIES, default 4) so a pipeline with more
# than 4 stages can keep every stage busy. Separate build dirs: the mounted build-x86 / build-vulkan are untouched
# until the binaries are copied in between orders.
set -euo pipefail
BUILD_HOME="${BUILD_HOME:-$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)}"   # the home of whoever ran it (run_remote runs it through sudo, so $HOME is root's)
OUT=$BUILD_HOME/llama-build-rpcasync
cd $BUILD_HOME/llama.cpp-rpcasync
git log -1 --format='%H %ci %s' | tee "$OUT/commit-c16.txt"
grep -n "GGML_SCHED_MAX_COPIES" ggml/CMakeLists.txt | head -3 | tee "$OUT/c16-option.txt"
COMMON="-DCMAKE_BUILD_TYPE=Release -DGGML_OPENMP=ON -DGGML_RPC=ON -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DBUILD_SHARED_LIBS=ON -DGGML_NATIVE=ON -DGGML_SCHED_MAX_COPIES=16"
echo "== x86 c16"
cmake -S . -B build-x86-c16 $COMMON > "$OUT/cmake-x86-c16.log" 2>&1
cmake --build build-x86-c16 -j 8 > "$OUT/make-x86-c16.log" 2>&1
echo "== vulkan c16"
cmake -S . -B build-vulkan-c16 $COMMON -DGGML_VULKAN=ON > "$OUT/cmake-vulkan-c16.log" 2>&1
cmake --build build-vulkan-c16 -j 8 > "$OUT/make-vulkan-c16.log" 2>&1
grep -h "GGML_SCHED_MAX_COPIES" build-x86-c16/CMakeCache.txt build-vulkan-c16/CMakeCache.txt
echo DONE
