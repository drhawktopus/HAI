#!/bin/bash
# Harvest one frame per motion event from the NVR's RTSP PLAYBACK stream.
# Runs INSIDE the linuxserver/ffmpeg container on Sundae (the host has no ffmpeg; the lab's
# documented pattern, same as the whisper audio extract).
#
# Reads /work/extract_tasks.tsv : channel <TAB> start <TAB> end <TAB> outname
# Writes JPEGs to /out, and every ffmpeg stderr line to /out/_extract.log
#
# No -stimeout: it was removed from ffmpeg (this image is Lavf63/ffmpeg 8.x), and an unrecognised
# option makes ffmpeg exit before it opens anything. External `timeout` does the same job and cannot
# rot with ffmpeg's option names.
# Do not send stderr to /dev/null: the line that explains a failed pull is on stderr.
set -u
TASKS=/work/extract_tasks.tsv
OUT=/out
LOG=$OUT/_extract.log
PW="${NVR_PASSWORD:?set NVR_PASSWORD to the NVR password (it lives in the credential locker)}"
HOST="${NVR_HOST:?set NVR_HOST to the address of the NVR}"

: > "$LOG"
ok=0; fail=0; n=0
total=$(wc -l < "$TASKS")

while IFS=$'\t' read -r ch st en name; do
  [ -z "${ch:-}" ] && continue
  n=$((n+1))
  [ -s "$OUT/$name" ] && { ok=$((ok+1)); continue; }
  url="rtsp://admin:${PW}@${HOST}:554/cam/playback?channel=${ch}&starttime=${st}&endtime=${en}"
  if timeout 40 ffmpeg -hide_banner -loglevel warning -rtsp_transport tcp \
      -i "$url" -frames:v 1 -update 1 -q:v 3 -y "$OUT/$name" >>"$LOG" 2>&1 \
      && [ -s "$OUT/$name" ]; then
    ok=$((ok+1))
  else
    fail=$((fail+1))
    echo "FAILED ch$ch $st -> $name" | tee -a "$LOG"
  fi
  [ $((n % 10)) -eq 0 ] && echo "  ... $n/$total (ok=$ok fail=$fail)"
done < "$TASKS"

echo "============================================"
echo "extracted ok=$ok  failed=$fail  of $total tasks"
echo "jpegs in out: $(ls -1 "$OUT"/*.jpg 2>/dev/null | wc -l)"
if [ "$fail" -gt 0 ]; then
  echo "--- last stderr lines (why they failed):"
  tail -6 "$LOG"
fi
