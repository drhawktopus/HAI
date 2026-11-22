#!/bin/bash
# Apply the documented GSI procedure (incl. the vbmeta step) to the three
# remaining tablets, then report a status-based verdict for each.
. "$(dirname "$0")/../settings.sh"   # FS, KEY, ADB, TABLETS and SERIALS, from the lab's settings
SC="$FS"
GSI="${1:?usage: flash_rest.sh <the GSI image>}"

for S in ${SERIALS[sam]} ${SERIALS[pippin]} ${SERIALS[merry]}; do
  echo ""
  echo "############################################################"
  echo "# $S"
  echo "############################################################"
  bash "$SC/flash/gsi_flash.sh" "$S" "$GSI"
  echo ""
  bash "$SC/flash/bootcheck.sh" "$S" 780
done

echo ""
echo "############ FINAL ############"
export MSYS_NO_PATHCONV=1
"$ADB" devices
"$SC/platform-tools/fastboot.exe" devices
