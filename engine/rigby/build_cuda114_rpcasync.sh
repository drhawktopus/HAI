#!/usr/bin/env bash
# #36: a CUDA build of the next engine for rigby's GTX 1060 (Pascal, sm_61). rigby runs driver 470 (the GT 730 needs
# it) = CUDA 11.4 at most, and CUDA 11.4 wants gcc <= 10, so the build runs in NVIDIA's 11.4.3 devel image
# (Ubuntu 20.04, gcc 9). Same source as /llama/next (23b0202 + the lab RPC patches + the scheduler profiler, OFF unless
# GGML_SCHED_PROFILE is set). Separate build dir; nothing live is touched. The CUDA runtime libraries are copied beside
# the binaries so they run under the host loader with the host's libcuda (driver 470).
set -euo pipefail
BUILD_HOME="${BUILD_HOME:-$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)}"   # the home of whoever ran it (run_remote runs it through sudo, so $HOME is root's)
OUT=$BUILD_HOME/llama-build-rpcasync
SRC=$BUILD_HOME/llama.cpp-rpcasync
IMG=nvidia/cuda:11.4.3-devel-ubuntu20.04
STAMP=$(date +%Y%m%d-%H%M%S)
exec > "$OUT/build-cuda114-$STAMP.out" 2>&1
echo "== pull $IMG"
docker pull "$IMG"
echo "== build"
docker run --rm -v "$SRC":/src -w /src -e DEBIAN_FRONTEND=noninteractive "$IMG" bash -c '
  set -euo pipefail
  apt-get update -qq
  apt-get install -y -qq python3-pip git libgomp1 > /dev/null
  pip3 install -q "cmake>=3.24,<4"
  export PATH=/usr/local/bin:$PATH
  cmake --version | head -1
  nvcc --version | tail -2
  cmake -S . -B build-cuda-114 -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=61 \
        -DGGML_OPENMP=ON -DGGML_RPC=ON -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DBUILD_SHARED_LIBS=ON \
        -DGGML_NATIVE=ON -DGGML_SCHED_MAX_COPIES=4 > build-cuda-114.cmake.log 2>&1 || { tail -40 build-cuda-114.cmake.log; exit 1; }
  nice -n 10 cmake --build build-cuda-114 -j 8 > build-cuda-114.make.log 2>&1 || { tail -60 build-cuda-114.make.log; exit 1; }
  for l in libcudart.so.11.0 libcublas.so.11 libcublasLt.so.11; do
    cp -L /usr/local/cuda/lib64/$l build-cuda-114/bin/
  done
  cp -L /usr/lib/x86_64-linux-gnu/libgomp.so.1 build-cuda-114/bin/ || true
  chown -R 1000:1000 build-cuda-114 build-cuda-114.*.log
  ls -la build-cuda-114/bin/llama-server build-cuda-114/bin/llama-bench build-cuda-114/bin/libggml-cuda.so
'
echo "BUILD-CUDA114 DONE $STAMP"
