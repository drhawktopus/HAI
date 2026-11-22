# Kirk's off-box watchdog, one tick: runs kirk_watchdog.py once and keeps a rolling log. The scheduled task
# "Kirk Change Watchdog" (install_kirk_watchdog.ps1) runs it every two minutes as SYSTEM, so it always returns and
# never waits on input (the module's every ssh runs BatchMode). Reports go out through the lab's message relay from
# the module itself, the way the factory's notify() sends them.
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File kirk_watchdog_tick.ps1 -Python <python.exe>
#
# Settings (the environment, else lab.env beside the tools): KIRK_WATCHDOG_HOME (its state and log),
# KIRK_WATCHDOG_KEY (its own ssh key for the head), KIRK_WATCHDOG_SSH_TARGET (user@host it reaches the head as), HAI_URL, DOCS_STATE_DIR (where the self-change window
# file is), HEAD_STACK_DIR, LOCAL_FOUNDRY_ROOT (whose etc holds the relay's address and credentials).
param([string]$Python = 'python')
$ErrorActionPreference = "Continue"

function Get-LabSetting([string]$Name, [string]$Default = '') {
    $v = [Environment]::GetEnvironmentVariable($Name)
    if ($v -and $v.Trim()) { return $v.Trim() }
    $file = $env:LAB_SETTINGS_FILE
    if (-not $file) { $file = Join-Path $PSScriptRoot '..\etc\lab.env' }
    if (Test-Path $file) {
        $m = Select-String -Path $file -Pattern ('^' + [regex]::Escape($Name) + '=(.*)$') | Select-Object -First 1
        if ($m -and $m.Matches[0].Groups[1].Value.Trim()) { return $m.Matches[0].Groups[1].Value.Trim() }
    }
    return $Default
}

$wdhome   = Get-LabSetting 'KIRK_WATCHDOG_HOME'
$key      = Get-LabSetting 'KIRK_WATCHDOG_KEY'
$target   = Get-LabSetting 'KIRK_WATCHDOG_SSH_TARGET'
$head     = Get-LabSetting 'HAI_URL'
$stateDir = Get-LabSetting 'DOCS_STATE_DIR'
$stack    = Get-LabSetting 'HEAD_STACK_DIR'
$foundry  = Get-LabSetting 'LOCAL_FOUNDRY_ROOT'
if (-not ($wdhome -and $key -and $target -and $head -and $stateDir -and $stack)) {
    [Console]::Error.WriteLine("set KIRK_WATCHDOG_HOME, KIRK_WATCHDOG_KEY, KIRK_WATCHDOG_SSH_TARGET, HAI_URL, DOCS_STATE_DIR and HEAD_STACK_DIR")
    exit 2
}
$log   = Join-Path $wdhome "tick.log"
$relay = if ($foundry) { Join-Path $foundry "etc" } else { "" }

$out = & $Python (Join-Path $PSScriptRoot "kirk_watchdog.py") `
    --state      (Join-Path $wdhome "state.json") `
    --window     (Join-Path $stateDir "self-change-window.json") `
    --url        ($head.TrimEnd('/') + "/health") `
    --ssh-key    $key `
    --ssh-target $target `
    --stack-dir  $stack `
    --relay-etc  $relay 2>&1
$rc = $LASTEXITCODE

# Rolling log (the last 200 lines are kept): every tick leaves a line, a failed tick leaves its error.
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Encoding utf8 $log "$stamp rc=$rc $($out -join ' | ')"
$tail = Get-Content $log -Tail 200
Set-Content -Encoding utf8 $log $tail
exit $rc
