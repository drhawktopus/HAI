#!/system/bin/sh
# wifi_watchdog.sh -- recover a tablet from a periodic WiFi deauth, ON THE NODE.
#
# WHY ON THE NODE: when the radio drops, adb drops with it (same link), so nothing outside the
# device can reach in to fix it. Recovery has to be local. Termux cannot do it -- Android 12
# blocks setWifiEnabled for ordinary apps -- but the `shell` uid (2000) can call `svc wifi`, and
# a process started once via adb from /data/local/tmp keeps that uid after adb disconnects.
#
# THE FAULT IT EXISTS FOR: these tablets deauth on a fixed cycle, 12 h 00 m after association to
# within seconds. Some re-associate in under a second; others sit unassociated for 20-35 minutes.
# The disconnect may be unavoidable; the 20-35 minutes is the defect.
#
# ------------------------------------------------------------------------------------------------
# SAFETY, because this thing toggles the radio it is reached over -- a guard that kills what it
# guards is worse than no guard:
#
#  * TWO TIERS, different bars. A BROAD signal (gateway unreachable) only ever REPORTS. Only the
#    NARROW, high-confidence signal (WiFi reports itself UNASSOCIATED) may ACT. A gateway that
#    stops answering while we are still associated is a network problem that toggling cannot fix
#    and might worsen.
#  * "COULD NOT TELL" IS NEVER "DOWN". If `cmd wifi status` returns nothing, that is UNKNOWN and
#    it does not count toward acting. An unmeasurable state must not render as a definite one.
#  * SUSTAINED, not instantaneous: NEED_FAILS consecutive checks (default 5 min) before acting,
#    so a one-second renewal blip is never worth a toggle.
#  * BOUNDED: one toggle, then COOLDOWN before it may act again, and MAX_ACTS_PER_DAY total. No
#    retry storms, and it cannot flap the fleet offline.
#  * STAND-DOWN FILE the operator owns, and the watchdog NEVER policies or deletes it.
#  * REPORT_ONLY mode so it can be proven against a real event before it is allowed to touch
#    anything. Run it this way first.
#  * Everything is wrapped: nothing here may raise and take the node down with it.
#
# usage:  setsid sh /data/local/tmp/wifi_watchdog.sh [act]     ("act" enables the ACT tier)
# ------------------------------------------------------------------------------------------------

GW="${GW:-}"        # a fixed gateway to check; unset = whatever the route outside goes via
DIR=/data/local/tmp
LOG=$DIR/wifiwd.log
STANDDOWN=$DIR/wifiwd.OFF
ACTS=$DIR/wifiwd.acts
LASTACT=$DIR/wifiwd.lastact
PERIOD=30            # seconds between checks
NEED_FAILS=10        # 10 x 30s = 5 minutes unassociated before acting
COOLDOWN=900         # 15 min after an action before another is allowed
MAX_ACTS_PER_DAY=6

MODE=report
# !!!! THE ACT TIER IS DISABLED. DO NOT RE-ENABLE IT UNTIL THE DEAD-MAN IS REPLACED. !!!!
#
# The ACT path arms `setsid sh -c 'sleep N; svc wifi enable' &` before disabling the radio, on the
# assumption that a detached process survives the link dropping. IT DOES NOT on these PHH GSI
# nodes: two nets armed (90 s and 420 s), both verified as running processes beforehand, and
# NEITHER fired; the node stayed off the network until it was physically touched.
#
# !! `svc wifi disable` is PERSISTENT -- it is not the 12 h deauth, nothing self-recovers from it,
# and adb and ssh both ride the WiFi. A failed re-enable strands the node until someone walks to
# it. So the ACT tier as designed can turn a 35-minute outage into an indefinite one.
#
# !! The two nets were NOT independent: same mechanism, so one root cause killed both. Two copies
# of one mechanism is one mechanism, and counting them as two chances is how a single point of
# failure gets dressed up as redundancy.
#
# Before ACT may be armed again, the re-enable must survive the radio dropping and be PROVEN to:
# candidates are an `am broadcast`/AlarmManager-backed timer, a foreground service, or a Termux:Boot
# hook -- none of which have been tested. Until then this script is an OBSERVER only, which is
# still useful: it timestamps every event for the re-armer and the status tile.
if [ "$1" = "act" ]; then
    MODE=report
    log "REFUSING 'act': the dead-man re-enable is PROVEN NON-FUNCTIONAL on these nodes."
    log "  Acting could strand this node indefinitely. Staying in report mode."
fi

log() {
    echo "$(date '+%Y-%m-%dT%H:%M:%S')  $*" >> "$LOG" 2>/dev/null
}

# CONNECTED / DISCONNECTED / UNKNOWN -- three states, never two.
#
# !! BOTH SIDES MATCH POSITIVELY. Matching only "connected" and letting everything else fall through
# to DISCONNECTED would read a permission error, an Android wording change or any odd transient
# string as DISCONNECTED and, five minutes later, cycle the radio on a state never measured. A
# broken sensor failing closed is worse than no sensor, because it is confidently wrong.
#
# !! Anything unrecognised is UNKNOWN, which NEVER counts toward acting. That is the safe default:
# if the real disconnected wording differs from the strings below, this watchdog does NOTHING and
# says UNKNOWN in the log, rather than acting on a guess. A live decoy shows the true string, and
# it is added here then: proven, not assumed.
#
# !! Mid-handshake states (ASSOCIATING / FOUR_WAY_HANDSHAKE / ...) are deliberately NOT
# "disconnected": the node is already recovering and toggling would interrupt it.
wifi_state() {
    out=$(cmd wifi status 2>/dev/null)
    if [ -z "$out" ]; then
        echo UNKNOWN; return
    fi
    case "$out" in
        *"Wifi is connected to"*)   echo CONNECTED ;;
        *"Wifi is not connected"*)  echo DISCONNECTED ;;
        *"Wifi is disabled"*)       echo DISCONNECTED ;;
        *)                          echo UNKNOWN ;;
    esac
}

# The gateway: GW if set, else what the route to an outside address goes via (192.0.2.1 is a documentation
# address and no packet is sent). Read on every check, so a watchdog started before WiFi is up still finds it.
gateway() {
    if [ -n "$GW" ]; then echo "$GW"; return; fi
    ip route get 192.0.2.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "via") {print $(i + 1); exit}}'
}

# yes / no, or unknown when no gateway can be found: "could not tell" is never reported as unreachable.
gw_ok() {
    g=$(gateway)
    if [ -z "$g" ]; then echo unknown; return; fi
    ping -c 1 -W 2 "$g" >/dev/null 2>&1 && echo yes || echo no
}

# !! HEARTBEAT. Without it this guard writes NOTHING while healthy, so "log has only the start
# line" is indistinguishable from "the watchdog died three minutes in" -- saying nothing would BE
# its failure mode, which is the one failure a guard cannot report. Liveness is therefore the AGE
# of the last heartbeat line, not the existence of a pid.
HEARTBEAT=1800       # 30 min
last_beat=0

SCAN_EVERY=180       # seconds between forced scans while unassociated (~1 per 3 min)
last_scan=0
scans=0

fails=0
# !! THE COOLDOWN MUST SURVIVE A RESTART. With last_act=0 at startup the FIRST action after any
# restart skips the 15-minute cooldown entirely -- which matters far more once something re-arms
# this script automatically, because a crash-loop would then toggle the radio on every respawn.
# The daily cap already persists (it lives in a file); the cooldown now does too.
last_act=$(cat "$LASTACT" 2>/dev/null)
case "$last_act" in
    ''|*[!0-9]*) last_act=0 ;;      # missing or not a number => treat as never acted
esac
log "=== watchdog start, mode=$MODE, period=${PERIOD}s, need=${NEED_FAILS} checks, gw=${GW:-from the route} ==="
[ "$last_act" -ne 0 ] && log "restored last_act=$last_act from $LASTACT (cooldown survives restarts)"

while true; do
    # The operator's switch is checked FIRST and is never touched by this script.
    if [ -f "$STANDDOWN" ]; then
        fails=0
        sleep "$PERIOD"
        continue
    fi

    st=$(wifi_state)
    gw=$(gw_ok)
    now=$(date +%s)

    # Heartbeat carries the OBSERVATIONS, so a reader can see what it saw, not just that it lived.
    #
    # !! IT ALSO DECLARES ITS OWN CADENCE. Tolkien derives its freshness thresholds from this field
    # instead of keeping a second copy of 1800 in Python -- two copies of one number in two files
    # that no test compares is a drift pair. The REPORTER declares the cadence; the reader derives from it. Changing HEARTBEAT
    # here therefore cannot silently make Tolkien's thresholds wrong.
    if [ $((now - last_beat)) -ge "$HEARTBEAT" ]; then
        log "HEARTBEAT  cadence=$HEARTBEAT state=$st gateway=$gw fails=$fails mode=$MODE"
        last_beat=$now
    fi

    if [ "$st" = "UNKNOWN" ]; then
        # Cannot measure => cannot conclude. Do not touch the counter.
        log "UNKNOWN  cmd wifi status returned nothing - not counted"
    elif [ "$st" = "CONNECTED" ]; then
        if [ "$fails" -gt 0 ]; then
            log "RECOVERED  associated again after $fails failed checks"
        fi
        fails=0
        # BROAD tier: associated but no gateway. Report only, never act.
        [ "$gw" = "no" ] && log "REPORT  associated but gateway $(gateway) unreachable (not acting: wrong tier)"
    else
        fails=$((fails + 1))
        log "DISCONNECTED  check $fails/$NEED_FAILS  (gateway=$gw)"

        # ---- SCAN TIER: the safe recovery action, and the ONLY one that runs -------------------
        # The fault is not that the radio is off; it is that Android does not SCAN promptly while
        # dozing and unassociated. `cmd wifi start-scan` forces that scan. It DISABLES NOTHING, so
        # it cannot strand a node and needs no dead-man -- worst case is the outage that was going
        # to happen anyway. That is why this runs in report mode too: it is safe by construction,
        # unlike the radio toggle, which is refused.
        #
        # !! LOG THE EVIDENCE, NOT THE INTENTION. "issued a scan" is the transcript; the artifact
        # is scan-results AGE dropping. Verified as the positive case on a healthy node: age went
        # >1000.0 -> 4.210 s and a new BSSID appeared. If age does NOT drop here, Doze is deferring
        # the scan too and this tier is inert -- which the log must show rather than hide behind a
        # command that returned 0.
        #
        # !! Rate-limited to SCAN_EVERY seconds, not every poll: the supplicant runs its own
        # backoff and hammering scans competes with it, costs battery on an already-unhappy node,
        # and buries the log.
        if [ $((now - last_scan)) -ge "$SCAN_EVERY" ]; then
            age_before=$(cmd wifi list-scan-results 2>/dev/null | awk 'NR==2 {print $4}')
            cmd wifi start-scan >/dev/null 2>&1
            sleep 6
            age_after=$(cmd wifi list-scan-results 2>/dev/null | awk 'NR==2 {print $4}')
            scans=$((scans + 1))
            log "SCAN #$scans issued  results-age ${age_before:-?} -> ${age_after:-?}  (a DROP proves a scan really occurred; no drop = Doze deferred it)"
            last_scan=$now
        fi
    fi

    if [ "$fails" -ge "$NEED_FAILS" ]; then
        since=$((now - last_act))
        today=$(date '+%Y-%m-%d')
        # !! Count REAL acts only. Counting every line including WOULD_ACT would let a day of
        # report-only observation spend the whole daily budget before the ACT tier was ever armed -- the safety cap defeated by the safety mode. "^$today ACT" cannot match
        # "$today WOULD_ACT" (the character after the date is 'W', not ' A').
        count=$(grep -c "^$today ACT" "$ACTS" 2>/dev/null)
        [ -z "$count" ] && count=0

        if [ "$last_act" -ne 0 ] && [ "$since" -lt "$COOLDOWN" ]; then
            log "HOLD  would act but only ${since}s since last action (cooldown ${COOLDOWN}s)"
            fails=0
        elif [ "$count" -ge "$MAX_ACTS_PER_DAY" ]; then
            log "HOLD  daily cap reached ($count/$MAX_ACTS_PER_DAY) - something else is wrong, not toggling"
            fails=0
        elif [ "$MODE" = "report" ]; then
            log "WOULD ACT  unassociated for $((NEED_FAILS * PERIOD))s - report-only mode, doing nothing"
            echo "$today WOULD_ACT $now" >> "$ACTS" 2>/dev/null
            echo "$now" > "$LASTACT" 2>/dev/null   # persist too: erring toward MORE cooldown
            last_act=$now
            fails=0
        else
            # !! DEAD-MAN NET IS PART OF THE MECHANISM, NOT JUST THE TEST. If this script dies
            # between disable and enable -- OOM kill, reaper, anything -- the node is off the
            # network with NO ADB TO REACH IN, the exact catastrophe this design exists to avoid.
            # A detached re-enable is armed FIRST, every single time, so the radio returns even if
            # the watchdog is gone.
            log "ACT  unassociated for $((NEED_FAILS * PERIOD))s - arming dead-man, then cycling"
            setsid sh -c 'sleep 120; svc wifi enable' </dev/null >/dev/null 2>&1 &
            svc wifi disable >/dev/null 2>&1
            sleep 3
            svc wifi enable  >/dev/null 2>&1
            echo "$today ACT $now" >> "$ACTS" 2>/dev/null
            echo "$now" > "$LASTACT" 2>/dev/null
            last_act=$now
            fails=0
            log "ACT done  radio cycled (dead-man re-enable armed for +120s); next check ${PERIOD}s"
        fi
    fi

    sleep "$PERIOD"
done
