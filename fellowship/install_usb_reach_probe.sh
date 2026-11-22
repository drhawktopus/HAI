#!/bin/bash
# Install (or update) the USB reach probe on the tablet head, from the Lab tree the head sees on the shared drive.
# Run as root through run_remote.py, with the source and the destination as one argument:
#
#   python Lab\bin\run_remote.py <tablet head> Lab\fellowship\install_usb_reach_probe.sh 120 --args <src>:<dest>
#
# <src> is usb_reach_probe.py as the head sees it; <dest> is where the watchdog key's forced command runs it
# (default /usr/local/bin/usb_reach_probe.py). It keeps a backup of what it replaces, refuses unless the installed
# bytes equal the source's, gives the file its folder's owner, runs the probe once, and prints the authorized_keys
# line the watchdog's key needs. Prints DONE last (run_remote's marker).
set -u
fail() { echo "FAILED: $*"; echo DONE; exit 1; }
ARG="${1:-}"
case "$ARG" in
  *:*) SRC="${ARG%%:*}"; DEST="${ARG#*:}" ;;
  *)   SRC="$ARG"; DEST=/usr/local/bin/usb_reach_probe.py ;;
esac
[ -n "$SRC" ] && [ -f "$SRC" ] || fail "no probe source at '$SRC' (pass --args <src>:<dest>)"
[ -n "$DEST" ] || fail "no destination"
dir=$(dirname "$DEST")
[ -d "$dir" ] || fail "no folder $dir"
src=$(sha256sum "$SRC" | cut -d' ' -f1)
echo "source $src  $SRC"
if [ -e "$DEST" ]; then
  bak="$DEST.bak-$(date +%Y%m%d-%H%M%S)"
  cp -a "$DEST" "$bak" || fail "backup"
  echo "backup $bak"
fi
owner=$(stat -c %U "$dir"); group=$(stat -c %G "$dir")
install -m 0755 -o "$owner" -g "$group" "$SRC" "$DEST" || fail "install"
inst=$(sha256sum "$DEST" | cut -d' ' -f1)
echo "dest   $inst  $DEST ($owner:$group)"
[ "$inst" = "$src" ] || fail "installed bytes differ from the source"
echo "probe: $(python3 "$DEST" 2)"
echo "forced command for the watchdog's key in the head user's authorized_keys:"
echo "  command=\"/usr/bin/python3 $DEST\",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty <watchdog public key>"
echo DONE
