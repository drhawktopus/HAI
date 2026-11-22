#!/bin/bash
# Wake every node out of Android's idle power state BEFORE benchmarking.
# Cold-start readings measure the power policy, not the hardware:
#   X606F park at 900MHz (of 2301); an X505F had 3 of 4 cores OFFLINED.
# Same class as the lab's "prove the GPU is healthy before quoting a timing".
export MSYS_NO_PATHCONV=1
. "$(dirname "$0")/../settings.sh"   # FS, KEY, ADB, TABLETS and SERIALS, from the lab's settings
CL="$FS/cluster"
K="$KEY"
O="-i $K -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=20 -o LogLevel=ERROR"
ALL="${TABLETS[gandalf]} ${TABLETS[frodo]} ${TABLETS[sam]} ${TABLETS[pippin]} ${TABLETS[merry]}"
SECS="${1:-20}"

echo "=== warming all nodes (${SECS}s all-core load) ==="
for IP in $ALL; do
  ( ssh $O -p 8022 "$IP" "python burn.py 512 $SECS" >/dev/null 2>&1 ) &
done
wait

echo "=== post-warm state ==="
for IP in $ALL; do
  R=$(timeout 30 ssh $O -p 8022 "$IP" "echo -n \"online=\$(nproc) \"; for c in 0 4; do f=/sys/devices/system/cpu/cpu\$c/cpufreq/scaling_cur_freq; [ -r \$f ] && awk -v c=\$c '{printf \"cpu%d=%dMHz \", c, \$1/1000}' \$f; done" 2>/dev/null | tr -d '\r')
  echo "  $IP  ${R:-?}"
done
