#!/usr/bin/env bash
# Engine patch #0 on the tablet head's CUDA 11.4 build: rebuild with slot_pending_token_patch.py (the slot save reports
# the sampled token no cache holds yet). Incremental: only the server's sources change. Same shape as
# build_cuda_graphs.sh: refuses while a CUDA llama process runs; the mounted build-cuda-114/bin is archived first and
# the patched sources are backed up; the marker is counted in the built binary.
#
#   bash build_slot_pending.sh [source tree]     (default: the tree /llama/cuda is mounted from)
#
# The default is the tree the /llama/cuda mount comes from (~/llama.cpp-latest). Read `docker inspect tablet-foundry`
# Mounts, not a doc, before naming another: a build of a tree nothing mounts leaves the serving engine unpatched.
set -euo pipefail
BUILD_HOME="${BUILD_HOME:-$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)}"   # the home of whoever ran it (run_remote runs it through sudo, so $HOME is root's)
OUT=$BUILD_HOME/llama-build-rpcasync
SRC=${1:-$BUILD_HOME/llama.cpp-latest}
IMG=nvidia/cuda:11.4.3-devel-ubuntu20.04
STAMP=$(date +%Y%m%d-%H%M%S)
exec > "$OUT/build-slotpending-$STAMP.out" 2>&1
if pgrep -f "[l]lama/cuda/llama-server|[l]lama-cuda-114/bin/llama" >/dev/null; then
  echo "REFUSED: a CUDA llama process is running"; echo "BUILD-SLOTPENDING FAILED"; exit 1
fi
for f in server-context.cpp server-task.h server-task.cpp; do
  cp -n "$SRC/tools/server/$f" "$OUT/$f.pre-slotpending-$STAMP"
done
python3 "$OUT/slot_pending_token_patch.py" "$SRC"
grep -c "pending_token" "$SRC/tools/server/server-context.cpp" "$SRC/tools/server/server-task.h" "$SRC/tools/server/server-task.cpp"
ARCH="$BUILD_HOME/llama-cuda114-bin-archive-$(basename "$SRC")-$STAMP"
cp -a "$SRC/build-cuda-114/bin" "$ARCH"
echo "source $SRC ($(git -C "$SRC" log -1 --format=%h)); archived bin -> $ARCH"
docker run --rm -v "$SRC":/src -w /src "$IMG" bash -c '
  set -euo pipefail
  apt-get update -qq && apt-get install -y -qq python3-pip > /dev/null && pip3 install -q "cmake>=3.24,<4"
  export PATH=/usr/local/bin:$PATH
  nice -n 10 cmake --build build-cuda-114 -j 8 > build-cuda-114.make-slotpending.log 2>&1 || { tail -40 build-cuda-114.make-slotpending.log; exit 1; }
  chown -R 1000:1000 build-cuda-114 build-cuda-114.make-slotpending.log
  # the server code lives in libllama-server-impl.so (llama-server is an 18 KB stub at 23b0202), so the marker is
  # counted there: counting the stub reads 0 over a good build. libllama.so already holds an unrelated
  # "pending_token_idx", so it is no marker.
  grep -a -c pending_token build-cuda-114/bin/libllama-server-impl.so
' || { echo "BUILD-SLOTPENDING FAILED"; exit 1; }
echo "BUILD-SLOTPENDING DONE $STAMP"
