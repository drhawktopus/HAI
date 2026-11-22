#!/usr/bin/env bash
# #36: apply the lab block-split patch (hotstore_block_patch.py, env-gated - unset = the PR unchanged) to the hotstore
# tree and rebuild incrementally. The mounted bin is archived first; refuses while a hotstore llama process runs.
set -euo pipefail
BUILD_HOME="${BUILD_HOME:-$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)}"   # the home of whoever ran it (run_remote runs it through sudo, so $HOME is root's)
OUT=$BUILD_HOME/llama-build-rpcasync
SRC=$BUILD_HOME/llama.cpp-hotstore
IMG=nvidia/cuda:11.4.3-devel-ubuntu20.04
STAMP=$(date +%Y%m%d-%H%M%S)
exec > "$OUT/build-hotstore-block-$STAMP.out" 2>&1
if pgrep -f "[l]lama/hotstore/llama|[l]lama.cpp-hotstore/build-cuda-114/bin/llama" >/dev/null; then echo "REFUSED: a hotstore llama process is running"; echo "BUILD-HOTSTORE-BLOCK FAILED"; exit 1; fi
cp -a "$SRC/build-cuda-114/bin" "$BUILD_HOME/llama-hotstore-bin-archive-$STAMP"
echo "archived bin -> $BUILD_HOME/llama-hotstore-bin-archive-$STAMP"
python3 "$OUT/hotstore_block_patch.py" "$SRC"
git -C "$SRC" diff --stat
docker run --rm -v "$SRC":/src -w /src "$IMG" bash -c '
  set -euo pipefail
  apt-get update -qq && apt-get install -y -qq python3-pip > /dev/null && pip3 install -q "cmake>=3.24,<4"
  export PATH=/usr/local/bin:$PATH
  nice -n 10 cmake --build build-cuda-114 -j 8 > build-cuda-114.make-block.log 2>&1 || { tail -40 build-cuda-114.make-block.log; exit 1; }
  chown -R 1000:1000 build-cuda-114 build-cuda-114.make-block.log
  grep -a -c "STATIC fill from" build-cuda-114/bin/libllama.so*
'
echo "BUILD-HOTSTORE-BLOCK DONE $STAMP"
