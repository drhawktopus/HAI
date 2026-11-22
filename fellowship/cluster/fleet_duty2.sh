#!/bin/bash
# Fleet duty-cycle A/B, RUN 2 — with contamination guards.
#
# Run 1 (08:42) was INVALID: a battery I "killed" with a LOCAL taskkill kept
# running on the tablets until 08:57 and overlapped both phases. .8 was found
# with 27 orphaned python procs and load 19.8.
#
# Guards added:
#   * kill remote python ON THE DEVICE before every phase (local kill != remote kill)
#   * assert py==0 on every node before measuring; abort the phase otherwise
#   * .177 EXCLUDED (7% battery — Android throttles hard at critical charge)
#
# Results -> cluster/RESULTS-duty2.md
export MSYS_NO_PATHCONV=1
. "$(dirname "$0")/../settings.sh"   # FS, KEY, ADB, TABLETS and SERIALS, from the lab's settings
CL="$FS/cluster"
OUT="$CL/RESULTS-duty2.md"
K="$KEY"
O="-i $K -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=25 -o LogLevel=ERROR"
ALL="${TABLETS[gandalf]} ${TABLETS[frodo]} ${TABLETS[sam]} ${TABLETS[merry]}"

declare -A NAME=(
 [${TABLETS[gandalf]}]="gandalf X6C6F 8c A12"
 [${TABLETS[frodo]}]="frodo X606F 8c A11"
 [${TABLETS[sam]}]="sam X606F 8c A11"
 [${TABLETS[merry]}]="merry X505F 4c A12"
)

say() { echo "$@" | tee -a "$OUT"; }
: > "$OUT"
say "# Fleet duty-cycle A/B — RUN 2 (clean) — $(date)"
say ""
say "Run 1 was contaminated by an orphaned test battery. Guards now enforce a quiet fleet."
say ".177 excluded: 7% battery."
say ""

quiet () {
  local bad=0
  for IP in $ALL; do
    timeout 60 ssh $O -p 8022 "$IP" "pkill -9 python 2>/dev/null; sleep 1; true" >/dev/null 2>&1
    local P=$(timeout 40 ssh $O -p 8022 "$IP" "pgrep -c python 2>/dev/null" 2>/dev/null | tr -d '\r')
    [ -z "$P" ] && P="?"
    say "   quiet-check $IP python=$P"
    [ "$P" != "0" ] && bad=1
  done
  return $bad
}

phase () {  # label burst rest cycles
  say "## $1"
  say '```'
  if ! quiet; then say "   !! ABORT: fleet not quiet"; say '```'; return; fi
  rm -f "$CL"/d2_*.json
  for IP in $ALL; do
    ( timeout 400 ssh $O -p 8022 "$IP" "python duty.py $2 $3 $4" > "$CL/d2_$IP.json" 2>/dev/null ) &
  done
  wait
  for IP in $ALL; do
    say "  ${NAME[$IP]}"
    say "     $(cat "$CL/d2_$IP.json" 2>/dev/null | tr -d '\r' || echo '<none>')"
  done
  say '```'
  say ""
}

phase "A. CONTINUOUS — one 20s burst" 20 0 1
say "cooling 60s..."
sleep 60
phase "B. DUTY-CYCLED — 4 x 5s bursts, 5s rests" 5 5 4

say "## Cleanup"
say '```'
quiet
say '```'
say ""
say "Done $(date)"
