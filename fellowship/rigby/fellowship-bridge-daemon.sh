#!/bin/bash
# Keep every tablet enx enslaved in br0. Idempotent; reconciles on each link event.
# The USB segment is FELLOWSHIP_USB_SEGMENT (the tablet head takes .1 on it), default 10.55.0.
SEG="${FELLOWSHIP_USB_SEGMENT:-10.55.0}"
ensure_br0() {
  ip link show br0 >/dev/null 2>&1 && return
  ip link add name br0 type bridge
  ip addr replace "$SEG.1/24" dev br0
  ip link set br0 up
}
bridge_one() {
  local e="$1"
  [ -e "/sys/class/net/br0/brif/$e" ] && return
  ip link set "$e" up
  ip link set "$e" master br0 || return
  ethtool -K "$e" sg off gso off tx off >/dev/null 2>&1
  ip neigh flush dev br0 >/dev/null 2>&1
  logger -t fellowship-bridge "enslaved $e into br0"
}
reconcile() {
  ensure_br0
  for e in $(ls /sys/class/net/ 2>/dev/null | grep '^enx'); do bridge_one "$e"; done
}
reconcile
# React to link add/remove/change; reconcile is cheap and idempotent.
ip monitor link | while read -r _; do reconcile; done
