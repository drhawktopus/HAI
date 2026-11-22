#!/bin/bash
# rigby (root, via run_remote.py), READ-ONLY: Intel 7-series USB port routing on the xHCI controller 00:14.0.
#   XUSB2PR  (0xD0) = which USB 2.0 ports are routed to xHCI (bit set) instead of EHCI
#   XUSB2PRM (0xD4) = which of them the BIOS allows to be switched
#   USB3_PSSEN (0xD8) = SuperSpeed enabled per port;  USB3PRM (0xDC) = which the BIOS allows
for r in d0 d4 d8 dc; do echo "REG_$r=0x$(setpci -s 00:14.0 ${r}.l)"; done
echo "XHCI_ROOT_PORTS=$(ls -d /sys/bus/usb/devices/usb3/3-0:1.0/usb3-port* 2>/dev/null | wc -l)"
dmesg | grep -iE 'xhci_hcd 0000:00:14.0' | head -n 8 | sed 's/^/DMESG /'
echo DONE
