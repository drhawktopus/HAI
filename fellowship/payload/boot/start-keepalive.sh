#!/data/data/com.termux/files/usr/bin/sh
# Runs at boot via Termux:Boot. Keeps the WiFi link associated with steady outbound traffic.
#
# WHY: a tablet can leave the LAN on a regular cycle while fully awake -- absent from ARP and both
# transports for tens of seconds at a time, ping returning "Network is unreachable" -- with doze, the
# wake lock, battery and power settings all ruled out. Steady outbound traffic keeps it associated.
#
# !! IDENTITY IS A PID, NOT A PATTERN. A `pgrep -f` supervisor fails both ways: a pattern that cannot
# match (an anonymous `( ... ) &` subshell has no name) spawns a fresh loop on every check, and one
# that matches too much (`nohup setsid sh start-keepalive.sh` puts the name on several pids) makes
# every instance see its own ancestors as "others" and exit. This uses a lockfile holding one pid and
# tests it with `kill -0`, which asks the OS whether that exact process is alive; a stale lockfile
# from a killed process is detected rather than obeyed.
#
# !! NO SUPERVISOR AND NO BACKGROUND SUBSHELL. This script IS the loop: one process. Termux:Boot
# runs it once at boot and backgrounds it. If it ever dies the node returns to being reachable in
# windows, which is safe.
#
# !! DOES NOT TOUCH THE RADIO. `svc wifi disable` is persistent on these nodes and a failed
# re-enable strands the device until someone physically walks to it.
#
# !! The log doubles as the diagnostic: continuous timestamps with non-zero gw_rc means "no route";
# missing timestamps means "CPU suspended". That distinction needs no adb, which matters because
# the Termux uid cannot read dumpsys.

HOME_DIR=/data/data/com.termux/files/home
LOG="$HOME_DIR/keepalive.log"
LOCK="$HOME_DIR/.keepalive.pid"
# The gateway to keep pinging. Termux cannot read the routing table, so the provisioner writes it into
# ~/.keepalive.env (GW=<address>) when it installs this script; GW in the environment wins.
[ -z "${GW:-}" ] && [ -f "$HOME_DIR/.keepalive.env" ] && . "$HOME_DIR/.keepalive.env"
if [ -z "${GW:-}" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [start] no gateway known: write GW=<address> into $HOME_DIR/.keepalive.env" >> "$LOG"
    exit 1
fi

if [ -f "$LOCK" ]; then
    old=$(cat "$LOCK" 2>/dev/null)
    # kill -0 asks the OS "does this exact pid exist?" -- it sends no signal.
    if [ -n "$old" ] && kill -0 "$old" 2>/dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [start] pid $old already alive - exiting" >> "$LOG"
        exit 0
    fi
    echo "$(date '+%Y-%m-%d %H:%M:%S') [start] stale lock (pid $old gone) - taking over" >> "$LOG"
fi
echo $$ > "$LOCK"

# CPU wake lock stops the CPU sleeping; the doze whitelist (set over adb) stops the app-standby
# freezer. NEITHER keeps the radio associated -- that is what the traffic below is for, and it is
# the part that was actually missing on this node.
termux-wake-lock 2>/dev/null

echo "$(date '+%Y-%m-%d %H:%M:%S') [start] keepalive loop up, pid $$" >> "$LOG"

while true; do
    ping -c 1 -W 2 "$GW" >/dev/null 2>&1
    echo "$(date '+%H:%M:%S') gw_rc=$?" >> "$LOG"
    if [ "$(wc -l < "$LOG" 2>/dev/null || echo 0)" -gt 20000 ]; then
        tail -5000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
    fi
    sleep 10
done
