#!/bin/bash
# Burst-decode ONE recorded NVR motion clip on a tablet, as fast as the hardware will go.
#
# The SCOPE §7g path in miniature: the NVR already detected the motion and recorded the clip, so
# there is nothing to decode continuously -- decode the interesting seconds, hard, when something
# happened. This answers whether playback decode beats realtime by enough to make that worthwhile.
#
# Playback, not realmonitor: `cam/playback?channel=N&starttime=...&endtime=...`, and the documented
# pacing is PER SESSION, so several clips in parallel across the fellowship multiply.
# The MOTION recording slot MainFormat[1] is 4K@15 on both channels, so a motion clip is very likely
# 4K even when the LIVE main stream is 1080p. The decoder reports what it got in its `output ...`
# line; read that rather than assuming.
set -u

CRED="${CRED:?set CRED=user:urlencodedpass}"
NVR="${NVR:?set NVR to the address of the NVR}"
CH="${CH:-1}"
START="${START:?set START=YYYY_MM_DD_HH_MM_SS}"
END="${END:?set END=YYYY_MM_DD_HH_MM_SS}"
MIME="${MIME:-video/hevc}"
W="${W:-3840}"
H="${H:-2160}"

case "$MIME" in
  *hevc*) BSF=hevc_mp4toannexb; FMT=hevc ;;
  *)      BSF=h264_mp4toannexb; FMT=h264 ;;
esac

URL="rtsp://${CRED}@${NVR}:554/cam/playback?channel=${CH}&starttime=${START}&endtime=${END}"

echo "clip ${START} -> ${END}  ch${CH}"
T0=$(date +%s)
ffmpeg -hide_banner -loglevel error -rtsp_transport tcp -i "$URL" \
  -an -c:v copy -bsf:v "$BSF" -f "$FMT" - 2>"$HOME/burst_ff.err" \
  | ./hwdec --mime "$MIME" --width "$W" --height "$H" --bench 2>&1 | tail -6
T1=$(date +%s)
echo "wall seconds: $((T1 - T0))"
echo "--- ffmpeg stderr (first 3) ---"
head -3 "$HOME/burst_ff.err"
