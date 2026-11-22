# Installs "Llama-swap Serve" on Mothership: llama-swap on :11435 serving Flash-Next (Strata) on demand.
# Shape: the Ollama Serve task's principal (the console user, Interactive, Highest - ROCm needs the user session; RUNBOOK-Ollama-
# Host-Operations) + the self-healing settings every lab daemon carries (UPS Power Logger / health daemons): AtLogon,
# a past-dated time trigger with a 15-min repetition (empty Duration = indefinite), IgnoreNew, RestartCount 3, no time
# limit. The VBS waits on llama-swap, so IgnoreNew drops every re-fire while it is alive.
param([string]$Vbs = "",      # the launcher VBS in the foundry's flashnext workspace (required)
      [string]$Exe = "",      # llama-swap.exe (required)
      [string]$User = "",     # the console user the task runs as (required: ROCm needs the user session)
      # The firewall scope is this box's own networks: pass the LAN and tailnet ranges the box
      # is on. There is no default - a box that has not said which networks it lives on has no
      # scope to allow, and this stops rather than guessing one.
      [string]$LanRange = "",
      [string]$TailnetRange = "")
$ErrorActionPreference = "Stop"
$name = "Llama-swap Serve"
if (-not $Vbs -or -not $Exe -or -not $User) { throw "pass -Vbs <launcher vbs> -Exe <llama-swap.exe> -User <console user>" }
foreach ($p in @($Vbs, $Exe, (Join-Path (Split-Path $Vbs) "llama-swap.yaml"), (Join-Path (Split-Path $Vbs) "llama_swap_serve.cmd"))) {
    if (-not (Test-Path $p)) { "MISSING: $p"; exit 1 }
}

# Firewall: the scope is the networks this box is on, given as parameters. Windows auto-adds inbound BLOCK rules for a
# new listening program and a block beats an allow - disable them.
if (-not $LanRange) { "MISSING: -LanRange (the LAN range this box is on)"; exit 1 }
if (-not (Get-NetFirewallRule -DisplayName "Llama-swap LAN" -ErrorAction SilentlyContinue)) {
    $remote = @($LanRange, $TailnetRange) | Where-Object { $_ }
    New-NetFirewallRule -DisplayName "Llama-swap LAN" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 11435 `
        -RemoteAddress $remote -Profile Any | Out-Null
}
Get-NetFirewallApplicationFilter | Where-Object { $_.Program -match "llama-swap" } | Get-NetFirewallRule |
    Where-Object { $_.Action -eq "Block" } | Disable-NetFirewallRule

$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "//B `"$Vbs`"" -WorkingDirectory (Split-Path (Split-Path $Exe))
$rep = (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)).Repetition
$rep.Duration = ""
$time = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddYears(-5)
$time.Repetition = $rep
$logon = New-ScheduledTaskTrigger -AtLogOn -User $User
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName $name -Action $action -Trigger @($time, $logon) -Settings $settings `
    -Principal $principal -Force | Out-Null

$v = Get-ScheduledTask -TaskName $name
$logonCount = @($v.Triggers | Where-Object { $_.CimClass.CimClassName -eq "MSFT_TaskLogonTrigger" }).Count
$repOnTime = @($v.Triggers | Where-Object {
    $_.CimClass.CimClassName -eq "MSFT_TaskTimeTrigger" -and $_.Repetition.Interval -eq "PT15M" }).Count
$ok = ($v.Settings.RestartCount -eq 3) -and ($repOnTime -ge 1) -and ($logonCount -ge 1) -and `
      ($v.Settings.MultipleInstances -eq "IgnoreNew") -and ($v.Principal.UserId -eq $User) -and `
      ($v.Principal.RunLevel -eq "Highest")
"task: logon=$logonCount repetitionOnTime=$repOnTime restart=$($v.Settings.RestartCount) instances=$($v.Settings.MultipleInstances) user=$($v.Principal.UserId) run=$($v.Principal.RunLevel) limit=$($v.Settings.ExecutionTimeLimit)"
"VERIFIED: $ok"
if (-not $ok) { exit 1 }
Start-ScheduledTask -TaskName $name
"started"
