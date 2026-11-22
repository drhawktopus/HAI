#!/bin/bash
# rigby (root, via run_remote.py): rigby_usb_scheme.sh old | default | show
# usbcore's enumeration scheme - the kernel's own workaround for "device descriptor read/64, error -71" /
# "device not accepting address" (old_scheme_first = try the old, GET_DESCRIPTOR-first scheme before the new one).
# Runtime only (lost at reboot) - nothing persists until a test proves it.
P=/sys/module/usbcore/parameters
case "$1" in
  old)     echo Y > $P/old_scheme_first; echo Y > $P/use_both_schemes ;;
  default) echo N > $P/old_scheme_first; echo Y > $P/use_both_schemes ;;
esac
echo "old_scheme_first=$(cat $P/old_scheme_first) use_both_schemes=$(cat $P/use_both_schemes) initial_descriptor_timeout=$(cat $P/initial_descriptor_timeout 2>/dev/null)"
echo DONE
