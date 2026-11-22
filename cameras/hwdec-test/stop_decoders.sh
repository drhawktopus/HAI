#!/bin/bash
# Stop every MANUALLY-started decoder on the fleet and ASSERT it is zero.
#
# WHY THIS EXISTS: `run_serve_port.sh` uses `setsid`, so the decoders it starts survive as PPID-1
# orphans and nothing reaps them. An orphan on port 8803 keeps the AGENT-SUPERVISED decoder from
# binding: it logs `cannot listen on 8803: Address already in use` every 20 s, escalating to 300 s,
# which reads as the supervisor failing.
#
# THE AGENT OWNS THE DECODER. On any node with a ~/hwdec.json assignment, tablet_foundry
# spawns and supervises hwdec on the assigned port. A manual decoder on that port WILL collide.
# Test on an unassigned node, or on a different port, or stop the agent first -- do not race it.
#
# `pkill -x` (exact process NAME), never `pkill -f <pattern>`: a `-f` pattern matches the killing
# command's OWN command line, so it can kill the ssh session issuing it, even when the pattern is
# bracketed and the literal word appears elsewhere on the line (`echo ASSERT hwdec=...`).
# Exact-name matching cannot do that.
set -u

. "$(dirname "$0")/settings.sh"   # KEY, NVR, ADB and TABLETS, from the lab's settings
OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
NODES=""; for n in $(printf '%s\n' "${!TABLETS[@]}" | sort); do NODES="$NODES $n:${TABLETS[$n]}"; done

# TWO TRANSPORTS, BECAUSE EACH ONE ALONE IS BLIND IN A DIFFERENT WAY, measured on a real orphan
# (a setsid'd decoder from a previous ssh session):
#
#   from TERMUX (ssh):  `ps` and `pgrep -x hwdec` BOTH report NOTHING -- while the process is
#                       alive and holding port 8803. `kill -9 <pid>` on it SUCCEEDS.
#   from ADB (uid 2000): `ps -A` shows it plainly -- but `kill` returns
#                       "Operation not permitted" (it belongs to the Termux app's uid).
#
# So: ENUMERATE OVER ADB, KILL OVER SSH. Asking Termux "are there orphans?" asks the one
# transport that structurally cannot answer: it returns a confident zero.

FAIL=0
for entry in $NODES; do
  name="${entry%%:*}"; ip="${entry##*:}"
  printf "%-8s " "$name"
  "$ADB" connect "$ip:5555" >/dev/null 2>&1

  # THE FILTER IS "any hwdec that is not the CURRENT agent's child", not "PPID == 1": a stale
  # decoder can be the child of a PREVIOUS agent generation, with a non-1 parent, and still hold the
  # port.
  agent=$("$ADB" -s "$ip:5555" shell "ps -A -o PID,ARGS 2>/dev/null | awk '/tablet_foundry.py --agent/ && !/awk/ {print \$1; exit}'" 2>/dev/null | tr -d '\r')
  agent="${agent:-0}"
  pids=$("$ADB" -s "$ip:5555" shell "ps -A -o PID,PPID,NAME 2>/dev/null | awk -v A=$agent '\$3==\"hwdec\" && \$2!=A {print \$1}'" 2>/dev/null | tr -d '\r')
  if [ -z "$pids" ]; then echo "clear (no orphans)"; continue; fi

  for p in $pids; do
    ssh -p 8022 -i "$KEY" $OPTS "$ip" "kill -9 $p" >/dev/null 2>&1
  done
  sleep 2

  # Re-verify with the SAME rule the kill used -- a verification that asks a narrower question than
  # the action would report success while survivors remain.
  left=$("$ADB" -s "$ip:5555" shell "ps -A -o PID,PPID,NAME 2>/dev/null | awk -v A=$agent '\$3==\"hwdec\" && \$2!=A {print \$1}'" 2>/dev/null | tr -d '\r' | tr '\n' ' ')
  if [ -z "$(echo $left)" ]; then
    echo "cleared: $(echo $pids | tr '\n' ' ')"
  else
    # Never report a clean sweep we did not verify -- that is how the next arm gets contaminated.
    echo "STILL RUNNING ($left) -- next test on this node would be VOID"
    FAIL=1
  fi
done

# The agent respawns its OWN decoder within its backoff window on assigned nodes. That is
# correct and expected; this script clears MANUAL ones so the agent can bind.
echo
echo "note: nodes with a ~/hwdec.json assignment will have their agent-supervised decoder back"
echo "      within ~20s. That is the supervisor doing its job, not a leak."
exit $FAIL
