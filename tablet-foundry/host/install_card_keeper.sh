#!/bin/bash
# Install (or update) rigby's CARD KEEPER on rigby's HOST, from the GATED Tolkien image.
#
#   on the tablet head, from your checkout:  sudo bash tablet-foundry/host/install_card_keeper.sh
#   or piped from another machine:  python "<lab tools>\run_remote.py" <tablet head> "<this folder>\install_card_keeper.sh" 300 --args <the tablet-foundry folder as the tablet head sees it>
#
# Order: deploy rigby_card_keeper.py through fleet_deploy.py FIRST (that is what gates it and puts it in the image);
# this script then copies the image's bytes, refuses unless they equal the source's (the folder named as the first
# argument, else the tablet-foundry folder this script sits in),
# lets the keeper write its own systemd units, enables the timer, and proves it with a --dry decision.
# Backups of anything replaced: /root/rigby-card-keeper-bak-<stamp>/. Prints DONE last (run_remote's marker).
set -u
BIN=/usr/local/bin/rigby_card_keeper.py
UNITS=/etc/systemd/system
SRC_DIR=${1:-$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)}
MIRROR=$SRC_DIR/rigby_card_keeper.py
[ -f "$MIRROR" ] || { echo "FAILED: no rigby_card_keeper.py in '$SRC_DIR': name the tablet-foundry folder as the first argument"; echo DONE; exit 1; }
stamp=$(date +%Y%m%d-%H%M%S)
bak=/root/rigby-card-keeper-bak-$stamp
fail() { echo "FAILED: $*"; echo DONE; exit 1; }

tmp=$(mktemp)
docker cp tablet-foundry:/app/rigby_card_keeper.py "$tmp" || fail "cannot copy the keeper out of tablet-foundry"
img=$(sha256sum "$tmp" | cut -d' ' -f1)
mir=$(sha256sum "$MIRROR" | cut -d' ' -f1)
echo "image  $img"
echo "mirror $mir"
[ "$img" = "$mir" ] || fail "the image's keeper is not the mirror's - deploy it through fleet_deploy.py first"

mkdir -p "$bak"
for f in "$BIN" "$UNITS/rigby-card-keeper.service" "$UNITS/rigby-card-keeper.timer"; do
  [ -e "$f" ] && cp -a "$f" "$bak/"
done
echo "backups: $(ls -A "$bak" | tr '\n' ' ')(in $bak)"

install -m 0755 -o root -g root "$tmp" "$BIN" || fail "install"
rm -f "$tmp"
host=$(sha256sum "$BIN" | cut -d' ' -f1)
echo "host   $host"
[ "$host" = "$img" ] || fail "installed bytes differ"

python3 "$BIN" --write-units "$UNITS" || fail "write-units"
systemctl daemon-reload
systemctl enable --now rigby-card-keeper.timer || fail "enable timer"
systemctl list-timers rigby-card-keeper.timer --no-pager
echo "nvidia-smi: $(command -v nvidia-smi)"
nvidia-smi -L
( set -a; . /etc/default/tolkien-host 2>/dev/null; set +a; python3 "$BIN" --dry )   # the units load that file too
echo DONE
