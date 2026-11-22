#!/bin/bash
# SHORT cluster battery — same load profile as the 25s burn that ran fine.
# No long sustained soaks (unwise unattended on battery with no telemetry).
# Results -> cluster/RESULTS-short.md
export MSYS_NO_PATHCONV=1
. "$(dirname "$0")/../settings.sh"   # FS, KEY, ADB, TABLETS and SERIALS, from the lab's settings
CL="$FS/cluster"
OUT="$CL/RESULTS-short.md"
K="$KEY"
O="-i $K -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=20 -o LogLevel=ERROR"
ALL="${TABLETS[gandalf]} ${TABLETS[frodo]} ${TABLETS[sam]} ${TABLETS[pippin]} ${TABLETS[merry]}"

say() { echo "$@" | tee -a "$OUT"; }
: > "$OUT"
say "# Short cluster battery — $(date)"
say ""

say "## 0. Liveness"
say '```'
LIVE=""
for IP in $ALL; do
  R=$(timeout 30 ssh $O -p 8022 "$IP" "nproc" 2>/dev/null | tr -d '\r')
  if [ -n "$R" ]; then say "  $IP  ${R} cores UP"; LIVE="$LIVE $IP"; else say "  $IP  DOWN"; fi
done
say '```'
say ""

say "## 1. Interconnect tax vs NODE COUNT (N=2048 fixed)"
say ""
say "Tonight: 2 nodes 80.8%, 5 nodes 93.4%. Does it keep climbing?"
say ""
say '```'
for KN in 1 2 3 4 5; do
  SUB=$(echo $LIVE | tr ' ' '\n' | head -$KN | tr '\n' ',' | sed 's/,$//')
  [ "$(echo "$SUB" | tr ',' '\n' | grep -c .)" -lt "$KN" ] && continue
  R=$(cd "$CL" && CLUSTER_NODES="$SUB" python master_n.py 2048 2>/dev/null | grep -E "sum of|effective|interconnect" | tr -s ' ' | tr '\n' '|')
  say "  nodes=$KN | $R"
done
say '```'
say ""

say "## 2. Interconnect tax vs PROBLEM SIZE (all live nodes)"
say ""
say "Compute ~ N^3, transfer ~ N^2. Bigger N should amortise the tax."
say ""
say '```'
ALLC=$(echo $LIVE | tr ' ' ',')
for N in 1536 2048 3072 4096; do
  R=$(cd "$CL" && CLUSTER_NODES="$ALLC" python master_n.py $N 2>/dev/null | grep -E "effective|interconnect" | tr -s ' ' | tr '\n' '|')
  say "  N=$N | $R"
done
say '```'
say ""

say "## 3. Short burn (15s) — compute-bound reference"
say '```'
rm -f "$CL"/burn_*.json
for IP in $LIVE; do
  ( timeout 200 ssh $O -p 8022 "$IP" "python burn.py 1024 15" > "$CL/burn_$IP.json" 2>/dev/null ) &
done
wait
(cd "$CL" && python burn_sum.py 2>/dev/null) | tee -a "$OUT"
say '```'
say ""
say "## Done $(date)"
