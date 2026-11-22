# #38: the lab ENGINE pipeline's nightly task on Softserve (engine_pipeline.py advances one phase per run).
# Same shape as the other "HAI Lab Update Stage *" tasks: Administrator S4U / Highest, pythonw, IgnoreNew.
# 05:30 so it runs AFTER the 02:30 drivers, 03:20 WU, 03:40 apps and 04:12 Linux stagers (two stagers never share a box).
# Run elevated: ssh softserve powershell -File E:\SSDShared\Lab\bin\register_engine_task.ps1  (RUNBOOK § ELEVATION)
param([string]$Pythonw = "")   # the interpreter for the task; the box's own pythonw when not given
if (-not $Pythonw) { $Pythonw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source }
if (-not $Pythonw -or -not (Test-Path $Pythonw)) { "MISSING pythonw: '$Pythonw' (pass -Pythonw <path>)"; exit 1 }
$exe = $Pythonw
$act = New-ScheduledTaskAction -Execute $exe -Argument ('"' + (Join-Path $PSScriptRoot 'engine_pipeline.py') + '"')
$trg = New-ScheduledTaskTrigger -Daily -At 05:30
$pri = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType S4U -RunLevel Highest
$set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 6) -StartWhenAvailable
Register-ScheduledTask -TaskName "HAI Lab Update Stage Engine" -Action $act -Trigger $trg -Principal $pri -Settings $set `
    -Description "#38 lab engine pipeline: one phase per night (detect/prepare/build/delivered/canary/fleet/verify); state DOCS_STATE_DIR\lab-updates\engine.json" -Force | Out-Null
$t = Get-ScheduledTask -TaskName "HAI Lab Update Stage Engine"
"{0} | {1} {2} | {3}/{4}/{5} | next {6} | limit {7} | inst {8}" -f $t.TaskName, $t.Actions[0].Execute, $t.Actions[0].Arguments, $t.Principal.UserId, $t.Principal.LogonType, $t.Principal.RunLevel, (Get-ScheduledTaskInfo $t.TaskName).NextRunTime, $t.Settings.ExecutionTimeLimit, $t.Settings.MultipleInstances
