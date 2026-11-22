#!/bin/bash
# Documented GSI procedure for Lenovo TB-X606X/X606V/X606F, fastboot-only method.
# Source: XDA "[GUIDE][ROM][GSI] Android 13 Stable for Lenovo TB-X606X/X606V/X606F
#         (w/Out TWRP Recovery)" thread 4759188, corroborated by the TWRP thread
#         (4222887) and the LOS21 guide (4731020).
#
#   erase system / userdata / cache
#   fastboot --disable-verity --disable-verification flash vbmeta vbmeta.img
#   fastboot flash system <gsi>
#   fastboot -w
#   fastboot reboot
#   FIRST BOOT TAKES 5-10 MINUTES. Do not judge it before then.
#
# Only deviation: platform-tools 34 flashes the system image, platform-tools 37
# runs -w. Each crashes on the other operation (std::bad_alloc / std::out_of_range).
#
# usage: gsi_flash.sh <serial> <gsi.img>

export MSYS_NO_PATHCONV=1
# This folder's parent, the fellowship tree; under Git Bash in the drive-letter form adb.exe needs.
LAB="$(cd "$(dirname "$0")/.." && { pwd -W 2>/dev/null || pwd; })"
FB37="$LAB/platform-tools/fastboot.exe"       # 37.0.1 -- runs -w      (34.0.4 dies: std::out_of_range)
FB34="$LAB/platform-tools-34/fastboot.exe"    # 34.0.4 -- flashes big  (37.0.1 dies: std::bad_alloc)
VBM="$LAB/avb/vbmeta_disabled.img"
S="$1"
IMG="$2"

step() { echo ""; echo ">>> $*"; }

echo "=== $S <- $(basename "$IMG") + empty vbmeta ==="
date
"$FB37" devices | grep -q "^$S" || { echo "ABORT: $S not in fastboot"; exit 1; }

step "erase system";   timeout 200 "$FB37" -s "$S" erase system   2>&1 | tail -1
step "erase userdata"; timeout 300 "$FB37" -s "$S" erase userdata 2>&1 | tail -1
step "erase cache";    timeout 200 "$FB37" -s "$S" erase cache    2>&1 | tail -1

step "flash vbmeta (disable-verity, disable-verification)"
timeout 120 "$FB37" -s "$S" --disable-verity --disable-verification flash vbmeta "$VBM" 2>&1 | tail -2
RC=${PIPESTATUS[0]}; echo "VBMETA_RC=$RC"
[ "$RC" -ne 0 ] && { echo "ABORT: vbmeta flash failed"; exit 1; }

step "flash system"
timeout 540 "$FB34" -s "$S" flash system "$IMG" 2>&1 | tail -2
RC=${PIPESTATUS[0]}; echo "SYSTEM_RC=$RC"
[ "$RC" -ne 0 ] && { echo "ABORT: system flash failed (device still in fastboot)"; exit 1; }

step "-w"; timeout 240 "$FB37" -s "$S" -w 2>&1 | tail -2; echo "W_RC=${PIPESTATUS[0]}"

step "reboot"; timeout 60 "$FB37" -s "$S" reboot 2>&1 | tail -1
date
echo ""
echo "DONE. First boot takes 5-10 min per the guide. Not polling."
