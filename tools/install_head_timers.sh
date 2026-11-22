#!/bin/bash
# Installs the head's periodic sweeps on the machine that runs the head (run there with sudo):
#   sudo bash <this folder>/install_head_timers.sh
# Copies head-timers/head_sweep.sh to /usr/local/bin and writes the units:
#   hai-claim-sweep.timer    every 4 hours at :10   (a sweep with no changed document makes no model call)
#   hai-photo-archive.timer  daily at 05:20
# Persistent=true on both: a sweep missed while the machine was down runs at the next boot.
# Settings live in /etc/default/hai-head (written with the defaults only when absent; never overwritten):
#   HAI_CONTAINER   the head's container name        (default hai)
#   HAI_HEAD_URL    the head's address from this box (default http://127.0.0.1:8099)
#   HAI_TIMER_TZ    the time zone the schedules read (default: the machine's own)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
install -m 0755 "$HERE/head-timers/head_sweep.sh" /usr/local/bin/head_sweep.sh
if [ ! -e /etc/default/hai-head ]; then
  printf 'HAI_CONTAINER=hai\nHAI_HEAD_URL=http://127.0.0.1:8099\n# HAI_TIMER_TZ=Area/City\n' > /etc/default/hai-head
fi
. /etc/default/hai-head
TZS=${HAI_TIMER_TZ:+ $HAI_TIMER_TZ}

unit() {   # name, description, door
  printf '[Unit]\nDescription=%s\nAfter=network-online.target docker.service\n\n[Service]\nType=oneshot\nUser=root\nExecStart=/usr/local/bin/head_sweep.sh %s\n' \
    "$2" "$3" > "/etc/systemd/system/$1.service"
}
timer() {  # name, description, OnCalendar
  printf '[Unit]\nDescription=%s\n\n[Timer]\nOnCalendar=%s\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n' \
    "$2" "$3" > "/etc/systemd/system/$1.timer"
}
unit hai-claim-sweep "Review the documentation claims that changed since the last sweep" claims/sweep
timer hai-claim-sweep "Documentation claim review, every 4 hours" "*-*-* 00,04,08,12,16,20:10:00$TZS"
unit hai-photo-archive "Archived chats' photos become text descriptions" photos/archive-sweep
timer hai-photo-archive "Daily photo archive sweep" "*-*-* 05:20:00$TZS"
systemctl daemon-reload
systemctl enable --now hai-claim-sweep.timer hai-photo-archive.timer
systemctl list-timers hai-claim-sweep.timer hai-photo-archive.timer --no-pager | head -4
