# Install the WiFi-watchdog re-armer as a scheduled task on the box that holds adb.
#
# !! A GUARD WITH NO RESTART POLICY HAS AN EXPIRY DATE, AND IT EXPIRES SILENTLY. Two foundry health
# daemons sat dead for a day this week because their tasks had one past-dated trigger, no
# repetition and RestartCount 0 -- and a guard's silence IS its failure mode, so neither could
# report it. This task therefore gets: AtStartup trigger + a REPETITION + RestartCount, and
# MultipleInstances=IgnoreNew so re-firing blindly is safe (the scheduler drops the duplicate).
# That pairing is a supervisor that needs no supervisor of its own.
#
# !! KNOWN TRAPS, both paid for in fix_health_daemon_task.ps1:
#   * [TimeSpan]::MaxValue is NOT "indefinite" -- it serialises to P99999999DT23H59M59S, the
#     scheduler rejects the whole XML, and THE WRITE FAILS ENTIRELY leaving the old settings in
#     place. Indefinite is an EMPTY RepetitionDuration.
#   * @() a Where-Object before reading .Count -- a single match returns the object, not an array,
#     and .Count is $null. My own verifier once reported FAILED on a write that had succeeded.
#
# !! VERIFY BY WATCHING IT RUN, NOT BY READING THE SETTINGS BACK: Next Run Time must be set, and rearm.log must
# gain lines at the next run.

$ErrorActionPreference = 'Stop'
$TaskName = 'Tablet WiFi Watchdog Re-armer'
$Script   = Join-Path $PSScriptRoot 'rearm_wifi_watchdogs.ps1'
# !! ASK THE OS WHO WE ARE; do not assemble it from env vars. "$env:USERDOMAIN\$env:USERNAME"
# can yield <WORKGROUP>\administrator -- USERDOMAIN is the workgroup, while the account is the
# local <MACHINE>\Administrator -- and Register-ScheduledTask fails with "No mapping between
# account names and security IDs". WindowsIdentity is the fact; the env vars are a claim.
# (This account is also the one whose profile holds the authorized adbkey.)
$User     = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path $Script)) { throw "script not found: $Script" }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NonInteractive -NoProfile -ExecutionPolicy Bypass -File `"$Script`""

# !! TWO TRIGGERS, AND THE SECOND ONE IS THE WHOLE POINT.
# An AtStartup trigger with a repetition attached does NOT start repeating until the machine next
# BOOTS -- the repetition rides on the trigger, and the trigger has not fired. Registered with
# AtStartup alone, this task read back perfectly (PT15M, IgnoreNew, RestartCount 3, State=Ready)
# and was completely INERT: `NextRunTime` was EMPTY, and it would have done nothing until the next
# reboot while looking installed. That is the same shape as the two foundry health daemons that
# sat dead for a day, and reading the settings back is exactly what fails to catch it.
#   * -Once with a PAST start date + repetition => repeats from now, forever, no reboot required.
#   * AtStartup as well => it also comes back after a reboot.
# RepetitionDuration is left UNSET on both; that is what "indefinite" actually means.
$interval = New-TimeSpan -Minutes 15

$tStart = New-ScheduledTaskTrigger -AtStartup
$tStart.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
                      -RepetitionInterval $interval).Repetition

$tNow = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddDays(-1) `
        -RepetitionInterval $interval

$trigger = @($tStart, $tNow)

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

# S4U: runs whether or not anyone is logged in, without storing a password, and still reads the
# user's profile -- which matters because the AUTHORIZED adbkey and the ssh key both live there.
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType S4U -RunLevel Limited

# !! DO NOT ANNOUNCE SUCCESS BEFORE CONFIRMING IT. An earlier version printed "registered:"
# unconditionally on the next line; Register-ScheduledTask failed with "Access is denied" (a TOKEN
# problem -- run this via ssh-to-self for a full token, RUNBOOK ELEVATION) and the script cheerfully
# reported success anyway, because the CimException did not terminate the way $ErrorActionPreference
# implied it would. Read the task back and let ITS existence be the claim.
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

$t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $t) {
    throw ("REGISTRATION FAILED: '$TaskName' does not exist after Register-ScheduledTask. " +
           "If this said 'Access is denied', that is a filtered token -- re-run through " +
           "ssh-to-self (see RUNBOOK ELEVATION), not from this shell.")
}
Write-Output "registered AND read back: $TaskName  (runs as $User)"

# !! THE ONLY SETTING THAT PROVES ANYTHING IS NextRunTime. Interval/IgnoreNew/RestartCount all read
# back correctly on a task that would never have fired. An empty NextRunTime means nothing is
# armed -- fail loudly here rather than reporting a healthy install.
$info = Get-ScheduledTaskInfo -TaskName $TaskName
if (-not $info.NextRunTime) {
    throw ("INSTALLED BUT INERT: NextRunTime is empty, so no trigger is armed and this task will " +
           "never fire on its own. Do NOT report this as installed.")
}
Write-Output ("  NextRunTime   : " + $info.NextRunTime + "   <-- armed")
Write-Output ("  triggers      : " + ($t.Triggers.CimClass.CimClassName -join ', '))
Write-Output ("  repetition    : " + $t.Triggers[0].Repetition.Interval +
              "  duration=" + $(if ($t.Triggers[0].Repetition.Duration) {
                  $t.Triggers[0].Repetition.Duration } else { '(empty = indefinite)' }))
Write-Output ("  multiple inst : " + $t.Settings.MultipleInstances)
Write-Output ("  restart count : " + $t.Settings.RestartCount)
Write-Output ("  state         : " + $t.State)
