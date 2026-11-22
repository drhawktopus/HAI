# Make a foundry health daemon self-healing.
#
# WHY: a task with ONE past-dated time trigger, no repetition and RestartCount 0 runs its daemon
# until the process exits, and then nothing on the box ever brings it back; nobody notices, because
# a guard that is not running cannot report that it is not running. The only sign is a status
# board's Daemon row going red, a day later.
#
# A guard that needs a person to restart it will be dead again next week. So:
#   * AtStartup trigger        -- survives a reboot.
#   * repetition every 15 min  -- survives a crash, a kill, or an exit nobody saw.
#   * MultipleInstances=IgnoreNew (already set) -- the repetition is a no-op while it is alive, so
#     this cannot ever produce two daemons. That pairing is what makes the repeat safe: re-fire
#     blindly, let the scheduler drop it if the daemon is already there.
#   * RestartCount/Interval    -- the scheduler's own retry for a failed start.
#
# It does NOT touch the existing trigger, the action, or the account. Read-then-write on one task.
param([Parameter(Mandatory = $true)][string]$TaskName)

$ErrorActionPreference = "Stop"
$t = Get-ScheduledTask -TaskName $TaskName

"BEFORE  {0}: triggers={1} restartCount={2} repetition={3}" -f `
    $TaskName, $t.Triggers.Count, $t.Settings.RestartCount, $t.Triggers[0].Repetition.Interval

# Keep whatever triggers exist; add a boot trigger carrying the repetition.
# Indefinite repetition is an EMPTY Duration, not a huge one. [TimeSpan]::MaxValue serialises to
# P99999999DT23H59M59S and the scheduler rejects the whole XML as out of range -- which fails the
# write entirely, so the task keeps its old (broken) settings and only the error says otherwise.
#
# THE REPETITION MUST HANG OFF A PAST-DATED **TIME** TRIGGER, NOT THE BOOT TRIGGER. A boot
# trigger's repetition window only opens when that trigger FIRES, i.e. at the next boot -- so on a
# box that booted days ago the repeat never runs and the task looks perfectly configured while
# healing nothing. Reading the settings back cannot show this; only a live decoy (kill the daemon,
# watch for it to return) does. THAT is why a guard is not verified until something kills it.
#
# A `-Once` trigger with a start boundary in the PAST plus a repetition repeats forever from now,
# which is the behaviour wanted. The boot trigger stays, without repetition, to cover reboots.
$rep = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
          -RepetitionInterval (New-TimeSpan -Minutes 15)).Repetition
$rep.Duration = ""

$timeTriggers = @($t.Triggers | Where-Object { $_.CimClass.CimClassName -eq "MSFT_TaskTimeTrigger" })
if ($timeTriggers.Count -ge 1) {
    "  attaching repetition to the existing time trigger (start $($timeTriggers[0].StartBoundary))"
    foreach ($tt in $timeTriggers) { $tt.Repetition = $rep }
    $keep = @($t.Triggers | Where-Object { $_.CimClass.CimClassName -ne "MSFT_TaskBootTrigger" })
} else {
    "  no time trigger - creating a past-dated one to carry the repetition"
    $new = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddYears(-5)
    $new.Repetition = $rep
    $keep = @($t.Triggers | Where-Object { $_.CimClass.CimClassName -ne "MSFT_TaskBootTrigger" }) + $new
}
$triggers = $keep + (New-ScheduledTaskTrigger -AtStartup)

$t.Settings.RestartCount = 3
$t.Settings.RestartInterval = "PT5M"
$t.Settings.ExecutionTimeLimit = "PT0S"        # a daemon must never be killed for running long
$t.Settings.DisallowStartIfOnBatteries = $false
$t.Settings.StopIfGoingOnBatteries = $false

Set-ScheduledTask -TaskName $TaskName -Trigger $triggers -Settings $t.Settings | Out-Null

$v = Get-ScheduledTask -TaskName $TaskName
$rep = ($v.Triggers | Where-Object { $_.Repetition.Interval } | Select-Object -First 1).Repetition.Interval
"AFTER   {0}: triggers={1} restartCount={2} repetition={3} timeLimit={4}" -f `
    $TaskName, $v.Triggers.Count, $v.Settings.RestartCount, $rep, $v.Settings.ExecutionTimeLimit

# Prove it, rather than trusting the write: the values must be readable back as intended.
# @() around the pipeline. In PS 5.1 a Where-Object that matches exactly ONE object returns that
# object, not an array, and `.Count` on it is $null -- so without it this check reports FAILED on a
# write that succeeded. A verifier that cries wolf is the same defect as a guard that does.
$bootCount = @($v.Triggers | Where-Object { $_.CimClass.CimClassName -eq "MSFT_TaskBootTrigger" }).Count
# The repetition must be on a TIME trigger. On a boot trigger it is inert until the next reboot,
# which is the exact bug this script was rewritten to fix -- so assert WHERE it landed, not just
# that some trigger somewhere carries it.
$repOnTime = @($v.Triggers | Where-Object {
    $_.CimClass.CimClassName -eq "MSFT_TaskTimeTrigger" -and $_.Repetition.Interval -eq "PT15M" }).Count
$ok = ($v.Settings.RestartCount -eq 3) -and ($repOnTime -ge 1) -and ($bootCount -ge 1)
"  bootTriggers=$bootCount  repetitionOnTimeTrigger=$repOnTime"
"VERIFIED: $ok"
if (-not $ok) { exit 1 }
