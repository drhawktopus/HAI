#!/bin/bash
# Make rigby a TRANSPARENT L2 BRIDGE between the LAN (eno1) and mordecai (enp1s0).
#
# The requirement: mordecai must see the network directly through rigby. rigby is a pass-through,
# never a secondary DNS server.
#
# So: NO dnsmasq, NO DHCP server, NO NAT, NO ip_forward. mordecai gets its address, gateway and DNS
# straight from the router / the resolver, exactly as if it were plugged into the switch. rigby only
# moves frames. (This ADDS a bridge; it does not undo a router: no ip_forward, no dnsmasq/bind/dhcpd,
# no NAT rules were ever on this box.)
#
# ── THE THREE THINGS THAT COULD GO WRONG, AND WHAT IS DONE ABOUT EACH ────────────────────────────
#
# 1. eno1 IS THE ONLY WAY IN. Enslaving it drops an ssh session mid-apply, and this box carries
#    br0 -- THE TABLET USB SEGMENT -- so a stranded rigby takes the Fellowship's USB network with
#    it. NIC names change when a card is moved to another slot, which is why the config below
#    matches a pattern instead of naming interfaces. Mitigation: run detached, and SELF-HEAL --
#    if br-lan has no IPv4 within the window, restore the backup config and re-apply, so a
#    dropped session cannot strand the box.
#
# 2. A BRIDGE TAKES THE LOWEST MEMBER MAC, which may be a different NIC than the one whose lease
#    the router reserved. That hands the router a new MAC, gets a new lease, and moves the box's
#    address. Mitigation: pin the bridge's MAC to ENO_MAC, the MAC the reservation is built on.
#
# 3. STP IS ON, DELIBERATELY. Cables get moved. If both bridge ports ever land on the same switch,
#    an STP-less bridge is a broadcast storm that takes down the ENTIRE house network, not just
#    rigby. STP costs a few seconds of forwarding delay and removes that whole failure class. This
#    is the one setting worth the latency.
#
# ── WHAT IS DELIBERATELY NOT TOUCHED ────────────────────────────────────
#   br0 (the tablet wire; its prefix is the TABLET_WIRE_PREFIX setting) and every enx* USB interface -- the
#   tablet segment. The match pattern below
#   is "en[op]*", which catches eno1 and enp1s0 and CANNOT catch enx*. That is checked, not assumed.
set -u

TS=$(date +%Y%m%d-%H%M%S)
BACKUP=/etc/netplan-backup-$TS
CFG=/etc/netplan/01-wired-any-slot.yaml
# This box's own wired MAC comes from the environment (the launcher that starts it sets it);
# there is no default, because a MAC belongs to the box, not to the tool.
: "${ENO_MAC:?ENO_MAC must be set to the MAC whose lease this bridge must keep}"
HEAL_WAIT=60

echo "=== 0. state before ==="
ip -brief addr | grep -v '^lo' | sed 's/^/    /'
echo "    br0 members: $(ls /sys/class/net/br0/brif/ 2>/dev/null | tr '\n' ' ')"

echo
echo "=== 1. backup -> $BACKUP ==="
mkdir -p "$BACKUP" && cp -a /etc/netplan/. "$BACKUP"/ && ls -1 "$BACKUP" | sed 's/^/    /'

echo
echo "=== 2. write the bridge config ==="
cat > "$CFG" <<YAML
network:
  version: 2
  renderer: networkd

  ethernets:
    lanports:
      # Matches eno1 and enp1s0. Deliberately a PATTERN, not names: a slot move renames the card, and a
      # config that named the old name takes the network down. "en[op]*" cannot
      # match enx* (the USB tablet NICs), which must stay on br0.
      match:
        name: "en[op]*"
      dhcp4: no
      dhcp6: no
      optional: true

  bridges:
    br-lan:
      interfaces: [lanports]
      dhcp4: yes
      dhcp6: no
      optional: true
      # Pin the bridge's MAC to the interface whose lease the router has reserved. Without this the
      # bridge adopts the lowest member MAC (the card) and the box gets a new lease.
      macaddress: $ENO_MAC
      parameters:
        stp: true
        forward-delay: 4
YAML
chmod 600 "$CFG"
sed 's/^/    /' "$CFG"

echo
echo "=== 3. validate BEFORE applying ==="
if ! netplan generate 2>&1 | sed 's/^/    /'; then
  echo "    VALIDATION FAILED -- restoring backup, nothing applied"
  rm -f "$CFG"; cp -a "$BACKUP"/. /etc/netplan/; netplan generate
  echo "RESULT: ABORTED-SAFE"
  echo DONE
  exit 0
fi
echo "    netplan generate OK"

echo
echo "=== 4. apply (this drops the ssh session; that is expected) ==="
netplan apply 2>&1 | sed 's/^/    /' || true

echo
echo "=== 5. self-heal watch: does br-lan get an address within ${HEAL_WAIT}s? ==="
GOT=""
for i in $(seq 1 $HEAL_WAIT); do
  GOT=$(ip -4 -brief addr show br-lan 2>/dev/null | awk '{print $3}')
  [ -n "$GOT" ] && break
  sleep 1
done

if [ -z "$GOT" ]; then
  echo "    NO ADDRESS after ${HEAL_WAIT}s -- ROLLING BACK so the box is not stranded"
  cp -a "$BACKUP"/. /etc/netplan/
  rm -f "$CFG"
  cp -a "$BACKUP/$(basename $CFG)" "$CFG" 2>/dev/null || true
  netplan generate && netplan apply
  sleep 20
  echo "    after rollback:"
  ip -brief addr | grep -v '^lo' | sed 's/^/      /'
  echo "RESULT: ROLLED-BACK (the bridge did not come up; rigby's original config is restored)"
  echo DONE
  exit 0
fi
echo "    br-lan has $GOT"

echo
echo "=== 6. verify the shape ==="
echo "    br-lan members: $(ls /sys/class/net/br-lan/brif/ 2>/dev/null | tr '\n' ' ')"
echo "    br0    members: $(ls /sys/class/net/br0/brif/ 2>/dev/null | tr '\n' ' ')"
echo "    br0    addr   : $(ip -4 -brief addr show br0 2>/dev/null | awk '{print $3}')"
echo "    default route : $(ip route | awk '/^default/{print; exit}')"
echo "    stp           : $(cat /sys/class/net/br-lan/bridge/stp_state 2>/dev/null)"
echo "    ip_forward    : $(cat /proc/sys/net/ipv4/ip_forward)   (want 0 -- pass-through, not a router)"
for s in dnsmasq bind9 named isc-dhcp-server; do
  printf "    %-16s %s\n" "$s" "$(systemctl is-active $s 2>/dev/null)"
done

echo
echo "=== 7. ASSERT the things that must be true (a bridge that half-worked must not read as OK) ==="
FAIL=0
M=$(ls /sys/class/net/br-lan/brif/ 2>/dev/null | tr '\n' ' ')
case "$M" in *eno1*) ;; *) echo "    ✗ eno1 is NOT a bridge member"; FAIL=1;; esac
case "$M" in *enp1s0*) ;; *) echo "    ✗ enp1s0 is NOT a bridge member"; FAIL=1;; esac
case "$M" in *enx*) echo "    ✗ a USB tablet NIC got enslaved to br-lan -- WRONG BRIDGE"; FAIL=1;; esac
[ "$(ls /sys/class/net/br0/brif/ 2>/dev/null | wc -l)" = "5" ] || { echo "    ✗ br0 no longer has its 5 tablet NICs"; FAIL=1; }
[ "$(cat /proc/sys/net/ipv4/ip_forward)" = "0" ] || { echo "    ✗ ip_forward is on -- that is a router, not a pass-through"; FAIL=1; }
[ $FAIL -eq 0 ] && echo "    ✓ all assertions passed" || echo "    SOME ASSERTIONS FAILED (above)"

echo
echo "RESULT: BRIDGE-UP $GOT"
echo DONE
