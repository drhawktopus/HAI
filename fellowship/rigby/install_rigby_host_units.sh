#!/bin/bash
# Install the tablet head's host services from this folder: the USB-segment bridge daemon (fellowship-bridge), the
# tablets' model store (fellowship-models) and the share mounter Docker waits for (rigby-shares). Run as root on the
# tablet head with this folder copied there, e.g. through run_remote.py after a tar copy of the folder:
#
#     install_rigby_host_units.sh <folder>:<models user>:<models port>:<models dir>:<share>[,<share>...]
#
# Writes /usr/local/sbin/{fellowship-bridge-daemon.sh,rigby-mount-shares.sh}, the three units (the two templates
# filled in) and Docker's drop-in that orders it after the mounter, enables the units, and restarts each service whose
# files changed (never Docker). All three are safe to restart: the bridge
# reconciles br0 and keeps it, the mounter only re-checks what is mounted, the model store is a plain file server.
# Prints each file's verdict (same / installed) and DONE last.
set -euo pipefail
ARG="${1:?usage: install_rigby_host_units.sh <folder>:<models user>:<models port>:<models dir>:<share>[,<share>...]}"
IFS=: read -r SRC MUSER MPORT MDIR SHARES <<< "$ARG"
[ -n "$SRC" ] && [ -n "$MUSER" ] && [ -n "$MPORT" ] && [ -n "$MDIR" ] && [ -n "$SHARES" ] \
  || { echo "ABORT: usage install_rigby_host_units.sh <folder>:<models user>:<models port>:<models dir>:<shares>"; echo DONE; exit 1; }
id "$MUSER" >/dev/null 2>&1 || { echo "ABORT: no user $MUSER"; echo DONE; exit 1; }
STAGE=$(mktemp -d)
cp "$SRC/fellowship-bridge-daemon.sh" "$SRC/rigby-mount-shares.sh" "$SRC/fellowship-bridge.service" "$STAGE/"
sed -e "s|@PORT@|$MPORT|" -e "s|@DIR@|$MDIR|" -e "s|@USER@|$MUSER|" "$SRC/fellowship-models.service.in" > "$STAGE/fellowship-models.service"
sed -e "s|@SHARES@|${SHARES//,/ }|" "$SRC/rigby-shares.service.in" > "$STAGE/rigby-shares.service"
changed=""
put() {   # <staged file> <target> <mode> <service it belongs to>
  if cmp -s "$1" "$2"; then
    echo "same      $2"
  else
    install -m "$3" "$1" "$2"
    echo "installed $2"
    changed="$changed $4"
  fi
}
put "$STAGE/fellowship-bridge-daemon.sh" /usr/local/sbin/fellowship-bridge-daemon.sh 0755 fellowship-bridge
put "$STAGE/rigby-mount-shares.sh" /usr/local/sbin/rigby-mount-shares.sh 0755 rigby-shares
put "$STAGE/fellowship-bridge.service" /etc/systemd/system/fellowship-bridge.service 0644 fellowship-bridge
put "$STAGE/fellowship-models.service" /etc/systemd/system/fellowship-models.service 0644 fellowship-models
put "$STAGE/rigby-shares.service" /etc/systemd/system/rigby-shares.service 0644 rigby-shares
# Docker starts after the mounter (ordering only: a changed drop-in needs a reload, never a Docker restart)
mkdir -p /etc/systemd/system/docker.service.d
put "$SRC/docker-wait-for-shares.conf" /etc/systemd/system/docker.service.d/wait-for-shares.conf 0644 docker-order
rm -rf "$STAGE"
systemctl daemon-reload
for u in fellowship-bridge fellowship-models rigby-shares; do
  systemctl enable "$u.service" >/dev/null 2>&1
  case " $changed " in *" $u "*) systemctl restart "$u.service"; echo "restarted $u";; esac
  echo "$u: $(systemctl is-active "$u.service") / $(systemctl is-enabled "$u.service")"
done
echo DONE
