# Operator checklist for an AMD factory - the changes on this box that are the owner's to make, one keypress each.
# Run by the owner from the desktop shortcut (it asks Windows for admin). Nothing changes without a Y for that item.
# Every item prints what it found, what it will change, and reads the result back.
#   1. Firewall: an inbound Allow for each lab engine's ollama.exe that has none (the same rules the serving engine has).
#   2. Dump key: Right-Ctrl + Scroll Lock twice forces a memory dump on the next freeze (CrashOnCtrlScroll).
#   3. Acronis Active Protection exclusions: opens the list and Acronis (its settings are UI-only).
# -DryRun shows everything and changes nothing. -Bundle points at the folder that holds the Ollama-* engine trees;
# by default, the folder two levels above the box's OLLAMA_EXE (in the lab.env one folder up from this script).
param(
    [string]$Bundle = '',
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
if (-not $Bundle) {
    $envFile = Join-Path (Split-Path -Parent $PSScriptRoot) 'lab.env'
    $m = if (Test-Path $envFile) { Select-String -Path $envFile -Pattern '^OLLAMA_EXE=(.+)$' | Select-Object -First 1 }
    if (-not $m) { throw "no -Bundle given and no OLLAMA_EXE in $envFile" }
    $Bundle = Split-Path -Parent (Split-Path -Parent $m.Matches[0].Groups[1].Value.Trim())
}
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $here ('checklist-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')

function Say([string]$text) {
    Write-Host $text
    Add-Content -Path $log -Value $text -Encoding utf8
}

function Ask([string]$question) {
    if ($DryRun) { Say "   [dry run] would ask: $question"; return $false }
    $a = Read-Host "$question [Y/N]"
    Add-Content -Path $log -Value "$question -> $a" -Encoding utf8
    return ($a -match '^[Yy]')
}

function RulesFor([string]$exe) {
    # every inbound rule whose program is exactly this file
    $found = @()
    foreach ($f in @(Get-NetFirewallApplicationFilter -PolicyStore ActiveStore | Where-Object { $_.Program -ieq $exe })) {
        $r = $f | Get-NetFirewallRule
        if ($r.Direction -eq 'Inbound') { $found += $r }
    }
    return ,$found
}

function Describe($rule) {
    $p = $rule | Get-NetFirewallPortFilter
    $a = $rule | Get-NetFirewallAddressFilter
    return ('{0} | {1} | enabled {2} | profile {3} | {4} port {5} | from {6}' -f $rule.DisplayName, $rule.Action,
            $rule.Enabled, $rule.Profile, $p.Protocol, ($p.LocalPort -join ','), ($a.RemoteAddress -join ','))
}

Say ('Mothership operator checklist  ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + $(if ($DryRun) { '  (DRY RUN: nothing will change)' } else { '' }))
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Say "   running as admin: $admin"
if (-not $admin -and -not $DryRun) { Say '   NOT admin - close this and use the desktop shortcut.'; Read-Host 'Enter to close'; exit 1 }

# ---------------------------------------------------------------- 1. firewall
Say ''
Say '== 1. Firewall: lab engines that the LAN cannot reach yet =='
$trees = @(Get-ChildItem -Path $Bundle -Directory -Filter 'Ollama-*' -ErrorAction SilentlyContinue |
           Where-Object { Test-Path (Join-Path $_.FullName 'ollama.exe') })
Say ("   engine trees found under {0}: {1}" -f $Bundle, $trees.Count)
# Trees the lab has NAMED AHEAD (upcoming_trees.txt beside this script, one folder name per line): an engine that is
# built or about to be, not installed yet. Windows accepts a rule for a program that is not there yet, so the owner can
# say yes once and the lab can switch engines later without asking again. A name is only ever a folder under $Bundle;
# anything else in the file is ignored.
$upcomingFile = Join-Path $here 'upcoming_trees.txt'
if (Test-Path $upcomingFile) {
    foreach ($name in @(Get-Content $upcomingFile | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^Ollama-[0-9A-Za-z.\-]+$' })) {
        $full = Join-Path $Bundle $name
        if (-not ($trees | Where-Object { $_.FullName -ieq $full })) {
            $trees += [pscustomobject]@{ Name = $name + '  (NOT INSTALLED YET: named ahead by the lab)'; FullName = $full; LastWriteTime = [datetime]::MinValue }
        }
    }
}
$state = @{}
foreach ($t in $trees) {
    $exe = Join-Path $t.FullName 'ollama.exe'
    $rules = RulesFor $exe
    $allow = @($rules | Where-Object { $_.Action -eq 'Allow' -and $_.Enabled -eq 'True' })
    $block = @($rules | Where-Object { $_.Action -eq 'Block' })
    $state[$exe] = @{ rules = $rules; allow = $allow; block = $block }
    $verdict = 'NO RULE'
    if ($block.Count) { $verdict = 'BLOCKED' } elseif ($allow.Count) { $verdict = 'allowed' }
    Say ("   {0,-8} {1}  ({2} rule(s))" -f $verdict, $t.Name, $rules.Count)
}
# the template: an engine that is allowed today - the one serving if there is one, else the newest allowed tree
$serving = @(Get-Process -Name ollama -ErrorAction SilentlyContinue | ForEach-Object { $_.Path } | Where-Object { $_ })
$template = $null
foreach ($exe in $serving) { if ($state.ContainsKey($exe) -and $state[$exe].allow.Count -and -not $state[$exe].block.Count) { $template = $exe } }
if (-not $template) {
    foreach ($t in ($trees | Sort-Object LastWriteTime)) {
        $exe = Join-Path $t.FullName 'ollama.exe'
        if ($state[$exe].allow.Count -and -not $state[$exe].block.Count) { $template = $exe }
    }
}
if ($template) {
    Say "   template (an engine the LAN reaches today): $template"
    foreach ($r in $state[$template].allow) { Say ('      ' + (Describe $r)) }
} else {
    Say '   no engine here has an Allow rule to copy from.'
}
$todo = @($trees | Where-Object { $e = Join-Path $_.FullName 'ollama.exe'; $state[$e].block.Count -or -not $state[$e].allow.Count })
if (-not $todo.Count) { Say '   nothing to do: every engine tree is allowed.' }
foreach ($t in $todo) {
    $exe = Join-Path $t.FullName 'ollama.exe'
    Say ''
    Say "   -> $($t.Name)"
    if ($state[$exe].block.Count) {
        foreach ($r in $state[$exe].block) { Say ('      has: ' + (Describe $r)) }
        if (Ask "      Turn these $($state[$exe].block.Count) Block rule(s) into Allow?") {
            $state[$exe].block | Set-NetFirewallRule -Action Allow -Enabled True
        }
    } elseif ($template) {
        Say "      will add the same $($state[$template].allow.Count) rule(s) the template has, for this program."
        if (Ask '      Add them?') {
            foreach ($r in $state[$template].allow) {
                $p = $r | Get-NetFirewallPortFilter
                $a = $r | Get-NetFirewallAddressFilter
                $new = @{ DisplayName = 'ollama.exe'; Direction = 'Inbound'; Action = 'Allow'; Program = $exe
                          Profile = $r.Profile; Protocol = $p.Protocol; RemoteAddress = $a.RemoteAddress
                          Description = 'Lab engine, added by mothership_checklist.ps1 as a copy of the serving engine''s rule' }
                if ($p.Protocol -in @('TCP', 'UDP')) { $new.LocalPort = $p.LocalPort }
                New-NetFirewallRule @new | Out-Null
            }
        }
    } else {
        Say '      no template to copy and no Block rule to flip: left alone. Tell Claude.'
    }
    $after = RulesFor $exe
    Say "      read back: $($after.Count) rule(s)"
    foreach ($r in $after) { Say ('         ' + (Describe $r)) }
}

# ---------------------------------------------------------------- 2. dump key
Say ''
Say '== 2. Dump key: force a memory dump on the next freeze (Right-Ctrl held + Scroll Lock twice) =='
$keys = @('HKLM:\SYSTEM\CurrentControlSet\Services\kbdhid\Parameters', 'HKLM:\SYSTEM\CurrentControlSet\Services\i8042prt\Parameters')
$missing = @()
foreach ($k in $keys) {
    $v = (Get-ItemProperty -Path $k -Name CrashOnCtrlScroll -ErrorAction SilentlyContinue).CrashOnCtrlScroll
    Say ("   {0}  CrashOnCtrlScroll = {1}" -f $k, $(if ($null -eq $v) { '(not set)' } else { $v }))
    if ($v -ne 1) { $missing += $k }
}
$cc = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl'
Say ("   dump type now (CrashDumpEnabled): {0}  (1 complete, 2 kernel, 3 small, 7 automatic) -> {1}" -f $cc.CrashDumpEnabled, $cc.DumpFile)
if ($cc.CrashDumpEnabled -notin @(1, 2, 7)) { Say '   NOTE: that dump type is too small to show what held the disk. Not changed here - tell Claude.' }
if (-not $missing.Count) {
    Say '   nothing to do: the key is set. (It works from the restart after it was set.)'
} elseif (Ask "   Set CrashOnCtrlScroll = 1 in $($missing.Count) place(s)? (works after the next restart)") {
    foreach ($k in $missing) {
        if (-not (Test-Path $k)) { New-Item -Path $k -Force | Out-Null }
        New-ItemProperty -Path $k -Name CrashOnCtrlScroll -Value 1 -PropertyType DWord -Force | Out-Null
        Say ("   read back {0} = {1}" -f $k, (Get-ItemProperty -Path $k -Name CrashOnCtrlScroll).CrashOnCtrlScroll)
    }
}

# ---------------------------------------------------------------- 3. Acronis
Say ''
Say '== 3. Acronis Active Protection: exclude the lab (set in its own window; there is no other way in) =='
$list = Join-Path $here 'acronis_exclusions.txt'
if (Test-Path $list) {
    Say "   the list: $list"
    if (Ask '   Open the list and Acronis now?') {
        Start-Process notepad.exe -ArgumentList ('"' + $list + '"')
        $lnk = @(Get-ChildItem -Path "$env:ProgramData\Microsoft\Windows\Start Menu\Programs" -Recurse -Filter '*.lnk' -ErrorAction SilentlyContinue |
                 Where-Object { $_.Name -match 'Acronis' -and $_.Name -notmatch 'Uninstall|Help|Rescue|Clone|Tools' })
        if ($lnk.Count) { Say "   opening: $($lnk[0].FullName)"; Start-Process $lnk[0].FullName }
        else { Say '   could not find an Acronis shortcut in the Start menu - open it by hand.' }
        Say '   In Acronis: Protection -> Active Protection -> Settings -> Manage exclusions. Add every line of the list.'
    }
} else {
    Say "   the list file is missing: $list"
}

Say ''
Say "Done. A record of this run: $log"
if (-not $DryRun) { Read-Host 'Enter to close' }
