# #38 Lab update system, step 1: what is installed on this Windows box and what updates are waiting.
# READ-ONLY. Programs come from the Uninstall registry keys, never Win32_Product (which triggers MSI repairs).
# Windows Update is asked through its own COM searcher (a search, never a download or install).
# Prints one JSON object. Driven by update_inventory.py.
$ErrorActionPreference = 'SilentlyContinue'
function S($d) { if ($d) { try { ([datetime]$d).ToString('s') } catch { [string]$d } } else { $null } }
$o = [ordered]@{ host = $env:COMPUTERNAME; asked = (Get-Date).ToString('s') }

$os = Get-CimInstance Win32_OperatingSystem
$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$o.os = [ordered]@{ caption = $os.Caption; version = $os.Version; build = $os.BuildNumber; ubr = $cv.UBR
                    display = $cv.DisplayVersion; installed = (S $os.InstallDate); last_boot = (S $os.LastBootUpTime) }
$o.reboot_pending = [bool]((Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') -or
                           (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'))

$au = Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU'
$wp = Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate'
$ux = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings'
$st = Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\WindowsStore'
$o.update_policy = [ordered]@{ NoAutoUpdate = $au.NoAutoUpdate; AUOptions = $au.AUOptions
    ExcludeWUDriversInQualityUpdate = $wp.ExcludeWUDriversInQualityUpdate; ActiveHoursStart = $ux.ActiveHoursStart
    ActiveHoursEnd = $ux.ActiveHoursEnd; PauseUpdatesExpiryTime = $ux.PauseUpdatesExpiryTime
    StoreAutoDownload = $st.AutoDownload }

try {
    $searcher = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()
    $r = $searcher.Search('IsInstalled=0 and IsHidden=0')
    $o.wu_pending = @($r.Updates | ForEach-Object { [pscustomobject]@{ title = $_.Title; kb = (@($_.KBArticleIDs) -join ',')
        type = $_.Type; categories = (@($_.Categories | ForEach-Object { $_.Name }) -join '|'); severity = $_.MsrcSeverity
        downloaded = $_.IsDownloaded; published = (S $_.LastDeploymentChangeTime) } })
    $n = $searcher.GetTotalHistoryCount()
    $o.wu_history = @($searcher.QueryHistory(0, [Math]::Min(30, $n)) | ForEach-Object {
        [pscustomobject]@{ date = (S $_.Date); title = $_.Title; result = $_.ResultCode; operation = $_.Operation } })
    $o.wu_ok = $true
} catch { $o.wu_ok = $false; $o.wu_error = $_.Exception.Message }

$o.drivers = @(Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.DriverProviderName -and
        $_.DriverProviderName -notmatch '^Microsoft' -and $_.DeviceName } | ForEach-Object {
    [pscustomobject]@{ class = $_.DeviceClass; device = $_.DeviceName; provider = $_.DriverProviderName
                       version = $_.DriverVersion; date = (S $_.DriverDate); inf = $_.InfName } } |
    Sort-Object device, version -Unique)

$keys = @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
          'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')
$keys += @(Get-ChildItem 'Registry::HKEY_USERS' | Where-Object { $_.Name -match 'S-1-5-21-[\d-]+$' } |
           ForEach-Object { 'Registry::' + $_.Name + '\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' })
$o.programs = @(Get-ItemProperty $keys | Where-Object { $_.DisplayName -and -not $_.SystemComponent } | ForEach-Object {
    [pscustomobject]@{ name = $_.DisplayName; version = $_.DisplayVersion; publisher = $_.Publisher
                       installed = $_.InstallDate; scope = $(if ($_.PSPath -match 'HKEY_USERS') { 'user' } else { 'machine' }) } } |
    Sort-Object name, version -Unique)

$pat = 'updat|upgrad|AMDRS|Radeon|AMD Crash|NvContainer|NVDisplay|NVIDIA|edgeupdate|gupdate|GoogleUpdater|Steam|Acronis|Parsec|Adobe|Mozilla|Brave|Discord|Ollama|ASUS|Armoury|Lian|L-Connect|Corsair|Razer|Logi|Chrome Remote|chromoting|Tailscale'
$o.vendor_services = @(Get-CimInstance Win32_Service | Where-Object { $_.Name -match $pat -or $_.DisplayName -match $pat } |
    ForEach-Object { [pscustomobject]@{ name = $_.Name; display = $_.DisplayName; state = $_.State; start = $_.StartMode
                                        path = $_.PathName } })
$o.update_tasks = @(Get-ScheduledTask | Where-Object { ($_.TaskName + ' ' + $_.TaskPath) -match 'updat|upgrad' -and
        $_.TaskPath -notmatch '^\\Microsoft\\Windows\\' } | ForEach-Object {
    $i = $_ | Get-ScheduledTaskInfo
    [pscustomobject]@{ name = $_.TaskName; path = $_.TaskPath; state = [string]$_.State; next = (S $i.NextRunTime)
                       last = (S $i.LastRunTime); action = (@($_.Actions | ForEach-Object { $_.Execute }) -join ' | ') } })

$mp = Get-MpComputerStatus
$o.defender = [ordered]@{ platform = $mp.AMProductVersion; engine = $mp.AMEngineVersion
    signatures = $mp.AntivirusSignatureVersion; signatures_updated = (S $mp.AntivirusSignatureLastUpdated)
    realtime = $mp.RealTimeProtectionEnabled }

$o.ollama = @(Get-Process ollama, llama-server | Where-Object { $_.Path } | ForEach-Object {
    [pscustomobject]@{ process = $_.ProcessName; path = $_.Path; file_version = $_.MainModule.FileVersion } } |
    Sort-Object path -Unique)
try { $o.ollama_api_version = (Invoke-RestMethod -TimeoutSec 5 'http://127.0.0.1:11434/api/version').version } catch { }
$o.winget = [bool](Get-Command winget -ErrorAction SilentlyContinue)

# THE LAB'S OWN ENGINES (the factory engines are part of the inventory). Roots from the foundry's
# constants (foundry_paths.AI_TOOLKIT / AI_TOOLKIT_KREA2 / COMFY_ROOT), llama-swap and the Flash-Next trees
# (RUNBOOK-Ollama-Host-Operations). Git revision read from .git itself (no git needed); package versions from each
# venv's dist-info names (no import - importing torch would touch the card's runtime).
function GitRev($root) {
    $g = Join-Path $root '.git'
    if (-not (Test-Path $g)) { return $null }
    $head = (Get-Content (Join-Path $g 'HEAD') -TotalCount 1).Trim()
    if ($head -notmatch '^ref: (.+)$') { return [pscustomobject]@{ ref = 'detached'; commit = $head } }
    $ref = $Matches[1]; $f = Join-Path $g $ref
    $c = if (Test-Path $f) { (Get-Content $f -TotalCount 1).Trim() } else {
        (Select-String -Path (Join-Path $g 'packed-refs') -Pattern (' ' + [regex]::Escape($ref) + '$') | Select-Object -First 1).Line.Split(' ')[0] }
    [pscustomobject]@{ ref = $ref; commit = $c; head_written = (S (Get-Item (Join-Path $g 'HEAD')).LastWriteTime) }
}
function Pkgs($venv) {
    $sp = Join-Path $venv 'Lib\site-packages'
    if (-not (Test-Path $sp)) { return $null }
    $want = '^(torch|torchvision|torchaudio|diffusers|transformers|accelerate|safetensors|xformers|numpy|peft|optimum-quanto|bitsandbytes|onnxruntime[-a-z]*|comfyui[-a-z]*|inspect[-_]ai)-(.+)\.dist-info$'
    $h = [ordered]@{}
    Get-ChildItem $sp -Directory -Filter '*.dist-info' | ForEach-Object { if ($_.Name -match $want) { $h[$Matches[1]] = $Matches[2] } }
    $pv = Join-Path $venv 'pyvenv.cfg'
    if (Test-Path $pv) { $h['python'] = ((Get-Content $pv | Where-Object { $_ -match '^version' }) -replace '^[^=]+=\s*', '') }
    $h
}
$o.foundry_env = [ordered]@{}
foreach ($scope in 'Machine', 'User') {
    [Environment]::GetEnvironmentVariables($scope).GetEnumerator() | Where-Object { $_.Key -like 'FOUNDRY*' } |
        ForEach-Object { $o.foundry_env[$scope + ':' + $_.Key] = $_.Value }
}
# the box's settings files (the lab.env beside the installed tools, and the foundry's own): the engine trees they name
$labenv = [ordered]@{}
$froot = $env:FOUNDRY_ROOT
if (-not $froot) { $froot = [Environment]::GetEnvironmentVariable('FOUNDRY_ROOT', 'Machine') }
foreach ($f in @((Join-Path $env:ProgramData 'HAI\lab.env'), $(if ($froot) { Join-Path $froot 'etc\lab.env' } else { $null }))) {
    if ($f -and (Test-Path $f)) {
        Get-Content $f | Where-Object { $_ -match '^[A-Z_]+=' } | ForEach-Object { $k, $v = $_ -split '=', 2; $labenv[$k] = $v }
    }
}
foreach ($k in @($labenv.Keys)) { if ($k -like 'FOUNDRY*') { $o.foundry_env['File:' + $k] = $labenv[$k] } }
# the engine trees to inventory: the trees the box's settings name, its foundry root, and UPDATE_INVENTORY_ROOTS (semicolon-separated)
$roots = @($labenv['FOUNDRY_AI_TOOLKIT'], $labenv['FOUNDRY_AITK_KREA2'], $labenv['FOUNDRY_COMFY_ROOT'], $froot) +
         @("$($labenv['UPDATE_INVENTORY_ROOTS'])" -split ';') | Where-Object { $_ } | Select-Object -Unique
foreach ($v in $o.foundry_env.Values) {           # FOUNDRY_COMFY_LAUNCH / FOUNDRY_AITK_KREA2 move an engine on this box
    $d = if ($v -match '\.(bat|cmd)$') { Split-Path $v } else { $v }
    if ($d -and (Test-Path $d -PathType Container)) { $roots += $d; $roots += (Join-Path $d 'ComfyUI') }
}
$o.engines = @(foreach ($r in ($roots | Select-Object -Unique)) {
    if (-not (Test-Path $r -PathType Container)) { continue }
    if (-not ((Test-Path (Join-Path $r '.git')) -or (Test-Path (Join-Path $r 'main.py')) -or (Test-Path (Join-Path $r 'run.py')) -or
              (Test-Path (Join-Path $r 'bin')))) { continue }       # an output-only folder is not an engine
    $venv = @('venv', '..\venv', 'python_env', 'python_embeded', '..\python_embeded', '.venv') |
        ForEach-Object { Join-Path $r $_ } | Where-Object { Test-Path (Join-Path $_ 'Lib\site-packages') } | Select-Object -First 1
    [pscustomobject]@{ root = $r; git = (GitRev $r); venv = $venv; packages = $(if ($venv) { Pkgs $venv } else { $null })
        custom_nodes = $(if (Test-Path (Join-Path $r 'custom_nodes')) { @(Get-ChildItem (Join-Path $r 'custom_nodes') -Directory | ForEach-Object { $_.Name }) } else { $null })
        updaters = @(Get-ChildItem $r -File | Where-Object { $_.Name -match 'updat' } | ForEach-Object { $_.Name }) }
})
$o.llama_swap = @(if ($labenv['LLAMA_SWAP_DIR']) { Get-ChildItem $labenv['LLAMA_SWAP_DIR'] -Directory | ForEach-Object { $_.Name } })
$o.flashnext_trees = @(if ($labenv['FLASHNEXT_PARENT']) { Get-ChildItem $labenv['FLASHNEXT_PARENT'] -Directory -Filter 'Ollama-FlashNext*' | ForEach-Object { $_.Name } })
$o.ollama_trees = @(Get-ChildItem -Path 'C:\Users\*\AppData\Local\Programs\Ollama*', 'C:\Users\*\AppData\Local\AMD\AI_Bundle\Ollama*' -Directory |
    ForEach-Object { $_.FullName })
$o | ConvertTo-Json -Depth 5 -Compress
