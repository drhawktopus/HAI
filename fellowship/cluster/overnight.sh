#!/bin/bash
# Overnight cluster test battery. Bounded, non-destructive, read-only w.r.t.
# device state (no flashing, no writes beyond scratch files on the nodes).
# It extends the finding that the cluster is transfer-bound, not compute-bound.
#
# Results -> cluster/RESULTS-overnight.md
export MSYS_NO_PATHCONV=1
. "$(dirname "$0")/../settings.sh"   # FS, KEY, ADB, TABLETS and SERIALS, from the lab's settings
CL="$FS/cluster"
OUT="$CL/RESULTS-overnight.md"
K="$KEY"
O="-i $K -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=20 -o LogLevel=ERROR"
ALL="${TABLETS[gandalf]} ${TABLETS[frodo]} ${TABLETS[sam]} ${TABLETS[pippin]} ${TABLETS[merry]}"

say() { echo "$@" | tee -a "$OUT"; }

: > "$OUT"
say "# Overnight cluster battery — started $(date)"
say ""
say "Extends the finding that this cluster is TRANSFER-bound, not compute-bound."
say ""

# ---------- liveness ----------
say "## 0. Liveness"
say ""
say '```'
LIVE=""
for IP in $ALL; do
  R=$(timeout 30 ssh $O -p 8022 "$IP" "nproc" 2>/dev/null | tr -d '\r')
  if [ -n "$R" ]; then say "  $IP  ${R} cores  UP"; LIVE="$LIVE $IP"; else say "  $IP  DOWN"; fi
done
say '```'
say ""
NLIVE=$(echo $LIVE | wc -w)
[ "$NLIVE" -lt 2 ] && { say "**ABORT: fewer than 2 nodes alive.**"; exit 1; }

# ---------- 1. node-count scaling at fixed N ----------
say "## 1. Interconnect tax vs NODE COUNT (fixed N=2048)"
say ""
say "Does the tax grow with node count? Tonight: 2 nodes 80.8% -> 5 nodes 93.4%."
say ""
say '```'
for K5 in 1 2 3 4 5; do
  SUB=$(echo $LIVE | tr ' ' '\n' | head -$K5 | tr '\n' ',' | sed 's/,$//')
  [ "$(echo $SUB | tr ',' '\n' | wc -l)" -lt "$K5" ] && continue
  R=$(cd "$CL" && CLUSTER_NODES="$SUB" python master_n.py 2048 2>/dev/null | grep -E "effective|interconnect|sum of" | tr '\n' ' ')
  say "  nodes=$K5  $R"
done
say '```'
say ""

# ---------- 2. problem-size sweep on all nodes ----------
say "## 2. Interconnect tax vs PROBLEM SIZE (all live nodes)"
say ""
say "Compute scales N^3, transfer N^2 — bigger N should amortise the tax."
say ""
say '```'
for N in 1024 2048 3072 4096; do
  R=$(cd "$CL" && CLUSTER_NODES="$(echo $LIVE | tr ' ' ',')" python master_n.py $N 2>/dev/null | grep -E "effective|interconnect" | tr '\n' ' ')
  say "  N=$N  $R"
done
say '```'
say ""

# ---------- 3. sustained thermal profile ----------
say "## 3. Sustained thermal profile (fanless — how far does it fall?)"
say ""
say "Tonight: 5s bursts = 2.6 GFLOP/s/core; 25s sustained = 0.95-1.73."
say ""
say '```'
for S in 15 60 180; do
  rm -f "$CL"/burn_*.json
  for IP in $LIVE; do
    ( timeout $((S+180)) ssh $O -p 8022 "$IP" "python burn.py 1024 $S" > "$CL/burn_$IP.json" 2>/dev/null ) &
  done
  wait
  T=$(cd "$CL" && python burn_sum.py 2>/dev/null | grep "CLUSTER TOTAL")
  say "  duration=${S}s   $T"
done
say '```'
say ""

say "## Done $(date)"
say ""
say "Read alongside \`Journal\\2026-08-03-claude-tablets.md\`."
