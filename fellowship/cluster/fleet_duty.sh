#!/bin/bash
# Fleet-wide duty-cycle A/B: does chunked work beat continuous on FANLESS
# hardware, and how much of the behaviour is per-tablet?
#
# A: continuous 20s          B: 4 x 5s bursts with 5s rests (same 20s of work)
# All nodes fire simultaneously so they share thermal + radio conditions.
# Results -> cluster/RESULTS-duty.md
export MSYS_NO_PATHCONV=1
. "$(dirname "$0")/../settings.sh"   # FS, KEY, ADB, TABLETS and SERIALS, from the lab's settings
CL="$FS/cluster"
OUT="$CL/RESULTS-duty.md"
K="$KEY"
O="-i $K -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=20 -o LogLevel=ERROR"
ALL="${TABLETS[gandalf]} ${TABLETS[frodo]} ${TABLETS[sam]} ${TABLETS[pippin]} ${TABLETS[merry]}"

declare -A NAME=(
 [${TABLETS[gandalf]}]="gandalf X6C6F 8c A12"
 [${TABLETS[frodo]}]="frodo X606F 8c A11"
 [${TABLETS[sam]}]="sam X606F 8c A11"
 [${TABLETS[pippin]}]="pippin X505F 4c A12"
 [${TABLETS[merry]}]="merry X505F 4c A12"
)

say() { echo "$@" | tee -a "$OUT"; }
: > "$OUT"
say "# Fleet duty-cycle A/B — $(date)"
say ""
say "Same total work (20s of compute), two shapes, all 5 nodes simultaneously."
say "Fanless hardware: testing whether thermal headroom beats clock speed."
say ""

for IP in $ALL; do scp $O -P 8022 "$CL/duty.py" "$IP:duty.py" >/dev/null 2>&1 & done
wait
say "staged duty.py on all nodes"
say ""

phase () {  # $1=label $2=burst $3=rest $4=cycles
  say "## $1"
  say '```'
  rm -f "$CL"/duty_*.json
  for IP in $ALL; do
    ( timeout 400 ssh $O -p 8022 "$IP" "python duty.py $2 $3 $4" > "$CL/duty_$IP.json" 2>/dev/null ) &
  done
  wait
  for IP in $ALL; do
    R=$(cat "$CL/duty_$IP.json" 2>/dev/null | tr -d '\r')
    say "  ${NAME[$IP]}"
    say "     ${R:-<no result>}"
  done
  say '```'
  say ""
}

phase "A. CONTINUOUS — one 20s burst, no rest" 20 0 1
say "cooling 45s..."
sleep 45
phase "B. DUTY-CYCLED — 4 x 5s bursts, 5s rests" 5 5 4

say "## Read"
say ""
say "* \`gflops_while_working\` = rate during burst time only (is the silicon faster when rested?)"
say "* \`gflops_per_wallclock\` = total work / total elapsed incl. rest (is chunking worth it in practice?)"
say "* \`per_cycle\` = rate of each burst in order — a falling series is thermal decay in real time."
say ""
say "Done $(date)"
