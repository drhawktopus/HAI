#!/usr/bin/env bash
# #34 B11 lab engine patches on rigby's CUDA 11.4 build: #3 slot_resume_patch.py (the slot save carries the drafter's
# memory + MTP carry + pending token; a `lab_resume` continue takes the interrupted run's next step), then #2
# slot_rng_patch.py (+ the sampler's random state).
#
#   bash build_slot_resume.sh build              -> builds in a COPY of the live tree; nothing live changes
#   bash build_slot_resume.sh swap <copy dir>    -> archives the live bin, patches the live sources, copies the
#                                                   built tree INTO the live build dir in place
#   PATCH=slot_rng_patch.py MARKER="the sampler's random state" bash build_slot_resume.sh build|swap ...
#                                                -> the next lab patch, same two steps; MARKER = a string the patched
#                                                   libllama-server-impl.so must hold
#
# Why a copy: build_slot_pending.sh built straight into the tree /llama/cuda is mounted from, so a Tolkien order that
# launched mid-build would have loaded half-linked libraries. The swap copies files into the EXISTING directories -
# /llama/cuda is a bind mount of build-cuda-114/bin, and a mount follows the directory it was made from (a renamed
# or replaced dir leaves the container on the old one). Refuses while a CUDA llama process runs.
set -euo pipefail
BUILD_HOME="${BUILD_HOME:-$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)}"   # the home of whoever ran it (run_remote runs it through sudo, so $HOME is root's)
OUT=$BUILD_HOME/llama-build-rpcasync
LIVE=$BUILD_HOME/llama.cpp-latest
IMG=nvidia/cuda:11.4.3-devel-ubuntu20.04
STAMP=$(date +%Y%m%d-%H%M%S)
PATCH=${PATCH:-slot_resume_patch.py}
MARKER=${MARKER:-lab resume: restored drafter}
TAG=$(basename "$PATCH" .py | tr -d _)
FILES="common/speculative.h common/speculative.cpp common/sampling.h common/sampling.cpp src/llama-ext.h src/llama-sampler.cpp tools/server/server-context.cpp tools/server/server-task.h tools/server/server-task.cpp tools/server/server-schema.cpp"
busy() { pgrep -f "[l]lama/cuda/llama-server|[l]lama-cuda-114/bin/llama" >/dev/null; }

case "${1:-}" in
build)
  WORK=$BUILD_HOME/llama.cpp-$TAG-$STAMP
  exec > "$OUT/build-$TAG-$STAMP.out" 2>&1
  cp -a "$LIVE" "$WORK"
  echo "copy $LIVE ($(git -C "$LIVE" log -1 --format=%h)) -> $WORK; patch $PATCH"
  python3 "$OUT/$PATCH" "$WORK"
  for f in $FILES; do printf '%s %s\n' "$(grep -c lab_ "$WORK/$f")" "$f"; done
  docker run --rm -e MARKER="$MARKER" -e TAG="$TAG" -v "$WORK":/src -w /src "$IMG" bash -c '
    set -euo pipefail
    apt-get update -qq && apt-get install -y -qq python3-pip > /dev/null && pip3 install -q "cmake>=3.24,<4"
    export PATH=/usr/local/bin:$PATH
    nice -n 10 cmake --build build-cuda-114 -j 8 > build-cuda-114.make-$TAG.log 2>&1 || { tail -60 build-cuda-114.make-$TAG.log; exit 1; }
    chown -R 1000:1000 build-cuda-114 build-cuda-114.make-$TAG.log
    # the server code lives in libllama-server-impl.so (llama-server is a stub)
    grep -a -c "$MARKER" build-cuda-114/bin/libllama-server-impl.so
  ' || { echo "BUILD-$TAG FAILED"; exit 1; }
  echo "BUILD-$TAG DONE $STAMP $WORK"
  ;;
swap)
  WORK=${2:?usage: swap <copy dir>}
  exec > "$OUT/swap-$TAG-$STAMP.out" 2>&1
  if busy; then echo "REFUSED: a CUDA llama process is running"; echo "SWAP-$TAG FAILED"; exit 1; fi
  grep -a -q "$MARKER" "$WORK/build-cuda-114/bin/libllama-server-impl.so" || { echo "REFUSED: $WORK has no patched build"; echo "SWAP-$TAG FAILED"; exit 1; }
  ARCH="$BUILD_HOME/llama-cuda114-bin-archive-$(basename "$LIVE")-$STAMP"
  cp -a "$LIVE/build-cuda-114/bin" "$ARCH"
  for f in $FILES; do cp --update=none "$LIVE/$f" "$OUT/$(basename "$f").pre-$TAG-$STAMP"; done
  python3 "$OUT/$PATCH" "$LIVE"
  cp -a "$WORK/build-cuda-114/." "$LIVE/build-cuda-114/"
  if busy; then echo "WARNING: a CUDA llama process started during the swap - it may hold mixed libraries"; fi
  for f in libllama-server-impl.so libllama.so llama-server; do
    [ -e "$WORK/build-cuda-114/bin/$f" ] || continue
    printf '%s  %s\n' "$(sha256sum < "$LIVE/build-cuda-114/bin/$f" | cut -c1-16)" "$f"
    cmp -s "$WORK/build-cuda-114/bin/$f" "$LIVE/build-cuda-114/bin/$f" && echo "  = the patched build" || { echo "  DIFFERS from the patched build"; echo "SWAP-$TAG FAILED"; exit 1; }
  done
  echo "archived bin -> $ARCH"
  echo "SWAP-$TAG DONE $STAMP"
  ;;
*) echo "usage: [PATCH=... MARKER=...] build | swap <copy dir>"; exit 2 ;;
esac
