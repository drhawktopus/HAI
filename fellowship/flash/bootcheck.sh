#!/bin/bash
# Status-based boot detector. Runs in the background and
# EXITS THE MOMENT IT HAS AN ANSWER rather than running out a timer.
#
# Verdicts:
#   BOOTED    - adb reports the serial as 'device'            (definitive)
#   BOOTLOOP  - >=3 preloader appear/disappear cycles         (definitive)
#   NO_SIGNAL - never enumerated anything at all              (dead / not powered)
#   SLOW      - hit the cap with partial signal, no verdict
#
# usage: bootcheck.sh <serial> [max_seconds]

export MSYS_NO_PATHCONV=1
# This folder's parent, the fellowship tree; under Git Bash in the drive-letter form adb.exe needs.
LAB="$(cd "$(dirname "$0")/.." && { pwd -W 2>/dev/null || pwd; })"
ADB="$LAB/platform-tools/adb.exe"
S="${1:?serial}"
MAX="${2:-900}"

start=$(date +%s)
cycles=0
prev_pre=0
seen_any=0

echo "watching $S (cap ${MAX}s) — exits on verdict"

while :; do
  now=$(date +%s)
  [ $((now - start)) -ge "$MAX" ] && break

  # definitive success
  if "$ADB" devices 2>/dev/null | grep -qE "^$S[[:space:]]+device"; then
    echo "VERDICT=BOOTED after $((now - start))s"
    "$ADB" -s "$S" shell getprop ro.lineage.version 2>/dev/null | tr -d '\r'
    exit 0
  fi

  # preloader presence = device restarting its boot chain
  pre=$(powershell -NoProfile -Command "@(Get-PnpDevice | Where-Object { \$_.Status -ne 'Unknown' -and \$_.InstanceId.Contains('PID_2000') }).Count" 2>/dev/null | tr -d '\r ')
  [ -z "$pre" ] && pre=0
  [ "$pre" -gt 0 ] && seen_any=1

  if [ "$prev_pre" -eq 0 ] && [ "$pre" -gt 0 ]; then
    cycles=$((cycles + 1))
    echo "  [$(date +%H:%M:%S)] preloader cycle #$cycles"
    if [ "$cycles" -ge 3 ]; then
      echo "VERDICT=BOOTLOOP after $((now - start))s ($cycles cycles)"
      exit 0
    fi
  fi
  prev_pre=$pre
  sleep 5
done

if [ "$seen_any" -eq 0 ]; then
  echo "VERDICT=NO_SIGNAL after ${MAX}s (never enumerated)"
else
  echo "VERDICT=SLOW after ${MAX}s ($cycles preloader cycles, never reached adb)"
fi
