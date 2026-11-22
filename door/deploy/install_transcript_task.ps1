# Refresh Uhura's filtered transcripts every 30 minutes, as SYSTEM: it reads the assistants' session files on this
# machine's disks and writes the folder the door mounts read-only as /transcripts (UHURA_TRANSCRIPTS_DIR there).
# Restart-proof: AtStartup + a repeating trigger, IgnoreNew, no execution time limit.
#
# usage: install_transcript_task.ps1 -OutDir <the transcripts folder>     (PYTHON_EXE overrides the python on PATH)
param([Parameter(Mandatory = $true)][string]$OutDir)
$ErrorActionPreference = 'Stop'
$py = $env:PYTHON_EXE
if (-not $py) { $py = (Get-Command python -ErrorAction Stop).Source }
if ($py -match '\\WindowsApps\\') { throw "python resolves to the Microsoft Store alias ($py); set PYTHON_EXE" }
$script = Join-Path $PSScriptRoot 'publish_transcripts.py'
$log = Join-Path $PSScriptRoot 'publish_transcripts.log'
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c ""{0}" -I "{1}" "{2}" >> "{3}" 2>&1"' -f $py, $script, $OutDir, $log)
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 30)
$boot = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'Uhura Transcripts' -Action $action -Trigger @($repeat, $boot) -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName 'Uhura Transcripts'
Start-Sleep -Seconds 60
$t = Get-ScheduledTask -TaskName 'Uhura Transcripts'
$i = $t | Get-ScheduledTaskInfo
'state={0} lastResult={1} lastRun={2} nextRun={3}' -f $t.State, $i.LastTaskResult, $i.LastRunTime, $i.NextRunTime
if (Test-Path $log) { Get-Content $log -Tail 2 }
