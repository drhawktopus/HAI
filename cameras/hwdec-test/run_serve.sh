#!/bin/bash
# Start hwdec in serve mode on a tablet, detached, and report where its log and beat are.
#
# Run this ON the tablet (piped in over ssh). It backgrounds hwdec with setsid so the process
# survives the ssh session closing (setsid, nohup and plain & all survive a closed adb session on
# these nodes).
#
# This is a TEST harness for proving the decoder. It is deliberately NOT the production launcher:
# production is the fellow agent supervising hwdec the same way it supervises ncnn_detect, so that
# it inherits the restart policy and health surface rather than growing a second one.
set -u

PORT="${PORT:-8803}"
CH="${CH:-1}"
MIME="${MIME:-video/hevc}"
W="${W:-1920}"
H="${H:-1080}"
SCALE="${SCALE:-704x480}"
FPS="${FPS:-5}"
NVR="${NVR:?set NVR to the address of the NVR}"
CRED="${CRED:?set CRED=user:urlencodedpass}"

# TERMUX HOME, NOT /data/local/tmp. Termux's app uid (10144) is DENIED write there: that
# directory belongs to adb's uid 2000, which is why the wifi watchdog (adb-started) lives there and
# this (Termux-started) cannot. A denied write there leaves an empty pid behind a "started" line.
# The agent that will supervise hwdec runs as the same Termux uid, so it can read this beat.
LOG="$HOME/hwdec.log"
BEAT="$HOME/hwdec.beat"

pkill -f 'hwdec --mime' 2>/dev/null
sleep 1

# -c:v copy => demux only, no decode. The hardware does the only expensive part.
# The annexb bitstream filter is codec-specific: hevc_mp4toannexb vs h264_mp4toannexb.
case "$MIME" in
  *hevc*) BSF=hevc_mp4toannexb; FMT=hevc ;;
  *)      BSF=h264_mp4toannexb; FMT=h264 ;;
esac

# NO SOCKET-TIMEOUT FLAG HERE. `-stimeout` was removed in ffmpeg 6+ (the tablets run 8.1.2) and
# `-rw_timeout` is not accepted by the rtsp demuxer either; with either one ffmpeg exits instantly,
# hwdec sees an empty pipe and reports "no parameter sets found before first slice", which reads as
# a STREAM fault. Verify the input command standalone before blaming what consumes it.
# Liveness is covered without it: a dead client kills the child, EOF ends the session and returns
# to listening, and the heartbeat's frame counter exposes a frozen decode.
IN="ffmpeg -hide_banner -loglevel error -rtsp_transport tcp \
-i 'rtsp://${CRED}@${NVR}:554/cam/realmonitor?channel=${CH}&subtype=0' \
-an -c:v copy -bsf:v ${BSF} -f ${FMT} -"

# EXTRA carries the experiment arm (--skip-param-sets / --pts-slices-only), so one build serves
# every arm and each run moves exactly one variable.
setsid ./hwdec --mime "$MIME" --width "$W" --height "$H" \
  --scale "$SCALE" --fps "$FPS" --serve "$PORT" ${EXTRA:-} \
  --heartbeat "$BEAT" --input-cmd "$IN" > "$LOG" 2>&1 < /dev/null &

sleep 2
echo "started pid=$(pgrep -f 'hwdec --mime' | tr '\n' ' ')"
echo "log=$LOG beat=$BEAT port=$PORT"
