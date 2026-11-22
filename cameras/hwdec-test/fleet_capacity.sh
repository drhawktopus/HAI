#!/bin/bash
# How many concurrent decode streams can ONE tablet sustain? Measured with NOTHING else in the loop:
# no live detection load perturbing the tablets, and no test load degrading a production camera.
#
# METHOD: N decoders on one node, each its own port, all pulling the same camera (the NVR serves ~10
# concurrent sessions, so the source is not the limit). Read each for a fixed window and count
# bytes. A stream keeping up delivers exactly fps*seconds frames. The first N where delivery falls
# short is the ceiling.
#
# ASSERT ZERO between arms. "printed killed" is not "is dead": a survivor from the previous arm
# makes the next arm's numbers quietly wrong.
set -u

. "$(dirname "$0")/settings.sh"   # KEY, NVR, ADB and TABLETS, from the lab's settings
OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
NODE="${NODE:-${TABLETS[gandalf]}}"
CRED="${CRED:?set CRED}"
CH="${CH:-1}"
W="${W:-2688}"
H="${H:-1520}"
SCALE="${SCALE:-704x480}"
FPS="${FPS:-5}"
SECS="${SECS:-30}"
MAXN="${MAXN:-4}"
FB=$((704 * 480 * 3 / 2))

HERE="$(cd "$(dirname "$0")" && pwd)"
tr -d '\r' < "$HERE/run_serve_port.sh" > /tmp/rsp.sh

r() { ssh -p 8022 -i "$KEY" $OPTS "$NODE" "$1" 2>&1; }
launch() { PORT=$1 ssh -p 8022 -i "$KEY" $OPTS "$NODE" \
    "PORT=$1 CH=$CH MIME=video/hevc W=$W H=$H SCALE='$SCALE' FPS=$FPS CRED='$CRED' NVR='$NVR' bash -s" \
    < /tmp/rsp.sh >/dev/null 2>&1; }

zero() {
  r "pkill -f '[h]wdec --mime' >/dev/null 2>&1; sleep 2; pkill -9 -f '[h]wdec --mime' >/dev/null 2>&1; pkill -f '[c]am/realmonitor' >/dev/null 2>&1; sleep 1; ps -ef | grep -c '[h]wdec --mime'" | tr -d '\r\n'
}

echo "node=$NODE  ch=$CH  source ${W}x${H}  ->  ${SCALE} @${FPS}fps   ${SECS}s per arm"
echo

for N in $(seq 1 "$MAXN"); do
  left=$(zero)
  if [ "$left" != "0" ]; then echo "N=$N VOID ($left survivors)"; continue; fi

  # SETTLE BEFORE THE ARM. Back to back, the NVR has not yet released the previous arm's RTSP
  # sessions, and N=3/N=4 both return EXACTLY ZERO -- which reads as a hard tablet capacity limit
  # and is nothing of the sort: run in isolation, three decoders start and deliver fine.
  sleep 15
  for i in $(seq 0 $((N - 1))); do launch $((8803 + i)); sleep 2; done
  sleep 5

  CMD=""
  for i in $(seq 0 $((N - 1))); do
    P=$((8803 + i))
    CMD="$CMD (exec 3<>/dev/tcp/$NODE/$P; timeout $SECS cat <&3 | wc -c > /tmp/fc_$P.n) & "
  done
  OUT=$(ssh sundae "$CMD wait; for f in /tmp/fc_88*.n; do echo \"\$(basename \$f) \$(cat \$f)\"; done; rm -f /tmp/fc_88*.n" 2>&1)

  WANT=$((FPS * SECS)); TOTAL=0; OK=1
  echo "--- N=$N ---"
  while read -r name bytes; do
    [ -z "${bytes:-}" ] && continue
    fr=$((bytes / FB)); TOTAL=$((TOTAL + fr)); pct=$((fr * 100 / WANT))
    [ "$pct" -lt 90 ] && OK=0
    printf "   %-12s %4d/%d frames (%d%%)\n" "$name" "$fr" "$WANT" "$pct"
  done <<< "$OUT"
  echo "   aggregate: $TOTAL frames delivered, $( [ $OK -eq 1 ] && echo 'ALL KEEPING UP' || echo 'SHORTFALL -> ceiling reached')"

  # `top -b -n 1` reports 0% for every process: its first sample has no interval to compare
  # against. Take TWO samples and read the second.
  r "echo -n '   decoder cpu: '; top -b -n 2 -d 2 2>/dev/null | awk '/hwdec --mime/ {s[NR]=\$9; n++} END {}' >/dev/null; top -b -n 2 -d 2 2>/dev/null | grep 'hwdec --mime' | tail -n $N | awk '{s+=\$9; c++} END {printf \"%.0f%% of one core across %d decoder(s)\n\", s, c}'; echo -n '   decode frames/log: '; for f in ~/hwdec_88*.log; do grep -o 'frames=[0-9]*' \$f | tail -1 | tr '\n' ' '; done; echo"
  echo
done

zero >/dev/null
echo "cleaned up"
