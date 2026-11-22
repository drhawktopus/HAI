#!/bin/bash
# Reflash one tablet. Checks every step; never chains past a failure.
# usage: reflash.sh <serial> <image>
export MSYS_NO_PATHCONV=1
. "$(dirname "$0")/../settings.sh"   # FS, KEY, ADB, TABLETS and SERIALS, from the lab's settings
SC="$FS"
FB34="$SC/platform-tools-34/fastboot.exe"   # handles large images (pt37 = std::bad_alloc)
FB37="$SC/platform-tools/fastboot.exe"        # handles -w      (pt34 = std::out_of_range)
S="$1"
IMG="$2"

echo "=== $S <- $(basename "$IMG") ==="
date

if ! "$FB37" devices | grep -q "^$S"; then
  echo "ABORT: $S not in fastboot"; exit 1
fi
echo "unlocked: $(timeout 20 "$FB37" -s "$S" getvar unlocked 2>&1 | head -1)"

echo ">>> flash system"
timeout 540 "$FB34" -s "$S" flash system "$IMG" 2>&1 | tail -2
RC=${PIPESTATUS[0]}
echo "FLASH_RC=$RC"
if [ "$RC" -ne 0 ]; then
  echo "ABORT: flash failed, leaving device in fastboot (recoverable, nothing erased)"
  exit 1
fi

echo ">>> wipe userdata"
timeout 180 "$FB37" -s "$S" -w 2>&1 | tail -2
echo "WIPE_RC=${PIPESTATUS[0]}"

echo ">>> reboot"
timeout 60 "$FB37" -s "$S" reboot 2>&1 | tail -1
date

echo ">>> watching for boot (up to ~4 min)"
for i in $(seq 1 12); do
  sleep 20
  N=$(powershell -NoProfile -Command "(Get-PnpDevice | Where-Object { \$_.InstanceId -like '*$S*' -and \$_.Status -ne 'Unknown' } | ForEach-Object { \$_.FriendlyName }) -join ' ; '")
  echo "[$(date +%H:%M:%S)] ${N:-<no usb>}"
  if "$ADB" devices | grep -q "^$S[[:space:]]*device"; then
    echo "BOOTED — adb online"
    break
  fi
done
"$ADB" devices
