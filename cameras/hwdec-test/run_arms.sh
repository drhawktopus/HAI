#!/bin/bash
# Run the deadlock experiment arms back to back, one variable each, and print the verdict per arm.
#
# The baseline wedges somewhere in frames 96-126, so the pass/fail test is "did it get well past
# that?". Each arm reads for 45 s, which is ~5x the 8 s the baseline survives.
#
# The verdict is taken from the DECODER'S OWN last diag line, not from the byte count: a client
# that read plenty of bytes before the stall still reports a healthy-looking total.
set -u
cd "$(dirname "$0")"

. "$(dirname "$0")/settings.sh"   # KEY, NVR, ADB and TABLETS, from the lab's settings
OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
NODE="${TABLETS[gandalf]}"
SECS="${SECS:-45}"

tr -d '\r' < run_serve.sh > /tmp/rs.sh

# WAIT FOR THE PORT, DO NOT JUST SLEEP. `pkill` + `sleep 1` is not enough: shutdown reaps the
# ffmpeg child (up to 2 s) before closing the listener, so the next arm dies on "Address already in
# use" and reads ZERO bytes, and a void arm reads exactly like a failed one.
wait_port_free () {
  local i
  for i in $(seq 1 40); do
    if ! ssh -p 8022 -i "$KEY" $OPTS "$NODE" \
         "timeout 3 bash -c '</dev/tcp/127.0.0.1/8803' 2>/dev/null" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "  !! PORT 8803 STILL HELD after 40s -- arm would be void, refusing to run it"
  return 1
}

: "${NVR_PASSWORD:?set NVR_PASSWORD to the NVR password (it lives in the credential locker)}"

arm () {
  local name="$1"; shift
  local extra="$1"; shift
  echo "================ ARM: $name   (extra='$extra') ================"
  wait_port_free || { echo; return; }
  ssh -p 8022 -i "$KEY" $OPTS "$NODE" \
      "CRED='admin:${NVR_PASSWORD}' NVR='$NVR' PORT=8803 CH=1 MIME=video/hevc W=1920 H=1080 \
       SCALE=704x480 FPS=5 EXTRA='$extra' bash -s" < /tmp/rs.sh > /dev/null 2>&1
  # Prove the listener came up as THIS arm before trusting anything it reports.
  if ssh -p 8022 -i "$KEY" $OPTS "$NODE" "grep -q 'Address already in use' ~/hwdec.log" 2>/dev/null; then
      echo "  !! ARM VOID -- listener never started"; echo; return
  fi
  ssh sundae "exec 3<>/dev/tcp/$NODE/8803; timeout $SECS cat <&3 | wc -c" 2>&1 \
      | sed 's/^/  bytes_read=/'
  ssh -p 8022 -i "$KEY" $OPTS "$NODE" "tail -2 ~/hwdec.log" 2>&1 | sed 's/^/  /'
  ssh -p 8022 -i "$KEY" $OPTS "$NODE" "pkill -f 'hwdec --mime'; sleep 1" >/dev/null 2>&1
  echo
}

ARMS="${ARMS:-all}"
if [ "$ARMS" = "all" ]; then
  arm "BASELINE (control)"                ""
  arm "C: skip mid-stream parameter sets" "--skip-param-sets"
  arm "A: PTS on picture NALs only"       "--pts-slices-only"
  arm "C+A: both"                         "--skip-param-sets --pts-slices-only"
else
  arm "C: skip mid-stream parameter sets" "--skip-param-sets"
  arm "C+A: both"                         "--skip-param-sets --pts-slices-only"
fi

# The baseline wedges somewhere in frames 96-126 (not a fixed count). So the pass test is "well
# past ~130 and still climbing"; a bare "it reached 130" is not enough on its own.
echo "VERDICT: PASS = last diag shows frames well past ~130 AND still climbing between the two lines."
