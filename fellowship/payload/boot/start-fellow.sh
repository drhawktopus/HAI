#!/data/data/com.termux/files/usr/bin/sh
# Runs at boot via Termux:Boot, and supervises the fellow agent for the rest of the tablet's life.
#
# !! WHY A RESPAWN LOOP AND NOT RUNIT. The service dirs under $PREFIX/var/service are vestigial on
# these tablets: `runsvdir` is not running and `termux-services` is not installed, so a new service
# dir is never picked up (and `sshd` carries a `down` file yet runs, because start-sshd.sh launches
# it directly). The mechanism on these devices is Termux:Boot running a script, so this matches it.
#
# !! A DAEMON WITH NO RESTART POLICY HAS AN EXPIRY DATE, AND IT EXPIRES SILENTLY: the only thing
# that would report the outage is the thing that died. Hence: respawn on exit, forever.
#
# !! THIS DOES NOT SURVIVE THE TABLET LOSING WI-FI. After an access point bounce these devices may
# not re-associate on their own, and need someone to unlock the screen and re-select the network. A
# resident agent does not change that.

PREFIX=/data/data/com.termux/files/usr
HOME_DIR=/data/data/com.termux/files/home
LOG="$HOME_DIR/fellow.log"

# Both Android suppression mechanisms have to be defeated and neither substitutes for the other:
# the doze whitelist (set separately via adb) stops the app-standby freezer; this wake-lock stops
# the CPU sleeping under a running job.
termux-wake-lock 2>/dev/null

echo "$(date '+%Y-%m-%d %H:%M:%S') [boot] fellow supervisor starting" >> "$LOG"

while true; do
  echo "$(date '+%Y-%m-%d %H:%M:%S') [sup] launching agent" >> "$LOG"
  TABLET_FELLOW_NAME="${FELLOW_NAME:-$(cat "$HOME_DIR/.fellow_name" 2>/dev/null)}" \
    python "$HOME_DIR/tablet_foundry.py" --agent >> "$LOG" 2>&1
  rc=$?
  echo "$(date '+%Y-%m-%d %H:%M:%S') [sup] agent exited rc=$rc — respawning in 5s" >> "$LOG"
  # !! A refused start (no token) exits 2 immediately. Without this pause that becomes a hot loop
  # that fills the log and burns the battery rather than failing visibly.
  sleep 5
done
