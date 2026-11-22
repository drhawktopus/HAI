#!/bin/bash
# #38 stager, rigby (root, via run_remote.py): install every waiting package, then report what a reboot would need.
# The noble-updates STUB-470 pin (/etc/apt/preferences.d/nvidia470, RUNBOOK-GT730) must exist or nothing is installed.
export DEBIAN_FRONTEND=noninteractive
echo "running: $(uname -r)"
if [ ! -f /etc/apt/preferences.d/nvidia470 ]; then
  echo "ABORT: the nvidia470 stub pin is missing - apt could swap the Kepler driver for 535 (RUNBOOK-GT730)"
  echo DONE; exit 1
fi
apt-get -o DPkg::Lock::Timeout=900 update -q 2>&1 | tail -n 2
apt-get -o DPkg::Lock::Timeout=900 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
        -y -q --with-new-pkgs upgrade > /tmp/rigby_apt_upgrade.out 2>&1
echo "apt_rc=$?"
tail -n 6 /tmp/rigby_apt_upgrade.out
echo "upgradable_left=$(apt list --upgradable 2>/dev/null | grep -vc '^Listing')"
NEWEST=$(ls /boot/vmlinuz-* | sed 's#/boot/vmlinuz-##' | sort -V | tail -n 1)
echo "NEWEST=$NEWEST"
echo "DKMS_NEWEST=$(dkms status 2>/dev/null | grep 'nvidia/470' | grep -c ", $NEWEST, .*installed")"
echo "EXTRA_NEWEST=$(dpkg -l "linux-modules-extra-$NEWEST" 2>/dev/null | grep -c '^ii')"
echo "NV470=$(dpkg -l nvidia-dkms-470 2>/dev/null | awk '/^ii/{print $3}')"
echo "REBOOT_REQUIRED=$([ -f /var/run/reboot-required ] && echo yes || echo no)"
echo DONE
