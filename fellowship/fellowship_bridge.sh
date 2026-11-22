#!/bin/bash
# Bring up the tablet head's br0 and enslave every USB tablet interface. RUN AS ROOT:
#
#     sudo bash fellowship_bridge.sh 10.55.0:11,12,13      # the segment, then the tablets' octets on it
#
# The argument names the segment and the octets to verify. usbnet_up.py passes its own roster, so the
# two halves cannot disagree about how many tablets exist; it does the tablet half over WiFi adb and
# needs no root. Only THIS half needs root on the head.
#
# !! ENSLAVE BY GLOB, NEVER BY NAME. Android's RNDIS gadget picks a new random MAC every time it
# is enabled, so the host interface is a different enx<mac> on every boot/replug. Anything that
# remembers "this enx is that tablet" bridges the wrong set and reports success.
#
# !! ASSERT THE COUNT. br0 keeps its OLD members happily, so a returning node sits outside a
# bridge that looks healthy -- it has a correct address and a correct route and is still
# unreachable. "Enslaved something" and "enslaved everything" are identical at the output unless
# the count is checked.
set -u

# "<segment>:<octets>", or the octets alone with the segment from FELLOWSHIP_USB_SEGMENT (default 10.55.0).
ARG="${1:-}"
case "$ARG" in
  *:*) SEG="${ARG%%:*}"; OCTETS="${ARG#*:}" ;;
  *)   SEG="${FELLOWSHIP_USB_SEGMENT:-10.55.0}"; OCTETS="$ARG" ;;
esac
if [ -z "$OCTETS" ]; then
  echo "!! name the tablets' octets on the segment:  sudo bash $0 10.55.0:11,12,13"
  echo "DONE"
  exit 2
fi
IFS=',' read -ra WANT_OCTETS <<< "$OCTETS"
WANT_TABLETS=${#WANT_OCTETS[@]}

if [ "$(id -u)" -ne 0 ]; then
  echo "!! must run as root:  sudo bash $0"
  echo "DONE"
  exit 2
fi

WANT=$(ls /sys/class/net | grep -cE '^(enx|usb)')
echo "USB interfaces present: $WANT"
if [ "$WANT" -eq 0 ]; then
  echo "!! ZERO usb interfaces present. That is NOTHING TO DO, not SET UP."
  echo "   The tablets' rndis0 may be down -- run usbnet_up.py --apply from the box that holds adb first."
  echo "DONE"
  exit 1
fi

ip link add name br0 type bridge 2>/dev/null || true
ip addr replace "$SEG.1/24" dev br0
ip link set br0 up

DEADLINE=$((SECONDS + 45))
while :; do
  for i in $(ls /sys/class/net | grep -E '^(enx|usb)'); do
    ip link set "$i" master br0 2>/dev/null
    ip link set "$i" up 2>/dev/null
  done
  n=$(bridge link | grep -c 'master br0')
  if [ "$n" -ge "$WANT" ]; then
    ip neigh flush dev br0 2>/dev/null
    echo "BRIDGE OK: $n of $WANT ports on br0, ARP flushed"
    echo "-- reachability (the only proof that matters):"
    # !! NEVER `ping -c1 -W1` here. ARP was just flushed, and -W1 covers resolution AND the reply,
    # so the first packet to a cold neighbour times out and a healthy node reports DOWN.
    up=0
    for o in "${WANT_OCTETS[@]}"; do
      a="$SEG.$o"
      loss=$(ping -c3 -W2 "$a" 2>/dev/null | sed -n 's/.* \([0-9]*\)% packet loss.*/\1/p')
      if [ -n "$loss" ] && [ "$loss" -lt 100 ]; then
        echo "   $a UP (${loss}% loss)"; up=$((up+1))
      else
        echo "   $a DOWN (${loss:-no reply}% loss)"
      fi
    done
    echo "RESULT: $up of $WANT_TABLETS tablets answer on the segment"
    if [ "$up" -ne "$WANT_TABLETS" ]; then
      echo "!! PARTIAL SEGMENT -- not a comparable test. Do not measure on it."
      echo "DONE"   # marker for Lab\bin\run_remote.py -- reached a VERDICT, not necessarily a pass
      exit 1
    fi
    echo "DONE"
    exit 0
  fi
  if [ "$SECONDS" -ge "$DEADLINE" ]; then
    echo "BRIDGE FAILED: only $n of $WANT ports on br0 after 45s"
    ls /sys/class/net | grep -E '^(enx|usb)' | sed 's/^/   present: /'
    bridge link | sed 's/^/   bridged: /'
    echo "DONE"
    exit 1
  fi
  sleep 2
done
