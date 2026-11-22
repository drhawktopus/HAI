#!/bin/bash
# Start ONE hwdec on a given port, with its own log and beat. Piped in over ssh as `bash -s`.
#
# WHY `bash -s` AND NOT `ssh host "cmd &"`: an inline backgrounded command does NOT let ssh
# return -- ssh waits for the channel to close even though the process is detached, so a harness
# hangs with no output. Piping a script that exits is the pattern that works.
#
# DOES NOT pkill siblings. The multi-stream arms need several decoders alive at once, and a
# `pkill -f 'hwdec --mime'` would take them all out, and can match the ssh session issuing it
# (the pattern matches its own command line).
#
# THIS LEAVES AN ORPHAN, ON PURPOSE, AND YOU MUST CLEAN IT UP. `setsid` means the decoder survives
# this script and the ssh session, as PPID 1. A leftover one on port 8803 blocks the AGENT'S
# supervised decoder, which then logs `Address already in use` on a 20s->300s backoff and reads
# exactly like the supervisor being broken. **Run `stop_decoders.sh` when you are done.**
#
# AND THE AGENT OWNS THIS PORT on any node carrying a ~/hwdec.json assignment: tablet_foundry
# spawns and supervises hwdec there. Starting a manual decoder on the same port RACES IT. Test on an
# unassigned node, or a different port.
set -u

PORT="${PORT:?set PORT}"
CH="${CH:-1}"
MIME="${MIME:-video/hevc}"
W="${W:-2688}"
H="${H:-1520}"
SCALE="${SCALE:-704x480}"
FPS="${FPS:-5}"
NVR="${NVR:?set NVR to the address of the NVR}"
CRED="${CRED:?set CRED=user:urlencodedpass}"

LOG="$HOME/hwdec_$PORT.log"
BEAT="$HOME/hwdec_$PORT.beat"
rm -f "$LOG" "$BEAT"

case "$MIME" in
  *hevc*) BSF=hevc_mp4toannexb; FMT=hevc ;;
  *)      BSF=h264_mp4toannexb; FMT=h264 ;;
esac

IN="ffmpeg -hide_banner -loglevel error -rtsp_transport tcp \
-i 'rtsp://${CRED}@${NVR}:554/cam/realmonitor?channel=${CH}&subtype=0' \
-an -c:v copy -bsf:v ${BSF} -f ${FMT} -"

ARGS="--mime $MIME --width $W --height $H --serve $PORT --heartbeat $BEAT --fps $FPS"
[ -n "$SCALE" ] && [ "$SCALE" != "none" ] && ARGS="$ARGS --scale $SCALE"
# CODEC forces one decoder by name, so "can THIS codec take these pixels" is a single answerable
# question instead of a guess about the device.
[ -n "${CODEC:-}" ] && ARGS="$ARGS --codec $CODEC"

setsid ./hwdec $ARGS --input-cmd "$IN" > "$LOG" 2>&1 < /dev/null &
sleep 2
echo "port=$PORT pid=$(pgrep -f "serve $PORT" | tr '\n' ' ') log=$LOG"
