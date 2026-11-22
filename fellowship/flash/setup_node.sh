#!/bin/bash
# Build a tablet into a cluster node: Termux -> openssh -> numpy -> sshd.
# Requires the tablet to have WiFi + internet already.
# usage: setup_node.sh <serial>
export MSYS_NO_PATHCONV=1
. "$(dirname "$0")/../settings.sh"   # FS, KEY, ADB, TABLETS and SERIALS, from the lab's settings
SC="$FS"
APK="${APK:?set APK to the Termux APK to install}"
PUB=$(cat "$KEY.pub")
PFX="/data/data/com.termux/files/usr"
HOME_T="/data/data/com.termux/files/home"
ENVV="export PREFIX=$PFX HOME=$HOME_T LD_LIBRARY_PATH=$PFX/lib PATH=$PFX/bin:\$PATH TMPDIR=$PFX/tmp DEBIAN_FRONTEND=noninteractive"
S="$1"

echo "=== setup $S ==="; date
IP=$("$ADB" -s $S shell "ip -f inet addr show wlan0 2>/dev/null | grep -o 'inet [0-9.]*' | cut -d' ' -f2" | tr -d '\r')
echo "ip: ${IP:-<none>}"
[ -z "$IP" ] && { echo "ABORT: no wifi"; exit 1; }

echo ">>> install termux"
timeout 240 "$ADB" -s $S install -r "$APK" 2>&1 | tail -1

echo ">>> wake + bootstrap"
"$ADB" -s $S shell input keyevent KEYCODE_WAKEUP >/dev/null 2>&1
"$ADB" -s $S shell wm dismiss-keyguard >/dev/null 2>&1
sleep 2
"$ADB" -s $S shell am start -n com.termux/.app.TermuxActivity >/dev/null 2>&1
for i in $(seq 1 24); do
  sleep 5
  N=$("$ADB" -s $S shell "run-as com.termux ls files/usr/bin 2>/dev/null | wc -l" | tr -d '\r ')
  [ "${N:-0}" -gt 100 ] && { echo "  bootstrap ok ($N binaries)"; break; }
done

echo ">>> apt update + openssh + numpy"
timeout 900 "$ADB" -s $S shell "run-as com.termux sh -c '$ENVV; apt-get update -qq 2>&1 | tail -2; apt-get install -y -qq openssh python-numpy 2>&1 | tail -3'"

echo ">>> authorized_keys + sshd"
"$ADB" -s $S shell "run-as com.termux sh -c 'mkdir -p files/home/.ssh && echo \"$PUB\" > files/home/.ssh/authorized_keys && chmod 700 files/home/.ssh && chmod 600 files/home/.ssh/authorized_keys'"
"$ADB" -s $S shell "run-as com.termux sh -c '$ENVV; sshd'"
sleep 3

echo ">>> verify over WiFi"
ssh -i "$KEY" -p 8022 -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15 -o LogLevel=ERROR \
    "$IP" "echo SSH_OK; nproc; python -c 'import numpy;print(\"numpy\",numpy.__version__)'" 2>&1 | tail -4
date
