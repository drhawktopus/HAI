#!/bin/bash
# #38 stager, rigby (root, via run_remote.py):  rigby_boot.sh try_<kernel> | keep_<kernel> | default
#   (ONE argument: run_remote.py's --args allows only [A-Za-z0-9,._:=/-])
#   try <k>   boot <k> ONCE (grub-reboot): the configured default is untouched, so any reboot after a failure lands
#             back on the known-good kernel - blue/green for kernels
#   keep <k>  make <k> the default (GRUB_DEFAULT; /etc/default/grub backed up first), update-grub
#   default   reboot into the configured default (the rollback)
MODE="${1%%_*}"; K="${1#*_}"; [ "$MODE" = "$1" ] && K=""
# NOT `shutdown -r +N`: it puts up /run/nologin at once, so the stager's own ssh (a normal user) cannot read this
# script's DONE marker and crashes with the box half-way into its reboot. A systemd timer 45 s out does not.
reboot_soon() { systemd-run --on-active=45 --unit="hai-stager-reboot-$(date +%s)" /bin/systemctl reboot >/dev/null 2>&1 \
                  && echo "REBOOT_SCHEDULED=45s"; }
UUID=$(grep -oP "gnulinux-advanced-\K[0-9a-f-]+" /boot/grub/grub.cfg | head -n 1)
ENTRY="gnulinux-advanced-$UUID>gnulinux-$K-advanced-$UUID"
case "$MODE" in
  try)
    case "$K" in 6.8.*) ;; *) echo "ABORT: $K is not a 6.8 kernel (6.10+ breaks the 470 module)"; echo DONE; exit 1;; esac
    grep -q "gnulinux-$K-advanced-$UUID" /boot/grub/grub.cfg || { echo "ABORT: no grub entry for $K"; echo DONE; exit 1; }
    grep -q 'next_entry' /boot/grub/grub.cfg || { echo "ABORT: grub.cfg has no next_entry support"; echo DONE; exit 1; }
    grub-reboot "$ENTRY" && echo "ONE_TIME=$(grub-editenv list | grep -c "next_entry=.*$K")"
    reboot_soon ;;
  keep)
    cp -p /etc/default/grub "/etc/default/grub.bak-$(date +%Y%m%d-%H%M%S)-pre-$K"
    sed -i "s|^GRUB_DEFAULT=.*|GRUB_DEFAULT=\"$ENTRY\"|" /etc/default/grub
    update-grub > /tmp/rigby_update_grub.out 2>&1; echo "update_grub_rc=$?"
    grep '^GRUB_DEFAULT' /etc/default/grub ;;
  default)
    grub-editenv - unset next_entry
    reboot_soon ;;
  *) echo "usage: try|keep <kernel> | default" ;;
esac
echo DONE
