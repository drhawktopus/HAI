# rearm_wifi_watchdogs.ps1 -- restart the tablets' on-node WiFi watchdog when it has stopped.
#
# Runs on the box that holds adb and the tablets' ALREADY-AUTHORIZED adbkey. Only
# uid 2000 can start the watchdog and adbd is the sole door to that uid, so the re-arm has to come
# from a box with an authorized adb client. Putting it here avoids shipping adb + a key into the
# tablet-foundry image for one function.
#
# !! THIS RE-ARMS FOR THE **NEXT** OUTAGE, NEVER THE CURRENT ONE. It needs the network to reach a
# node, so it can only act once a node is already BACK. Nothing external can rescue a node
# mid-outage -- that is the entire reason the watchdog lives on the device.
#
# !! ONE DERIVATION, NOT TWO. Status comes from Tolkien's `wifi_watchdog_status()` rather than
# being re-implemented here in PowerShell. Detection and action must share one derivation or they
# drift into disagreement. This script decides
# NOTHING about liveness; it only acts on a verdict Tolkien already computed.
#
# !! TWO CONSECUTIVE CONFIRMATIONS before acting: a check that runs at the wrong moment gives a
# confident wrong answer, and a node 30 seconds into a reboot must not read as "watchdog absent,
# re-arm it".
#
# !! Acts ONLY on `absent` / `down`. Never on `unknown` (could not measure), never on `denied`
# (exists but unreadable -- that would restart an already-running watchdog), never on `silent`
# (still inside 3x cadence). Never touches the operator's stand-down flag; the watchdog checks it.
#
# !! ASCII ONLY IN THIS FILE. Windows PowerShell reads a file without a byte-order mark as ANSI, so
# a non-ASCII character can turn into mojibake, or into a string delimiter, on a Get-Content /
# Set-Content round trip. Plain "!!" cannot be corrupted by an encoding mismatch.

$ErrorActionPreference = 'Stop'
# Settings: the environment wins, then the lab's settings file (LAB_SETTINGS_FILE, else Lab\etc\lab.env), then the
# default. The same precedence as Lab\bin\lab_settings.py.
function Get-LabSetting([string]$Name, [string]$Default = '') {
    $v = [Environment]::GetEnvironmentVariable($Name)
    if ($v -and $v.Trim()) { return $v.Trim() }
    $file = $env:LAB_SETTINGS_FILE
    if (-not $file) { $file = Join-Path $PSScriptRoot '..\..\etc\lab.env' }
    if (Test-Path $file) {
        $m = Select-String -Path $file -Pattern ('^' + [regex]::Escape($Name) + '=(.*)$') | Select-Object -First 1
        if ($m -and $m.Matches[0].Groups[1].Value.Trim()) { return $m.Matches[0].Groups[1].Value.Trim() }
    }
    return $Default
}
$Adb        = Get-LabSetting 'ADB_EXE' 'adb'
$TabletHead = Get-LabSetting 'TABLET_HEAD_NAME' 'tablet-head'
$UsbSegment = (Get-LabSetting 'FELLOWSHIP_USB_SEGMENT' '10.55.0').TrimEnd('.') + '.'
$Log    = Join-Path $PSScriptRoot 'rearm.log'
$State  = Join-Path $PSScriptRoot 'rearm.state.json'
$Script = '/data/local/tmp/wifi_watchdog.sh'
$Helper = Join-Path $PSScriptRoot 'wifiwd_status.py'
$Mode   = 'report'          # flip to 'act' only after the watchdog is proven on a real event
# The fallback roster when Tolkien's status carries none: the Fellowship roster (FELLOWSHIP_ROSTER), minus the tablets
# whose only address is on the USB segment, which have no network adb door.
$Nodes = @{}
$RosterPath = Get-LabSetting 'FELLOWSHIP_ROSTER'
if ($RosterPath -and (Test-Path $RosterPath)) {
    (Get-Content $RosterPath -Raw | ConvertFrom-Json).tablets.PSObject.Properties |
        Where-Object { -not ([string]$_.Value.address).StartsWith($UsbSegment) } |
        ForEach-Object { $Nodes[$_.Name] = [string]$_.Value.address }
}
$Actionable = @('absent','down')

function Write-Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'), $msg
    Add-Content -Path $Log -Value $line -Encoding utf8
}

# ---- adb WITHOUT letting it kill this script ----------------------------------------------------
# !! $ErrorActionPreference = 'Stop' (set above) makes a NATIVE command's stderr a TERMINATING
# error in PS 5.1 -- each stderr line becomes a NativeCommandError ErrorRecord. So a perfectly
# ordinary "error: device '<address>:5555' not found" does not just fail the call, it ABORTS
# the whole re-armer, on the first unreachable node.
#
# A guard that can crash its own host is worse than no guard. This wrapper returns TEXT, never
# raises, and drops ErrorRecords so a caller reading the output cannot mistake an adb complaint
# for device output.
function Invoke-AdbSafe {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$AdbArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $raw = & $Adb @AdbArgs 2>&1
        return (($raw | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] }) -join "`n").Trim()
    } catch {
        return ''
    } finally {
        $ErrorActionPreference = $prev
    }
}

# Is adb actually able to talk to this node RIGHT NOW? Returns $true only on a real transport.
# Everything downstream must gate on this: adb returns EMPTY (not an exception) when it cannot
# reach a device, and empty output silently reads as "absent" in any -match test.
function Test-AdbTransport([string]$ip) {
    Invoke-AdbSafe connect "$($ip):5555" | Out-Null
    return ((Invoke-AdbSafe -s "$($ip):5555" get-state) -eq 'device')
}

# ---- ask TOLKIEN, do not re-derive -------------------------------------------------------------
# !! Calls a FILE on the share, not `python3 -c "..."`. The one-liner form had to survive
# PowerShell -> ssh -> sh -> docker exec -> python and did not: the quotes around "/app" were
# stripped in that chain and python received a bare `sys.path.insert(0,/app)`. Text through shells
# corrupts silently; the documented fix is to run a file.
try {
    # The tablet head's login is in the docker group, so no sudo. A wrong ssh target makes every run log
    # "SKIP no JSON" and re-arm nothing while the task reports exit 0 -- a guard that expires silently --
    # so read the log, not the task's result.
    $raw = Get-Content $Helper -Raw | & ssh $TabletHead "docker exec -i tablet-foundry python3 -"
    $json = ($raw | Where-Object { $_ -match '^\s*\{' } | Select-Object -First 1)
    if (-not $json) { throw "no JSON in Tolkien's reply" }
    $status = $json | ConvertFrom-Json
} catch {
    # Could not ask => could not conclude. Do NOT act, and say why.
    Write-Log ("SKIP  could not read Tolkien status ({0}) - no action taken" -f $_.Exception.Message)
    exit 0
}

# ---- roster from TOLKIEN, never re-typed here ----------------------------------------------------
# !! The roster file above is a FALLBACK only. The helper ships `_nodes` (host -> address) straight
# from Tolkien's TABLET_NET, so a tablet added to Tolkien reaches this script without anyone editing it.
if ($status._nodes) {
    $Nodes = @{}
    $status._nodes.PSObject.Properties | Where-Object { -not ([string]$_.Value).StartsWith($UsbSegment) } |
        ForEach-Object { $Nodes[$_.Name] = [string]$_.Value }
} else {
    Write-Log ("WARN  Tolkien status carried no _nodes roster - using the built-in fallback list ({0} nodes)" -f $Nodes.Count)
}

# ---- consecutive-confirmation state ------------------------------------------------------------
$prev = @{}
if (Test-Path $State) {
    try {
        (Get-Content $State -Raw | ConvertFrom-Json).PSObject.Properties |
            ForEach-Object { $prev[$_.Name] = $_.Value }
    } catch { $prev = @{} }
}
$now = @{}

foreach ($name in ($Nodes.Keys | Sort-Object)) {
    $rec = $status.$name
    if (-not $rec) { $now[$name] = 0; continue }

    # !! NOT $state. PowerShell variable names are CASE-INSENSITIVE, so `$state` IS `$State` --
    # the state-FILE PATH declared above. An earlier version used `$state` here and silently
    # overwrote that path with the node's status string, so Set-Content wrote a file literally
    # named `absent` into the CWD, returned success, and logged "state saved". Confirmations then
    # reset on every run and the re-arm could NEVER have fired, while the log insisted it worked.
    # Found only by listing the directory and seeing a stray file called `absent`.
    $nodeState = [string]$rec.state

    if ($Actionable -notcontains $nodeState) {
        if ($prev[$name] -gt 0) { Write-Log ("{0}  cleared (state={1})" -f $name, $nodeState) }
        $now[$name] = 0
        continue
    }

    $count = 1 + [int]$prev[$name]
    $now[$name] = $count
    if ($count -lt 2) {
        # First sighting. Could be a node 30 s into a reboot -- confirm before acting.
        Write-Log ("{0}  state={1} - first sighting, waiting for confirmation" -f $name, $nodeState)
        continue
    }

    $ip = $Nodes[$name]

    # !! IS THE SCRIPT EVEN THERE? "not provisioned" and "provisioned but not running" both read
    # as `absent`, and only the second is fixable by restarting. Starting a nonexistent file over
    # adb returns exit 0, so without this check the log would say RE-ARMED while nothing ran --
    # a false success that repeats forever, every poll. Provisioning is a human act, not something
    # this script should silently attempt.
    # !! GATE ON THE TRANSPORT FIRST. adb does not throw when a device is unreachable -- it returns
    # EMPTY. Empty then fails the 'yes' match below and reads as "NOT PROVISIONED", which is a
    # DEFINITE claim built from a measurement that never happened. An absent ANSWER is not an
    # absent FILE.
    if (-not (Test-AdbTransport $ip)) {
        Write-Log ("{0}  adb transport UNAVAILABLE - cannot tell whether {1} is present. Reporting UNKNOWN, not absent; the node may be healthy over ssh." -f $name, $Script)
        $now[$name] = 0
        continue
    }

    $present = Invoke-AdbSafe -s "$($ip):5555" shell "[ -e $Script ] && echo yes || echo no"
    if ($present -notmatch 'yes') {
        Write-Log ("{0}  NOT PROVISIONED - {1} is not on this node; re-arm cannot help, it needs installing" -f $name, $Script)
        $now[$name] = 0        # do not keep re-confirming something a restart cannot fix
        continue
    }

    Write-Log ("{0}  state={1} confirmed x{2} - re-arming via adb" -f $name, $nodeState, $count)
    $ok = $false
    foreach ($attempt in 1..3) {
        try {
            & $Adb connect "$($ip):5555" | Out-Null
            & $Adb -s "$($ip):5555" shell "setsid sh $Script $Mode </dev/null >/dev/null 2>&1 &" | Out-Null
            if ($LASTEXITCODE -eq 0) { $ok = $true; break }
        } catch {
            Write-Log ("{0}  attempt {1} threw: {2}" -f $name, $attempt, $_.Exception.Message)
        }
    }
    if ($ok) {
        Write-Log ("{0}  RE-ARMED (mode={1})" -f $name, $Mode)
        $now[$name] = 0
    } else {
        # Bounded: report and stop. Never retry forever, never take the caller down.
        Write-Log ("{0}  RE-ARM FAILED after 3 attempts - reporting, not retrying" -f $name)
    }
}

# !! The consecutive-confirmation counter is WORTHLESS if this does not land -- every run would be
# a "first sighting" forever and the re-arm would never fire. The write is therefore VERIFIED, not
# assumed: a silent persistence failure presents exactly as "the watchdog is being patient", which
# is indistinguishable from working.
# ---- PHANTOM PROCESS MONITORING: the detector depends on it, checked here because adb can read it
# !! On Android 12 the resident detector is an app-child chain:
#      com.termux -> start-fellow.sh -> tablet_foundry.py --agent -> ncnn_detect   (uid u0_a1xx)
# which is exactly the shape phantom process monitoring kills. It survives ONLY because the
# monitor is disabled by hand on the Android 12 nodes. If an OS update re-arms it, those nodes
# stop serving detections with no error anywhere - the symptom is a slower pipeline, not a
# crash. (The watchdog itself is PPID 1 / uid shell and is NOT at risk.)
#
# !! This CANNOT be checked from Tolkien's ssh path: `settings get` throws SecurityException for
# Termux's uid while returning `false` to adb's uid 2000. Probing it there reads "unset" on every
# node - a permission error wearing the costume of a value. It belongs here, where adb is.
# !! IT REPORTS WHAT IT SAW, EVERY RUN - not only on deviation. Logging only when something is
# wrong makes a BROKEN CHECK indistinguishable from a HEALTHY FLEET, which is the same silence
# ambiguity the watchdog's heartbeat exists to remove. One compact line is the evidence that the
# check ran at all.
$phantom = @()
foreach ($name in ($Nodes.Keys | Sort-Object)) {
    $ip = $Nodes[$name]
    try {
        & $Adb connect "$($ip):5555" | Out-Null
        $raw = (& $Adb -s "$($ip):5555" shell "getprop ro.build.version.release; settings get global settings_enable_monitor_phantom_procs") 2>$null
        $lines = @($raw | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
        $rel = if ($lines.Count -ge 1) { $lines[0] } else { '?' }
        $ph  = if ($lines.Count -ge 2) { $lines[1] } else { 'unreadable' }
    } catch { $rel = '?'; $ph = 'unreadable' }

    $phantom += "$name(a$rel)=$ph"
    if ($rel -eq '12' -and $ph -ne 'false') {
        # Only Android 12 has the killer; only 'false' is the state we require. Anything else on a
        # 12 node is reported loudly - including 'unreadable', which is NOT the same as safe.
        Write-Log "PHANTOM PROCS $name (Android $rel): value='$ph' - EXPECTED 'false'. The ncnn detector is an app-child and can be killed silently; Frigate loses this node with no error."
    }
}
Write-Log ("phantom-procs checked: " + ($phantom -join ' '))

# ---- TERMUX APP RECOVERY: the gap this script did NOT cover -------------------------------------
# !! Everything above restores the WATCHDOG, which lives in
# /data/local/tmp and runs as uid shell. It says nothing about the Termux APP -- and the fellow
# agent, sshd and the resident detector all run INSIDE com.termux. A node can lose the whole
# app: ping fine, adb fine, ssh refused, no agent. Termux:Boot restores all of it on a
# REBOOT (verified), but nothing triggers between reboots: `am start` of the activity does not run
# the boot scripts, and RUN_COMMAND needs a permission adb's uid 2000 does not hold.
#
# !! THREE STATES, AND THE THIRD IS WHY THIS IS NOT A ONE-LINER (same shape as NOT PROVISIONED
# above). "no pid" alone would reboot a node that simply does not have Termux installed, forever.
#      installed + pid   -> healthy, nothing to do
#      installed, no pid -> the gap; a reboot genuinely fixes it
#      NOT installed     -> a reboot CANNOT conjure an app. Report, never act.
#
# !! THE DETECTOR WAS VALIDATED AGAINST A POSITIVE CONTROL BEFORE BEING TRUSTED TO REPORT ABSENCE:
# `pidof com.termux` returns a pid on a healthy node. A matcher never proven to fire on the
# positive case cannot support a negative verdict.
#
# !! A DESTRUCTIVE ACTION NEEDS A HIGHER BAR THAN A REPORT. A reboot is the most
# disruptive thing this script can do, so it requires: package installed, TWO consecutive
# confirmations like every other action here, and a per-node COOLDOWN so a node that reboots back
# into the same state cannot loop. Nothing in flight is lost -- the agent runs under com.termux, so
# if that is dead the node is already doing no Fellowship work.
#
# !! SEPARATE STATE FILE on purpose: $State has one writer, and a new consumer does not get to add
# a second (PowerShell variable names are case-insensitive, so `$state` IS `$State`).
$TmxStateFile   = Join-Path $PSScriptRoot 'rearm.termux.json'
$TmxCooldownSec = 1800          # 30 min between reboots of the SAME node

$tmxPrev = @{}
if (Test-Path $TmxStateFile) {
    try {
        (Get-Content $TmxStateFile -Raw | ConvertFrom-Json).PSObject.Properties |
            ForEach-Object { $tmxPrev[$_.Name] = $_.Value }
    } catch { $tmxPrev = @{} }
}
$tmxNow  = @{}
$tmxSeen = @()
$nowEpoch = [int][double]::Parse((Get-Date -UFormat %s))

foreach ($name in ($Nodes.Keys | Sort-Object)) {
    $ip = $Nodes[$name]
    $tmxInstalled = $false
    $tmxPid = ''

    # !! THE try/catch BELOW GUARDS THE WRONG FAILURE MODE, AND THIS GATE IS WHY IT NOW WORKS.
    # adb does NOT throw when a device is unreachable -- it writes to stderr and returns EMPTY
    # output, so the catch never fires, `$pkg` is empty, `-match` is false, and `$tmxInstalled`
    # keeps its initial $false. That would render as the DEFINITE verdict "NOT-INSTALLED".
    #
    # A node with no adb transport (no persist.adb.tcp.port; wireless debugging does not re-arm at
    # boot on these GSIs) would be logged both "NOT PROVISIONED" and "TERMUX NOT INSTALLED" while its
    # watchdog file is present and its agent is running.
    #
    # "Could not measure" must never render as a definite negative -- an unreachable node is
    # UNKNOWN. Ask the transport whether it is usable BEFORE interpreting anything it returns.
    if (-not (Test-AdbTransport $ip)) {
        $tmxSeen += "$name=unknown(no adb transport)"
        Write-Log ("{0}  adb transport UNAVAILABLE - reporting UNKNOWN, not absent. This node may be perfectly healthy over ssh; adb is a separate door, and this node has no persistent one." -f $name)
        $tmxNow[$name] = @{ confirms = 0; lastReboot = [int]$tmxPrev[$name].lastReboot }
        continue
    }

    try {
        $pkg = Invoke-AdbSafe -s "$($ip):5555" shell "pm list packages com.termux"
        $tmxInstalled = ($pkg -match 'package:com\.termux')
        $tmxPid = (Invoke-AdbSafe -s "$($ip):5555" shell "pidof com.termux").Trim()
    } catch {
        # Could not ask => could not conclude. Never rendered as a definite state.
        $tmxSeen += "$name=unreachable"
        $tmxNow[$name] = @{ confirms = 0; lastReboot = [int]$tmxPrev[$name].lastReboot }
        continue
    }

    $prevRec  = $tmxPrev[$name]
    $lastBoot = if ($prevRec) { [int]$prevRec.lastReboot } else { 0 }
    $confirms = if ($prevRec) { [int]$prevRec.confirms }   else { 0 }

    if (-not $tmxInstalled) {
        $tmxSeen += "$name=NOT-INSTALLED"
        Write-Log ("{0}  TERMUX NOT INSTALLED - a reboot cannot fix this; it needs installing" -f $name)
        $tmxNow[$name] = @{ confirms = 0; lastReboot = $lastBoot }
        continue
    }
    if ($tmxPid) {
        $tmxSeen += "$name=up($tmxPid)"
        if ($confirms -gt 0) { Write-Log ("{0}  termux recovered (pid {1})" -f $name, $tmxPid) }
        $tmxNow[$name] = @{ confirms = 0; lastReboot = $lastBoot }
        continue
    }

    # installed, no pid -> actionable
    $confirms = $confirms + 1
    $tmxSeen += "$name=DEAD(x$confirms)"
    if ($confirms -lt 2) {
        Write-Log ("{0}  TERMUX DEAD - first sighting, waiting for confirmation before rebooting" -f $name)
        $tmxNow[$name] = @{ confirms = $confirms; lastReboot = $lastBoot }
        continue
    }
    $since = $nowEpoch - $lastBoot
    if ($lastBoot -gt 0 -and $since -lt $TmxCooldownSec) {
        Write-Log ("{0}  TERMUX DEAD confirmed x{1} but only {2}s since the last reboot (cooldown {3}s) - HOLDING. A node rebooting back into the same state must not loop." -f $name, $confirms, $since, $TmxCooldownSec)
        $tmxNow[$name] = @{ confirms = $confirms; lastReboot = $lastBoot }
        continue
    }

    Write-Log ("{0}  TERMUX DEAD confirmed x{1} - rebooting to let Termux:Boot restore sshd + agent" -f $name, $confirms)
    try {
        & $Adb connect "$($ip):5555" | Out-Null
        & $Adb -s "$($ip):5555" reboot | Out-Null
        Write-Log ("{0}  REBOOT ISSUED - recovery is NOT confirmed here; the next run reports whether termux came back" -f $name)
        $tmxNow[$name] = @{ confirms = 0; lastReboot = $nowEpoch }
    } catch {
        Write-Log ("{0}  REBOOT FAILED ({1}) - reporting, not retrying" -f $name, $_.Exception.Message)
        $tmxNow[$name] = @{ confirms = $confirms; lastReboot = $lastBoot }
    }
}

# Same rule as the phantom check: report what was SEEN every run, so a broken check and a healthy
# fleet are not both silent.
Write-Log ("termux checked: " + ($tmxSeen -join ' '))
try {
    ($tmxNow | ConvertTo-Json -Compress -Depth 4) | Set-Content -Path $TmxStateFile -Encoding utf8
    if (-not (Test-Path $TmxStateFile)) {
        Write-Log "TERMUX STATE WRITE REPORTED OK BUT FILE IS ABSENT - confirmations cannot accumulate"
    }
} catch {
    Write-Log ("TERMUX STATE WRITE FAILED ({0}) - confirmations cannot accumulate, reboot will never fire" -f $_.Exception.Message)
}

$summary = ($now.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ' '
try {
    ($now | ConvertTo-Json -Compress) | Set-Content -Path $State -Encoding utf8
    if (Test-Path $State) {
        Write-Log "state saved: $summary"
    } else {
        Write-Log "STATE WRITE REPORTED OK BUT FILE IS ABSENT - confirmations cannot accumulate"
    }
} catch {
    $m = $_.Exception.Message
    Write-Log "STATE WRITE FAILED ($m) - confirmations cannot accumulate, re-arm will never fire"
}
