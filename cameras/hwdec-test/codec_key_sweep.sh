#!/bin/bash
# Which decoder NAME actually takes 2K on this node? Try every candidate, report what delivers.
#
# merry and pippin are a slightly different model from gandalf/sam/frodo, and the qcom decoder they
# advertise refuses to configure 2688x1520. But their own /vendor/etc/media_codecs*.xml also lists
# OMX.google.hevc.decoder at 2048x2048 AND 4096x4096, plus c2.android.hevc.decoder -- neither of
# which was in hwdec's try-list. So this is very likely another selection gap, not a limit.
#
# THE TEST IS DELIVERED FRAMES, NOT "configure succeeded". sam's OMX.MTK decoder configures AND
# starts at 3840x2160 -- a resolution it declares it cannot do -- and then emits nothing. Anything
# short of frames arriving is not evidence it works.
set -u

. "$(dirname "$0")/settings.sh"   # KEY, NVR, ADB and TABLETS, from the lab's settings
OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
NODE="${NODE:?set NODE to the address of the tablet (the Fellowship roster lists them)}"
CRED="${CRED:?set CRED}"
CH="${CH:-1}"
W="${W:-2688}"
H="${H:-1520}"
SECS="${SECS:-20}"
FB=$((704 * 480 * 3 / 2))

CANDIDATES="
OMX.qcom.video.decoder.hevc
c2.qti.hevc.decoder
OMX.google.hevc.decoder
c2.android.hevc.decoder
c2.mtk.hevc.decoder
OMX.MTK.VIDEO.DECODER.HEVC
"

echo "node=$NODE  source ${W}x${H}  ${SECS}s each"
echo

for nm in $CANDIDATES; do
  ssh -p 8022 -i "$KEY" $OPTS "$NODE" "pkill -f '[h]wdec --mime' >/dev/null 2>&1; sleep 1" >/dev/null 2>&1
  IN="ffmpeg -hide_banner -loglevel error -rtsp_transport tcp -i 'rtsp://${CRED}@${NVR}:554/cam/realmonitor?channel=${CH}&subtype=0' -an -c:v copy -bsf:v hevc_mp4toannexb -f hevc -"
  ssh -p 8022 -i "$KEY" $OPTS "$NODE" \
    "cd ~ && rm -f hwdec_key.log && setsid ./hwdec --mime video/hevc --width $W --height $H \
     --scale 704x480 --fps 5 --serve 8899 --codec '$nm' --input-cmd \"$IN\" \
     > hwdec_key.log 2>&1 < /dev/null & sleep 2; exit 0" >/dev/null 2>&1
  sleep 2
  B=$(ssh sundae "exec 3<>/dev/tcp/$NODE/8899 2>/dev/null && timeout $SECS cat <&3 | wc -c || echo 0" 2>/dev/null | tail -1)
  [ -z "${B:-}" ] && B=0
  FR=$((B / FB))
  NOTE=$(ssh -p 8022 -i "$KEY" $OPTS "$NODE" "grep -oE 'decoder started: [^ ]*|cannot configure [0-9x]*|would not start|no named decoder' ~/hwdec_key.log 2>/dev/null | head -1" 2>/dev/null | tr -d '\r')
  printf "  %-32s %4d frames   %s\n" "$nm" "$FR" "${NOTE:-(could not create)}"
done

ssh -p 8022 -i "$KEY" $OPTS "$NODE" "pkill -f '[h]wdec --mime' >/dev/null 2>&1; sleep 1; echo -n '  cleanup: hwdec='; ps -ef | grep -c '[h]wdec --mime'" 2>&1
