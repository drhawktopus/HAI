#!/bin/bash
# How many concurrent 1080p decode streams can ONE tablet actually serve?
#
# This number decides how many tablets 8-10 cameras need. Arithmetic from a single data point
# (1080p downscale = 56% of one core on gandalf) is a prediction, not a measurement.
#
# METHOD: start N decoders on the SAME node, each pulling the same camera on its own port (the NVR
# serves ~10 concurrent sessions, so the source is not the limit). Read each for a fixed window and
# count bytes. A stream that is keeping up delivers exactly fps * seconds frames; the first N where
# delivered drops below that is the ceiling.
#
# ncnn_detect IS LEFT RUNNING. The real pipeline never stops, so capacity measured on an idle node
# would be a number the lane can never have. Detection contention is part of the answer.
#
# Run FROM Softserve. Reads results via sundae (the container network reaches the tablets).
set -u

. "$(dirname "$0")/settings.sh"   # KEY, NVR, ADB and TABLETS, from the lab's settings
OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
NODE="${NODE:-${TABLETS[sam]}}"
CRED="${CRED:?set CRED}"
CH="${CH:-1}"
W="${W:-1920}"
H="${H:-1080}"
SECS="${SECS:-30}"
FPS="${FPS:-5}"
MAXN="${MAXN:-4}"
FRAMEBYTES=$((704 * 480 * 3 / 2))

r() { ssh -p 8022 -i "$KEY" $OPTS "$NODE" "$1" 2>&1; }

# ASSERT ZERO, do not trust pkill's exit. A leftover decoder from a previous arm silently makes
# the next arm's numbers wrong, and "printed killed" is not "is dead".
cleanup() {
  r "pkill -f '[h]wdec --mime' >/dev/null 2>&1; sleep 2; pkill -9 -f '[h]wdec --mime' >/dev/null 2>&1; sleep 1; pkill -f '[c]am/realmonitor' >/dev/null 2>&1; sleep 1" >/dev/null
  local n
  n=$(r "ps -ef | grep -c '[h]wdec --mime'")
  echo "$n" | tr -d '\r\n'
}

echo "node=$NODE  ch=$CH  ${W}x${H}  ${SECS}s per arm  target ${FPS} fps/stream"
echo

for N in $(seq 1 "$MAXN"); do
  left=$(cleanup)
  if [ "$left" != "0" ]; then echo "ARM N=$N VOID -- $left decoder(s) survived cleanup"; continue; fi

  # start N decoders, ports 8803..
  for i in $(seq 0 $((N - 1))); do
    P=$((8803 + i))
    IN="ffmpeg -hide_banner -loglevel error -rtsp_transport tcp -i 'rtsp://${CRED}@${NVR}:554/cam/realmonitor?channel=${CH}&subtype=0' -an -c:v copy -bsf:v hevc_mp4toannexb -f hevc -"
    r "cd ~ && setsid ./hwdec --mime video/hevc --width $W --height $H --scale 704x480 --fps $FPS \
        --serve $P --heartbeat ~/hwdec_$P.beat --input-cmd \"$IN\" > ~/hwdec_$P.log 2>&1 < /dev/null &" >/dev/null
  done
  sleep 4

  # read all N concurrently from sundae, one background reader each
  CMD=""
  for i in $(seq 0 $((N - 1))); do
    P=$((8803 + i))
    CMD="$CMD (exec 3<>/dev/tcp/$NODE/$P; timeout $SECS cat <&3 | wc -c > /tmp/cap_$P.n) & "
  done
  ssh sundae "$CMD wait; for f in /tmp/cap_88*.n; do echo -n \"\$f \"; cat \$f; done; rm -f /tmp/cap_88*.n" 2>&1 \
    | while read -r f b; do
        [ -z "${b:-}" ] && continue
        fr=$((b / FRAMEBYTES))
        want=$((FPS * SECS))
        pct=$((fr * 100 / want))
        printf "  N=%d %s -> %d frames of %d expected (%d%%)\n" "$N" "$(basename "$f")" "$fr" "$want" "$pct"
      done

  # node-side truth: total CPU of the decoders, and whether the detector is still alive
  r "echo -n '   sam cpu: '; top -b -n 1 2>/dev/null | grep '[h]wdec' | awk '{s+=\$9} END {print s\"% of one core across \"NR\" decoders\"}'; echo -n '   detector: '; ps -ef | grep -c '[n]cnn_detect'"
  echo
done

cleanup >/dev/null
echo "cleaned up"
