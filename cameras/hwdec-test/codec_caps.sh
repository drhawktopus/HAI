#!/bin/bash
# What can each fellow ACTUALLY decode? Read from the device's own codec XML, not probed.
#
# THE FLEET IS MIXED SILICON AND `getprop` HIDES IT. These are PHH Treble GSIs, so
# ro.board.platform / ro.hardware report the GSI's generic values (mt6765 / mt8768) while the real
# vendor partition tells the truth: gandalf ships media_codecs_mediatek_video.xml, merry ships
# media_codecs_8937_v1.xml + media_codecs_8956.xml -- i.e. QUALCOMM MSM8937/8956.
#
# That explains three different failures from one binary:
#   gandalf  4K HEVC decodes fine
#   sam      4K HEVC -> getInputBuffer returns cap 0
#   merry    2K and 4K HEVC -> AMediaCodec_configure outright fails, D1 H.264 fine
#
# The device declares its limits: read them rather than probing each node by trial.
set -u
. "$(dirname "$0")/settings.sh"   # KEY, NVR, ADB and TABLETS, from the lab's settings
OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

for name in $(printf '%s\n' "${!TABLETS[@]}" | sort); do
  ip=${TABLETS[$name]}
  echo "================ $name ($ip) ================"
  ssh -p 8022 -i "$KEY" $OPTS "$ip" '
    echo -n "  android="; getprop ro.build.version.release
    echo -n "  vendor codec xml: "; ls /vendor/etc/media_codecs*video*.xml /vendor/etc/media_codecs_[0-9]*.xml 2>/dev/null | tr "\n" " "; echo
    echo -n "  soc hint: "; ls /vendor/etc/media_codecs* 2>/dev/null | grep -oE "mediatek|8937|8956|8953|qcom|exynos" | sort -u | tr "\n" " "; echo
    echo "  --- HEVC decoder limits as DECLARED ---"
    for f in /vendor/etc/media_codecs*.xml; do
      awk -v F="$f" "
        /<MediaCodec/ && /hevc/ && /ecoder|dec\./ {inblk=1; name=\$0; next}
        inblk && /Limit name=\"size\"/ {print \"    \" F \": \" \$0}
        inblk && /<\/MediaCodec>/ {inblk=0}
      " "$f" 2>/dev/null
    done | sed "s/  */ /g" | head -6
  ' 2>&1
  echo
done
