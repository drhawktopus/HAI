#!/bin/bash
# Install wifi_watchdog.sh onto tablet nodes. Idempotent; safe to re-run.
#
# Provisioning is a HUMAN act, deliberately not something the re-armer does: `absent` means the
# script was never installed, and a restart cannot fix that. The re-armer says so and stops.
#
# Traps this handles:
#   * `adb push` fails on these nodes with `remote fchown failed` -> pipe via `cat` instead.
#   * Android's sh breaks on CRLF and the Write tool emits it -> strip CR before sending.
#   * Git Bash rewrites /data/local/tmp/... into C:/Program Files/Git/data/... -> MSYS_NO_PATHCONV=1.
#   * Verify by sha256 on BOTH sides, on the bytes, not by trusting the copy.
#   * Never `pkill -f` a name that appears in this script's own command line -- kill by PID.
set -u
cd "$(dirname "$0")"
PT="${ADB:-../platform-tools/adb.exe}"   # adb; the ADB variable overrides
MODE="${MODE:-report}"

declare -A NODES
eval "NODES=( $(python ../roster.py --sh-lan) )"   # the roster's network tablets (FELLOWSHIP_ROSTER)
TARGETS="${*:-}"
if [ -z "$TARGETS" ]; then
    echo "usage: $0 <tablet> [<tablet> ...]   (the roster's network tablets: ${!NODES[*]})"
    exit 2
fi

tr -d '\r' < wifi_watchdog.sh > /tmp/wd_lf.sh
WANT=$(sha256sum /tmp/wd_lf.sh | cut -c1-16)
echo "local script sha256: $WANT   mode=$MODE"
echo

for name in $TARGETS; do
    ip="${NODES[$name]:-}"
    if [ -z "$ip" ]; then echo "$name: unknown node"; continue; fi
    printf '%-9s ' "$name"

    if ! MSYS_NO_PATHCONV=1 $PT connect "$ip:5555" >/dev/null 2>&1; then
        echo "adb connect failed - SKIPPED"; continue
    fi

    # Stop any existing instance BY PID (never pkill -f: this script's own cmdline contains the name)
    pid=$(MSYS_NO_PATHCONV=1 $PT -s "$ip:5555" shell \
          "ps -A -o PID,ARGS 2>/dev/null | grep '[s]h /data/local/tmp/wifi_watchdog.sh' | head -1 | awk '{print \$1}'" \
          2>/dev/null | tr -d '\r\n ')
    if [ -n "$pid" ]; then
        MSYS_NO_PATHCONV=1 $PT -s "$ip:5555" shell "kill $pid" >/dev/null 2>&1
        printf 'stopped pid %s; ' "$pid"
    fi

    MSYS_NO_PATHCONV=1 $PT -s "$ip:5555" shell "cat > /data/local/tmp/wifi_watchdog.sh" < /tmp/wd_lf.sh
    got=$(MSYS_NO_PATHCONV=1 $PT -s "$ip:5555" shell \
          "chmod 755 /data/local/tmp/wifi_watchdog.sh; sha256sum /data/local/tmp/wifi_watchdog.sh | cut -c1-16" \
          2>/dev/null | tr -d '\r\n ')
    if [ "$got" != "$WANT" ]; then
        echo "SHA MISMATCH (got $got want $WANT) - NOT STARTED"; continue
    fi

    MSYS_NO_PATHCONV=1 $PT -s "$ip:5555" shell \
        "setsid sh /data/local/tmp/wifi_watchdog.sh $MODE </dev/null >/dev/null 2>&1 &" >/dev/null 2>&1
    sleep 4
    beat=$(MSYS_NO_PATHCONV=1 $PT -s "$ip:5555" shell \
           "tail -1 /data/local/tmp/wifiwd.log 2>/dev/null" 2>/dev/null | tr -d '\r')
    echo "sha OK; $beat"
done
