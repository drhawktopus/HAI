#!/usr/bin/env bash
# #36: the next build again with the lab's scheduler profiler (GGML_SCHED_PROFILE=N; OFF unless set) to split rigby's
# ~50 ms/token of GPU / dispatch / hand-off time. Same commit, same lab RPC patches, the mounted build-vulkan's exact
# cmake options. Separate build dir: the mounted build-vulkan is untouched until its binaries are copied in between orders.
set -euo pipefail
BUILD_HOME="${BUILD_HOME:-$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)}"   # the home of whoever ran it (run_remote runs it through sudo, so $HOME is root's)
OUT=$BUILD_HOME/llama-build-rpcasync
SRC=$BUILD_HOME/llama.cpp-rpcasync
STAMP=$(date +%Y%m%d-%H%M%S)
LOG="$OUT/make-vulkan-prof.log"
exec > "$OUT/build-prof-$STAMP.out" 2>&1
cd "$SRC"
git log -1 --format='%H %ci %s' | tee "$OUT/commit-prof.txt"
cp -n ggml/src/ggml-backend.cpp "$OUT/ggml-backend.cpp.pre-schedprof-$STAMP"
python3 "$OUT/sched_profile_23b0202.py" "$SRC"
grep -c "lab patch: scheduler profile" ggml/src/ggml-backend.cpp
COMMON="-DCMAKE_BUILD_TYPE=Release -DGGML_OPENMP=ON -DGGML_RPC=ON -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DBUILD_SHARED_LIBS=ON -DGGML_NATIVE=ON -DGGML_SCHED_MAX_COPIES=4"
cmake -S . -B build-vulkan-prof $COMMON -DGGML_VULKAN=ON > "$OUT/cmake-vulkan-prof.log" 2>&1
nice -n 10 cmake --build build-vulkan-prof -j 8 > "$LOG" 2>&1
ls -la build-vulkan-prof/bin/llama-server build-vulkan-prof/bin/llama-bench
grep -h -E "^(GGML_VULKAN|GGML_RPC|GGML_SCHED_MAX_COPIES|CMAKE_BUILD_TYPE|GGML_NATIVE|BUILD_SHARED_LIBS)[:=]" \
  build-vulkan-prof/CMakeCache.txt
echo "BUILD-PROF DONE $STAMP"
