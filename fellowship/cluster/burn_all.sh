#!/bin/bash
# Fire burn.py on all five nodes SIMULTANEOUSLY and sum the result.
# Ships ~2 KB per node. This is the cluster's best case: compute-bound,
# transfer-free -- the workload class these tablets are genuinely good at.
export MSYS_NO_PATHCONV=1
. "$(dirname "$0")/../settings.sh"   # FS, KEY, ADB, TABLETS and SERIALS, from the lab's settings
SC="$FS"
K="$KEY"
O="-i $K -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=20 -o LogLevel=ERROR"
IPS="${TABLETS[gandalf]} ${TABLETS[frodo]} ${TABLETS[sam]} ${TABLETS[pippin]} ${TABLETS[merry]}"
SECS="${1:-20}"

echo "=== staging burn.py (2 KB per node) ==="
for IP in $IPS; do scp $O -P 8022 "$SC/cluster/burn.py" "$IP:burn.py" >/dev/null 2>&1 & done
wait
echo "=== firing all 5 concurrently, ${SECS}s each ==="
date
rm -f "$SC/cluster/burn_"*.json
for IP in $IPS; do
  ( ssh $O -p 8022 "$IP" "python burn.py 1024 $SECS" > "$SC/cluster/burn_$IP.json" 2>/dev/null ) &
done
wait
date
echo ""
python "$SC/cluster/burn_sum.py"
