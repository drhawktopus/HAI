#!/bin/bash
# The tablet head's engine build for ANY lab tree (the engine pipeline's last step):
#     build_cuda114.sh <short commit>:<engine folder>      (one argument: run_remote's --args takes no spaces)
# The engine folder is the tablet head's TABLET_HEAD_ENGINE_TREE; the pipeline copies the patched tree
# llama.cpp-<short> and patches/cuda_graphs_pre_volta_patch.py into it first. Recipe: NVIDIA's CUDA 11.4.3 image
# (driver 470 = CUDA 11.4), sm_61 (the GTX 1060), and the pre-Volta CUDA-graphs patch the tablet head's engine carries.
# Nothing live is touched: fleet_deploy --engine --only <tablet head> swaps the /llama/cuda mount from build-cuda-114/bin
# afterwards. Low priority, 4 jobs. Prints DONE last.
set -euo pipefail
ARG="${1:?usage: build_cuda114.sh <short commit>:<engine folder>}"
SHORT="${ARG%%:*}"
TREE="${ARG#*:}"
[ -n "$SHORT" ] && [ "$TREE" != "$ARG" ] && [ -n "$TREE" ] || { echo "ABORT: usage build_cuda114.sh <short commit>:<engine folder>"; echo DONE; exit 1; }
SRC="$TREE/llama.cpp-$SHORT"
PRE_VOLTA="$TREE/patches/cuda_graphs_pre_volta_patch.py"
IMG=nvidia/cuda:11.4.3-devel-ubuntu20.04
STAMP=$(date +%Y%m%d-%H%M%S)
LOG="$TREE/build-cuda114-$SHORT-$STAMP.out"
[ -d "$SRC" ] || { echo "ABORT: no tree at $SRC"; echo DONE; exit 1; }
[ -f "$PRE_VOLTA" ] || { echo "ABORT: no pre-Volta patch at $PRE_VOLTA"; echo DONE; exit 1; }
OWNER=$(stat -c %u:%g "$SRC")   # the build output belongs to whoever owns the tree (the build itself runs as root)
{
echo "== source $SRC (llama.cpp $SHORT + lab patches #0/#3/#2 + ollama compat)"
echo "lab_resume refs: $(grep -c "lab_resume" "$SRC/tools/server/server-context.cpp")"
python3 "$PRE_VOLTA" "$SRC"
echo "pre-volta marker: $(grep -c "LAB_CUDA_GRAPHS_PRE_VOLTA" "$SRC/ggml/src/ggml-cuda/ggml-cuda.cu")"
docker run --rm -v "$SRC":/src -w /src -e DEBIAN_FRONTEND=noninteractive -e OWNER="$OWNER" "$IMG" bash -c '
  set -euo pipefail
  apt-get update -qq
  apt-get install -y -qq python3-pip git libgomp1 > /dev/null
  pip3 install -q "cmake>=3.24,<4"
  export PATH=/usr/local/bin:$PATH
  cmake -S . -B build-cuda-114 -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=61 \
        -DGGML_OPENMP=ON -DGGML_RPC=ON -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DBUILD_SHARED_LIBS=ON \
        -DGGML_NATIVE=ON -DGGML_SCHED_MAX_COPIES=4 > build-cuda-114.cmake.log 2>&1 || { tail -40 build-cuda-114.cmake.log; exit 1; }
  nice -n 19 cmake --build build-cuda-114 -j 4 > build-cuda-114.make.log 2>&1 || { tail -60 build-cuda-114.make.log; exit 1; }
  for l in libcudart.so.11.0 libcublas.so.11 libcublasLt.so.11; do
    cp -L /usr/local/cuda/lib64/$l build-cuda-114/bin/
  done
  cp -L /usr/lib/x86_64-linux-gnu/libnccl.so.2 build-cuda-114/bin/ 2>/dev/null || echo "no libnccl in image"
  cp -L /usr/lib/x86_64-linux-gnu/libgomp.so.1 build-cuda-114/bin/ || true
  chown -R "$OWNER" build-cuda-114 build-cuda-114.*.log
  ls -la build-cuda-114/bin/llama-server build-cuda-114/bin/libllama-server-impl.so build-cuda-114/bin/libggml-cuda.so
  echo "lab markers: $(grep -a -c -E "lab_resume|lab_rng|lab_get_carry" build-cuda-114/bin/libllama-server-impl.so)"
  echo "ollama compat symbols: $(grep -a -c llama_ollama_compat build-cuda-114/bin/libllama.so)"
'
echo "BUILD-CUDA114-$SHORT OK $STAMP"
} > "$LOG" 2>&1 || { echo "BUILD FAILED - see $LOG"; tail -n 20 "$LOG"; echo DONE; exit 1; }
echo "BUILD_OK=$SRC/build-cuda-114/bin"
echo "LOG=$LOG"
echo DONE
