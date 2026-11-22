#!/bin/bash
# Build a freshly-flashed tablet into a full Fellowship node, in one pass.
#
# usage: provision_node.sh <adb-serial> <fellow-name> [static-ip-hint]
#   e.g. provision_node.sh <serial from `adb devices`> tablet-3
#
# -- WHAT A WORKING NODE CARRIES ------------------------------------------------------------------
# Termux, openssh and numpy are the start; every addition below is load-bearing:
#
#   com.termux.boot   Without Termux:Boot NOTHING starts after a reboot -- Termux does not launch on
#                     its own, so sshd never returns and the node needs physical hands.
#   boot scripts      start-sshd.sh + start-fellow.sh (respawn supervisor), and start-keepalive.sh
#                     where the payload has it.
#   doze whitelist    Android's app-standby freezer will otherwise suspend the agent. This is a
#                     SEPARATE mechanism from termux-wake-lock and neither substitutes for the other.
#   .fellow_name      per-node identity. .fellow_token is a SHARED fellowship secret (minted once by
#                     fellow_ensure_token) -- same value on every node, do NOT mint a new one.
#   the detector      optional: when payload/bin holds hwdec and ncnn_detect (and payload/det its
#                     models and control image) they are delivered and proven by the agent's own live
#                     decoy. ffmpeg is installed either way; hwdec's input pipeline is an ffmpeg command.
#
# !! THE TWO APKs MUST SHARE A SIGNING KEY. Termux:Boot only functions if it is signed with the SAME
# key as Termux, so take both from one source (both from F-Droid, or both pulled off a working node).
# A mismatch presents as "boot scripts silently never run", with no error message anywhere.
#
# !! PAYLOAD GOES OVER SSH, NOT adb push. Termux's home is not writable by the adb user, and
# /data/local/tmp is not readable by the Termux uid, so the obvious adb route needs run-as gymnastics
# for every file. Once sshd is up, scp is simply correct.
#
# !! VERIFY, DO NOT ASSUME. Every stage below asserts its own result, and the detector stage runs the
# agent's live decoy against det/control.jpg and det/control.expect -- a POSITIVE CONTROL, so a clean
# verdict means the instrument works rather than merely that nothing errored.
set -u
# !! GIT BASH REWRITES ANDROID PATHS. An argument that looks like a unix path -- /data/local/tmp/x --
# gets converted to a Windows path (C:/Program Files/Git/data/local/tmp/x) before adb.exe ever sees
# it. MSYS_NO_PATHCONV=1 alone is NOT enough: adb then prints "1 file pushed" AND an error, and a
# caller that swallows output reads the pair as success.
# !! AND IT IS A GLOBAL OFF-SWITCH. It correctly stops the DEVICE path being rewritten, and it
# equally stops the LOCAL source path being rewritten, so adb.exe (a Windows binary) is handed a
# literal /tmp/_wd.sh it cannot stat: `adb: error: cannot stat`. ONE VARIABLE CANNOT SERVE BOTH SIDES
# OF AN `adb push`, and MSYS2_ARG_CONV_EXCL='*' fails the same way. So the off-switch stays on for the
# DEVICE side, and the LOCAL side is converted explicitly with `cygpath -w` via winpath() below.
# !! Any NEW adb call taking an on-device path must be verified by READING THE DEVICE afterwards --
# see the wifi_watchdog stage for the pattern. adb's exit code does not tell you the path was right.
export MSYS_NO_PATHCONV=1

# Local path -> the Windows form adb.exe needs. No-op when cygpath is absent (real Linux hosts).
winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

# !! winpath() only neutralises Git Bash's rewriting of the LOCAL argument. Where cygpath is absent
# (a real Linux host) it is a no-op and adb takes the path as it is.

# Re-establish the adb transport after a root/unroot (both RESTART adbd).
# !! $S is an adb SERIAL, not necessarily an ip:port -- the usage accepts a USB serial, for which
# "${S%%:*}:5555" builds the nonsense target "<serial>:5555". Reconnect against the MEASURED $IP
# when we have one and S is a TCP endpoint; otherwise just wait for the existing serial to come back.
adb_reconnect() {
  case "$S" in
    *:*) [ -n "${IP:-}" ] && "$ADB" connect "$IP:5555" >/dev/null 2>&1 \
                          || "$ADB" connect "$S" >/dev/null 2>&1 ;;
    *)   : ;;                      # USB serial: it re-enumerates under the same name
  esac
  "$ADB" -s "$S" wait-for-device >/dev/null 2>&1 || true
}

# This folder's parent, the fellowship tree; under Git Bash in the drive-letter form adb.exe needs.
LAB="$(cd "$(dirname "$0")/.." && { pwd -W 2>/dev/null || pwd; })"
ADB="${ADB:-$LAB/platform-tools/adb.exe}"   # adb; the ADB variable overrides
P="$LAB/payload"
KEY="$LAB/cluster/tabcluster_ed25519"
PUBK="$LAB/cluster/tabcluster_ed25519.pub"
TOKEN_F="$LAB/cluster/fellow.token"
# Tolkien's on-tablet agent, from Tolkien's source folder (the CODE_MIRRORS setting's "tolkien" entry).
# The lab's tool folder beside this tree: bin in the lab's own tree, tools in the published one.
LABBIN=""; for d in "$LAB/../bin" "$LAB/../tools"; do [ -f "$d/lab_settings.py" ] && { LABBIN="$d"; break; }; done
[ -n "$LABBIN" ] || { echo "ABORT: no lab_settings.py in $LAB/../bin or $LAB/../tools"; exit 1; }
eval "$(python "$LABBIN/lab_settings.py" --sh CODE_MIRRORS.tolkien)"
AGENT="${CODE_MIRRORS_TOLKIEN//\\//}/tablet_foundry.py"

S="${1:?usage: provision_node.sh <adb-serial> <fellow-name>}"
NAME="${2:?usage: provision_node.sh <adb-serial> <fellow-name>}"

PFX="/data/data/com.termux/files/usr"
HOME_T="/data/data/com.termux/files/home"
ENVV="export PREFIX=$PFX HOME=$HOME_T LD_LIBRARY_PATH=$PFX/lib PATH=$PFX/bin:\$PATH TMPDIR=$PFX/tmp DEBIAN_FRONTEND=noninteractive"
SSHOPT="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=20"

FAIL=0
step() { echo ""; echo "=================== $* ==================="; }
ok()   { echo "  OK  $*"; }
bad()  { echo "  !!  $*"; FAIL=1; }
# Neither pass nor fail: something measured and worth recording. Kept distinct from ok()
# so an observation can never be read as a verified success.
note() { echo "  ..  $*"; }

echo "### provisioning $S as '$NAME' ###"; date

# ── preflight: every payload file must exist BEFORE we touch the tablet ─────────────────────────
step "preflight"
# The detector is optional: present when payload/bin holds hwdec, and then all of its parts must be.
DETECT=0; [ -f "$P/bin/hwdec" ] && DETECT=1
DETECT_FILES=()
[ "$DETECT" -eq 1 ] && DETECT_FILES=("$P/bin/hwdec" "$P/bin/ncnn_detect" "$P/det/control.jpg" "$P/det/control.expect")
command -v "$ADB" >/dev/null 2>&1 && ok "adb ($ADB)" || bad "MISSING adb: $ADB (set ADB)"
for f in "$P/apk/com.termux.apk" "$P/apk/com.termux.boot.apk" \
         "$P/boot/start-sshd.sh" "$P/boot/start-fellow.sh" \
         "${DETECT_FILES[@]}" \
         "$KEY" "$PUBK" "$TOKEN_F" "$AGENT"; do
  [ -f "$f" ] && ok "$(basename "$f")" || bad "MISSING $f"
done
[ "$FAIL" -eq 0 ] || { echo "ABORT: payload incomplete"; exit 1; }

"$ADB" -s "$S" wait-for-device
IP=$("$ADB" -s "$S" shell "ip -f inet addr show wlan0 2>/dev/null | grep -o 'inet [0-9.]*' | cut -d' ' -f2" | tr -d '\r')
[ -n "$IP" ] && ok "wifi $IP" || { bad "no wifi on wlan0 -- connect it first"; exit 1; }

# ── apps ────────────────────────────────────────────────────────────────────────────────────────
step "install Termux + Termux:Boot"
timeout 300 "$ADB" -s "$S" install -r "$P/apk/com.termux.apk"      2>&1 | tail -1
timeout 120 "$ADB" -s "$S" install -r "$P/apk/com.termux.boot.apk" 2>&1 | tail -1
for pkg in com.termux com.termux.boot; do
  "$ADB" -s "$S" shell "pm list packages" 2>/dev/null | grep -q "package:$pkg$" && ok "$pkg" || bad "$pkg not installed"
done

step "bootstrap Termux (first launch unpacks \$PREFIX)"
"$ADB" -s "$S" shell input keyevent KEYCODE_WAKEUP >/dev/null 2>&1
"$ADB" -s "$S" shell wm dismiss-keyguard >/dev/null 2>&1
sleep 2
"$ADB" -s "$S" shell am start -n com.termux/.app.TermuxActivity >/dev/null 2>&1
N=0
for i in $(seq 1 30); do
  sleep 5
  N=$("$ADB" -s "$S" shell "run-as com.termux ls files/usr/bin 2>/dev/null | wc -l" | tr -d '\r ')
  [ "${N:-0}" -gt 100 ] && break
done
[ "${N:-0}" -gt 100 ] && ok "bootstrap ($N binaries)" || bad "bootstrap did not complete (saw ${N:-0} binaries)"

# !! Termux:Boot must be LAUNCHED ONCE before it will ever run boot scripts. Installing it is not
# enough -- Android will not deliver BOOT_COMPLETED to an app the user has never opened.
"$ADB" -s "$S" shell am start -n com.termux.boot/.BootActivity >/dev/null 2>&1
sleep 3
ok "Termux:Boot launched once (required before it will ever fire)"

# -- PERSISTENT adb ON :5555: DO THIS WHILE ADB IS IN HAND -----------------------------------------
# A working node has wireless debugging OFF and adbd listening on 5555 at every boot, with no pairing:
#
#     adb_wifi_enabled     = 0
#     persist.adb.tcp.port = 5555
#
# !! WIRELESS DEBUGGING IS NOT THE MECHANISM AND IS A TRAP. Its port is random per session and
# `adb_wifi_enabled=1` survives in the settings db but does NOT re-arm TLS at boot on these GSIs, so
# a node set up that way needs a new pairing code from its own screen after every reboot. Set the
# property here, while the tablet is on USB with full adb.
#
# !! setprop on a persist.* property needs ROOT -- uid 2000 gets "Failed to set property". These are
# `-userdebug` GSIs (ro.debuggable=1) so `adb root` works. It restarts adbd, which is FINE here and
# is in fact the point: adbd comes back listening on 5555 because the property is now set.
step "persistent adb on :5555 (the step that removes ALL future pairing)"
"$ADB" -s "$S" root >/dev/null 2>&1
sleep 5
"$ADB" connect "${S%%:*}:5555" >/dev/null 2>&1
"$ADB" -s "$S" shell 'setprop persist.adb.tcp.port 5555' >/dev/null 2>&1
sleep 2
PORT=$("$ADB" -s "$S" shell getprop persist.adb.tcp.port 2>/dev/null | tr -d '\r')
if [ "$PORT" = "5555" ]; then
  ok "persist.adb.tcp.port=5555 -- this node will be adb-reachable after every reboot, no pairing"
else
  bad "persist.adb.tcp.port NOT set (got '$PORT'). Without it this node needs a NEW pairing code from the physical screen after EVERY reboot. Fix it now while adb is in hand -- it is far more expensive later."
fi
"$ADB" -s "$S" unroot >/dev/null 2>&1 || true
sleep 3

# -- STEP ZERO: HOLD THE WAKE LOCK BEFORE ANY SUSTAINED WORK ---------------------------------------
# !! THIS MUST HAPPEN BEFORE apt, BEFORE any download, BEFORE anything long. It is the difference
# between a provisioned node and a node that vanishes off the LAN mid-install: a held wake lock
# blocks doze, and deep doze under a sustained download takes the WiFi out from under sshd. The
# weaker radios drop out of ARP entirely, on both transports, not merely ssh.
#
# !! The doze whitelist is NOT a substitute and neither is the boot script's own call: the whitelist
# stops the app-standby freezer, the wake lock stops the CPU/radio sleeping under a running job, and
# start-fellow.sh only takes it once the AGENT is up -- which is after this stage, far too late.
step "hold termux-wake-lock (STEP ZERO - before any sustained work)"
"$ADB" -s "$S" shell "run-as com.termux sh -c '$ENVV; termux-wake-lock'" >/dev/null 2>&1
sleep 2
# Verify by OBSERVATION, not by the command returning: termux-wake-lock exits 0 whether or not the
# Termux app actually took the lock. `Locks acquired: N / released: M` with N > M means one is HELD
# -- and note that misreading "released: 0" as "none held" has produced a wrong conclusion here
# before (RUNBOOK, tablet WiFi section).
WL=$("$ADB" -s "$S" shell 'dumpsys power 2>/dev/null | grep -ciE "PARTIAL_WAKE_LOCK.*termux"' 2>/dev/null | tr -d '\r ')
if [ "${WL:-0}" -ge 1 ]; then ok "wake lock HELD ($WL termux partial locks)"
else bad "wake lock NOT held -- STOP: a sustained apt on an X606F without it drops the node off the LAN"; fi
[ "$FAIL" -eq 0 ] || { echo "ABORT: refusing to run a long install on a dozing node"; exit 1; }

# ── packages ────────────────────────────────────────────────────────────────────────────────────
# What a node needs. Experiment tools (iperf3, openmpi, tesseract, proot-distro) are left out
# deliberately rather than by omission.
step "apt: openssh python-numpy ffmpeg + friends"
# !! A HELD DPKG LOCK IS A BUSY LANE, NOT A BROKEN ORDER -- WAIT, DO NOT FAIL.
# An apt-get started through run-as is detached from the adb transport, so when the transport drops
# mid-run (adbd restarting, a cable) the install keeps going on the tablet. A re-run then hits its lock
# ("Unable to acquire the dpkg frontend lock") and every later stage fails as if the node were broken,
# when it is a healthy node whose package manager is still working. Wait for the lock.
waited=0
while [ "$waited" -lt 1200 ]; do
  busy=$("$ADB" -s "$S" shell 'ps -A -o ARGS 2>/dev/null | grep -c "[a]pt-get"' 2>/dev/null | tr -d '\r ')
  [ "${busy:-0}" -eq 0 ] && break
  [ "$waited" -eq 0 ] && echo "  .. another apt-get is running on the node; waiting for the lane"
  sleep 10; waited=$((waited + 10))
done
[ "$waited" -gt 0 ] && echo "  .. lane cleared after ${waited}s"

timeout 1200 "$ADB" -s "$S" shell "run-as com.termux sh -c '$ENVV; apt-get update -qq 2>&1 | tail -2; apt-get install -y -qq openssh python python-numpy python-pillow ffmpeg net-tools lsof nano curl 2>&1 | tail -5'"

# !! ASSERT WHAT LANDED. "apt-get returned" is not evidence: the command above runs through run-as
# over a transport that can vanish, and a lost transport produces EMPTY output that reads exactly
# like a quiet success. Name the files that must exist.
for want in bin/sshd etc/ssh/sshd_config bin/ffmpeg bin/python; do
  if "$ADB" -s "$S" shell "run-as com.termux test -e files/usr/$want && echo yes" 2>/dev/null | tr -d '\r' | grep -q yes; then
    ok "$want"
  else
    bad "MISSING files/usr/$want -- apt did not complete"
  fi
done
[ "$FAIL" -eq 0 ] || { echo "ABORT: package stage incomplete; nothing downstream can succeed"; exit 1; }

step "ssh key + sshd"
PUB=$(cat "$PUBK")
"$ADB" -s "$S" shell "run-as com.termux sh -c 'mkdir -p files/home/.ssh && echo \"$PUB\" > files/home/.ssh/authorized_keys && chmod 700 files/home/.ssh && chmod 600 files/home/.ssh/authorized_keys'"
"$ADB" -s "$S" shell "run-as com.termux sh -c '$ENVV; sshd'"
sleep 4
ssh -i "$KEY" -p 8022 $SSHOPT "$IP" "echo SSH_OK" 2>&1 | grep -q SSH_OK && ok "ssh reachable at $IP" || bad "ssh not reachable at $IP"

# ── doze ────────────────────────────────────────────────────────────────────────────────────────
# !! Must be read back over ADB, not from inside Termux: the Termux uid cannot read
# `dumpsys deviceidle`, so asking from there returns an empty list for a node that IS whitelisted.
# That is a visibility failure that looks exactly like a configuration failure.
step "doze whitelist"
for pkg in com.termux com.termux.boot; do
  "$ADB" -s "$S" shell "dumpsys deviceidle whitelist +$pkg" >/dev/null 2>&1
done
WL=$("$ADB" -s "$S" shell 'dumpsys deviceidle whitelist' 2>/dev/null | grep -c 'com.termux')
[ "${WL:-0}" -ge 1 ] && ok "whitelisted ($WL termux entries)" || bad "doze whitelist did not take"

# ── payload over ssh ────────────────────────────────────────────────────────────────────────────
step "deliver payload"
SCP="scp -i $KEY -P 8022 $SSHOPT"
$SCP "$AGENT"            "$IP:~/tablet_foundry.py"   >/dev/null 2>&1 && ok "tablet_foundry.py" || bad "tablet_foundry.py"
ssh -i "$KEY" -p 8022 $SSHOPT "$IP" "mkdir -p ~/det ~/.termux/boot" 2>/dev/null
if [ "$DETECT" -eq 1 ]; then
  $SCP "$P/bin/hwdec"      "$IP:~/hwdec"               >/dev/null 2>&1 && ok "hwdec"             || bad "hwdec"
  $SCP "$P/bin/ncnn_detect" "$IP:~/ncnn_detect"        >/dev/null 2>&1 && ok "ncnn_detect"       || bad "ncnn_detect"
  $SCP "$P/det/"*          "$IP:~/det/"                >/dev/null 2>&1 && ok "det/ models"       || bad "det/ models"
else
  note "no detector payload (payload/bin/hwdec): the decoder and detector are not delivered"
fi
$SCP "$P/boot/start-sshd.sh" "$P/boot/start-fellow.sh" "$IP:~/.termux/boot/" >/dev/null 2>&1 && ok "boot scripts" || bad "boot scripts"
# start-keepalive.sh: steady outbound traffic so the radio never gets an idle window to drop
# association in. Without it a node can drop off the LAN on a regular cycle while fully awake,
# which neither the doze whitelist nor the wake lock prevents.
if [ -f "$P/boot/start-keepalive.sh" ]; then
  $SCP "$P/boot/start-keepalive.sh" "$IP:~/.termux/boot/" >/dev/null 2>&1 && ok "start-keepalive.sh" || bad "start-keepalive.sh"
  # Its gateway: Termux cannot read the routing table and adb can, so it is resolved here and written beside it.
  GWADDR=$("$ADB" -s "$S" shell "ip route get 192.0.2.1 2>/dev/null" | tr -d '\r' | awk '{for (i = 1; i < NF; i++) if ($i == "via") {print $(i + 1); exit}}')
  if [ -n "$GWADDR" ]; then
    ssh -i "$KEY" -p 8022 $SSHOPT "$IP" "echo GW=$GWADDR > ~/.keepalive.env" 2>/dev/null \
      && ok "keepalive gateway $GWADDR" || bad "keepalive gateway not written"
  else
    bad "keepalive gateway: no route via a gateway on this tablet"
  fi
fi

# ── /data/local/tmp payload: uid SHELL, so adb only. ssh cannot write here. ─────────────────────
# The watchdog runs from an adb shell (uid 2000, or root, a superset) because it calls `svc wifi`,
# which Termux cannot. Pushing the file without STARTING it leaves a node that looks provisioned
# and has no watchdog.
step "wifi_watchdog (adb + uid shell; ssh cannot do this)"
if [ -f "$LAB/cluster/wifi_watchdog.sh" ]; then
  tr -d '\r' < "$LAB/cluster/wifi_watchdog.sh" > /tmp/_wd.sh

  # A precaution, not a proven requirement: root is proven necessary for `setprop persist.adb.tcp.port`
  # (uid 2000 gets "Failed to set property"), not for placing a file in /data/local/tmp. The landing
  # assertion below is what makes this stage safe.
  "$ADB" -s "$S" root >/dev/null 2>&1
  sleep 5
  adb_reconnect                       # adb root restarts adbd; re-establish before touching it

  # !! DO NOT SWALLOW THE PUSH OUTPUT. adb can print "1 file pushed" AND an error in the same run
  # (Git Bash path rewriting, see MSYS2_ARG_CONV_EXCL at the top) and still exit 0. Capture it.
  PUSH_OUT=$("$ADB" -s "$S" push "$(winpath /tmp/_wd.sh)" /data/local/tmp/wifi_watchdog.sh 2>&1)
  "$ADB" -s "$S" shell 'chmod 755 /data/local/tmp/wifi_watchdog.sh' >/dev/null 2>&1

  # !! ASSERT THE FILE LANDED BY READING THE DEVICE -- never infer it from the push's exit code.
  # This is the step that separates "the file is not there" from "the file is there and did not
  # start". Those are different faults with different fixes.
  LANDED=$("$ADB" -s "$S" shell 'ls -l /data/local/tmp/wifi_watchdog.sh 2>/dev/null | wc -l' 2>/dev/null | tr -d '\r ')

  if [ "${LANDED:-0}" -lt 1 ]; then
    bad "wifi_watchdog PUSH FAILED -- the file is NOT on the device. This is a PUSH fault, not a start fault. adb said: ${PUSH_OUT:-<no output>}"
    bad "  → check (a) adbd is root (adb root; adb shell id), (b) the on-device path was not rewritten by Git Bash into C:/Program Files/Git/data/local/tmp/"
  else
    # !! DROP ROOT *BEFORE* STARTING IT, NOT AFTER. `adb root` and `adb unroot` RESTART adbd, and that
    # kills anything launched from an adb shell. Starting first and unrooting afterwards kills the
    # watchdog on the way out, while a check run in between still sees it briefly alive and reports
    # RUNNING for a process that did not survive the stage.
    "$ADB" -s "$S" unroot >/dev/null 2>&1 || true
    sleep 3
    adb_reconnect

    # ASSERT THE IDENTITY WE ARE ABOUT TO LAUNCH UNDER. "unroot returned" is not "we are uid 2000":
    # an unroot that silently failed leaves a root shell, which starts a watchdog that cannot
    # survive and looks identical at this point.
    # REPORT THE LAUNCH IDENTITY -- do NOT refuse on it. adbd on these userdebug GSIs often stays root,
    # and a watchdog running as root calls `svc wifi` fine: uid 2000 is the MINIMUM capability and root
    # is a superset, so a hard `uid == 2000` gate would refuse working nodes. A guard that blocks valid
    # work is worse than no guard; this one states what it saw and lets the run continue.
    UID_NOW=$("$ADB" -s "$S" shell 'id -u' 2>/dev/null | tr -d '\r ')
    case "${UID_NOW:-}" in
      2000) note "adb shell is uid 2000 (shell) -- the documented target identity" ;;
      0)    note "adb shell is uid 0 (root): unroot did not take, or this adbd runs as root. NOT a failure -- the watchdog runs as root and works. Recorded so the launch identity is never a guess." ;;
      "")   note "adb shell uid UNREADABLE -- proceeding, but the launch identity is unknown. That is an inconclusive, not a pass." ;;
      *)    note "adb shell is uid ${UID_NOW} -- unexpected; proceeding and recording it." ;;
    esac

    "$ADB" -s "$S" shell 'setsid sh /data/local/tmp/wifi_watchdog.sh report >/dev/null 2>&1 &' >/dev/null 2>&1
    sleep 3
    # [w]ifi_watchdog: the bracket trick EXCLUDES THE OBSERVER -- without it this grep matches its
    # own command line and reports a watchdog that does not exist.
    # !! This check is only meaningful AFTER the last adbd restart: run before it, it would pass for a
    # process the restart then killed.
    WD=$("$ADB" -s "$S" shell 'ps -A -o ARGS 2>/dev/null | grep -c "[w]ifi_watchdog"' 2>/dev/null | tr -d '\r ')
    [ "${WD:-0}" -ge 1 ] && ok "wifi_watchdog RUNNING ($WD procs, report mode, uid ${UID_NOW:-unknown})" \
                         || bad "wifi_watchdog IS ON DISK but NOT RUNNING -- the push was fine, the start failed. The other six run it; a file on disk is not a watchdog"
  fi
else
  bad "wifi_watchdog.sh missing from $LAB/cluster/"
fi

echo "$NAME"           > /tmp/_fname.$$
$SCP /tmp/_fname.$$      "$IP:~/.fellow_name"  >/dev/null 2>&1 && ok ".fellow_name=$NAME" || bad ".fellow_name"
$SCP "$TOKEN_F"          "$IP:~/.fellow_token" >/dev/null 2>&1 && ok ".fellow_token (shared)" || bad ".fellow_token"
rm -f /tmp/_fname.$$

ssh -i "$KEY" -p 8022 $SSHOPT "$IP" \
  'chmod +x ~/hwdec ~/ncnn_detect ~/.termux/boot/*.sh; chmod 600 ~/.fellow_token' 2>/dev/null

# ── verification, with a positive control ───────────────────────────────────────────────────────
step "VERIFY"
# !! THE CONTROL IS THE AGENT'S OWN: `--detect-selftest` (detect_selftest(), its live decoy).
#   ncnn_detect does not take an image path -- its interface is <param> <bin> <width> <height> and it
#   reads RAW frames on stdin -- so a hand-built control fails on a good node and condemns the
#   INSTRUMENT, not the subject. Never build a second one.
#
# !! THE VERIFY MUST BE ABLE TO FAIL THE RUN: every check below sets FAIL. A verification stage that
#   cannot turn the verdict red is decoration.
#
# !! EXISTENCE IS NOT FUNCTION: ffmpeg can be present, on PATH and the right version while unable to
# link (a libc++ older than its libraries need). `test -x` says yes to a binary that cannot run.
# Execute it and read the output.
V=$(ssh -i "$KEY" -p 8022 $SSHOPT "$IP" '
  echo "name:    $(cat ~/.fellow_name 2>/dev/null)"
  echo "cores:   $(nproc)"
  echo "numpy:   $(python -c "import numpy;print(numpy.__version__)" 2>&1)"
  echo "ffmpeg:  $(ffmpeg -version 2>&1 | head -1)"
  echo "hevc:    $(ffmpeg -hide_banner -decoders 2>/dev/null | grep -cE "^ VFS..D hevc")"
  echo "hwdec:   $(test -x ~/hwdec && echo present || echo MISSING)"
  echo "agent:   $(test -s ~/tablet_foundry.py && wc -c < ~/tablet_foundry.py) bytes"
  echo "boot:    $(ls ~/.termux/boot/ 2>/dev/null | tr "\n" " ")"
  echo "--- live decoy (the agent+ model + real decode path) ---"
  cd ~ && test -x ~/ncnn_detect && timeout 180 python tablet_foundry.py --detect-selftest 2>&1 | grep -E "\"(ok|expected|found|ms)\"" | head -6
' 2>&1)
echo "$V"

echo "$V" | grep -q '^ffmpeg:  ffmpeg version' && ok "ffmpeg executes" || bad "ffmpeg does not execute (present != working -- check libc++ vs libplacebo)"
if [ "$DETECT" -eq 1 ]; then
  echo "$V" | grep -qE '^hevc:    [1-9]'          && ok "hevc decoder"    || bad "no hevc decoder -- hwdec input_cmd cannot work"
  echo "$V" | grep -q '"ok": true'                && ok "LIVE DECOY PASSED" || bad "live decoy did NOT pass -- detector is not proven"
fi

echo ""
date
if [ "$FAIL" -eq 0 ]; then
  echo "PROVISIONED: $NAME at $IP"
  echo "REMAINING (deliberately NOT automated -- these change shared state):"
  echo "  1. add \"$NAME\" to the Fellowship roster (FELLOWSHIP_ROSTER): address $IP, serial $S"
  echo "  2. add \"$NAME\": [\"$IP\", \"$S\"] to Tolkien's TABLET_NET, recreate its container, and"
  echo "     verify the container is the one that restarted"
  echo "  3. ~/hwdec.json only if this node is to carry a camera"
else
  echo "INCOMPLETE -- see !! lines above. Node is NOT ready."
fi
exit $FAIL
