#!/usr/bin/env bash
# #36: rebuild the CUDA 11.4 build with the env-gated pre-Volta CUDA-graphs patch (OFF unless LAB_CUDA_GRAPHS_PRE_VOLTA
# is set). Incremental: only ggml-cuda.cu changes. The mounted build-cuda-114/bin is archived first and replaced only
# after the build succeeds; Tolkien's lane must be idle (checked).
set -euo pipefail
BUILD_HOME="${BUILD_HOME:-$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)}"   # the home of whoever ran it (run_remote runs it through sudo, so $HOME is root's)
OUT=$BUILD_HOME/llama-build-rpcasync
SRC=$BUILD_HOME/llama.cpp-rpcasync
IMG=nvidia/cuda:11.4.3-devel-ubuntu20.04
STAMP=$(date +%Y%m%d-%H%M%S)
exec > "$OUT/build-cudagraphs-$STAMP.out" 2>&1
if pgrep -f "[l]lama/cuda/llama-server|[l]lama-cuda-114/bin/llama" >/dev/null; then echo "REFUSED: a CUDA llama process is running"; exit 1; fi
cp -n "$SRC/ggml/src/ggml-cuda/ggml-cuda.cu" "$OUT/ggml-cuda.cu.pre-graphs-$STAMP" || true
python3 "$OUT/cuda_graphs_pre_volta_patch.py" "$SRC"
grep -c "LAB_CUDA_GRAPHS_PRE_VOLTA" "$SRC/ggml/src/ggml-cuda/ggml-cuda.cu"
cp -a "$SRC/build-cuda-114/bin" "$BUILD_HOME/llama-cuda114-bin-archive-$STAMP"
echo "archived bin -> $BUILD_HOME/llama-cuda114-bin-archive-$STAMP"
docker run --rm -v "$SRC":/src -w /src "$IMG" bash -c '
  set -euo pipefail
  apt-get update -qq && apt-get install -y -qq python3-pip > /dev/null && pip3 install -q "cmake>=3.24,<4"
  export PATH=/usr/local/bin:$PATH
  nice -n 10 cmake --build build-cuda-114 -j 8 > build-cuda-114.make-graphs.log 2>&1 || { tail -40 build-cuda-114.make-graphs.log; exit 1; }
  chown -R 1000:1000 build-cuda-114 build-cuda-114.make-graphs.log
  grep -a -c LAB_CUDA_GRAPHS_PRE_VOLTA build-cuda-114/bin/libggml-cuda.so.0
'
echo "BUILD-CUDAGRAPHS DONE $STAMP"
