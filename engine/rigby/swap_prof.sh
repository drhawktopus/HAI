#!/usr/bin/env bash
# #36: put the scheduler-profiler build (profiler OFF unless GGML_SCHED_PROFILE is set) into /llama/next/vulkan,
# between orders, with the current binaries archived first. Refuses if any llama process is running.
set -euo pipefail
BUILD_HOME="${BUILD_HOME:-$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)}"   # the home of whoever ran it (run_remote runs it through sudo, so $HOME is root's)
SRC=$BUILD_HOME/llama.cpp-rpcasync
LIVE=$SRC/build-vulkan/bin
NEW=$SRC/build-vulkan-prof/bin
STAMP=$(date +%Y%m%d-%H%M%S)
ARCH=$BUILD_HOME/llama-next-vulkan-archive-$STAMP
if pgrep -f "[l]lama-server|[l]lama-bench|[l]lama-perplexity" >/dev/null; then
  echo "REFUSED: a llama process is running"; pgrep -af "[l]lama-server|[l]lama-bench" | cut -c1-160; exit 1
fi
cp -a "$LIVE" "$ARCH"
echo "archived $LIVE -> $ARCH ($(ls "$ARCH" | wc -l) files)"
cp -a "$NEW"/. "$LIVE"/
echo "copied $(ls "$NEW" | wc -l) files from $NEW"
for f in "$NEW"/*; do
  b=$(basename "$f")
  [ -L "$f" ] && continue
  cmp -s "$f" "$LIVE/$b" || { echo "MISMATCH $b"; exit 1; }
done
echo "all regular files byte-identical to the new build"
grep -a -c "SCHED PROFILE over" "$LIVE/libggml-base.so" || true
echo "SWAP DONE $STAMP"
