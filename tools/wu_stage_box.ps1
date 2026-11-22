# #38 stager, the box side: search / download / install Windows Update SOFTWARE updates through Windows
# Update's own engine. Runs as a one-shot SYSTEM task ("HAI WU Stage") because the update engine refuses installs
# started from a remote (ssh) session. Writes its progress and result to $Out (atomic replace) at every phase, so the
# stager reads STATUS, never a clock. Drivers are excluded by policy; feature upgrades and anything needing a licence
# accepted are skipped and reported, never installed. -Only <kb,kb>: install only those KBs (+ Defender definitions) -
# the canary rule (a KB must have succeeded on another box first).
param([string]$Mode = 'install', [string]$Out = "$env:ProgramData\HAI\wu-stage\result.json", [string]$Only = '')
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path (Split-Path $Out) | Out-Null
$r = [ordered]@{ mode = $Mode; phase = 'search'; done = $false; pid = $PID; started = (Get-Date).ToString('s') }
function W { $r.t = (Get-Date).ToString('s'); ($r | ConvertTo-Json -Depth 6 -Compress) | Set-Content -Encoding UTF8 ($Out + '.tmp'); Move-Item -Force ($Out + '.tmp') $Out }
try {
    W
    $s = New-Object -ComObject Microsoft.Update.Session
    $s.ClientApplicationID = 'HAI lab update stager (#38)'
    $res = $s.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0 and Type='Software'")
    $want = New-Object -ComObject Microsoft.Update.UpdateColl
    $r.found = @(); $r.skipped = @()
    $allowed = @($Only -split ',' | Where-Object { $_ -and $_ -ne 'none' })    # 'none' = no canary list
    foreach ($u in $res.Updates) {
        $cats = @($u.Categories | ForEach-Object { $_.Name }) -join '|'
        $kbs = @($u.KBArticleIDs)
        $row = [ordered]@{ title = $u.Title; kb = ($kbs -join ','); categories = $cats }
        if ($cats -match 'Upgrades') { $row.why = 'feature upgrade - not staged'; $r.skipped += $row; continue }
        if (-not $u.EulaAccepted) { $row.why = 'needs a licence accepted - not staged'; $r.skipped += $row; continue }
        if ($allowed.Count -and $cats -notmatch 'Definition' -and -not ($kbs | Where-Object { $allowed -contains $_ })) {
            $row.why = 'not yet proven on another box (canary rule)'; $r.skipped += $row; continue }
        [void]$want.Add($u); $r.found += $row
    }
    if ($Mode -eq 'search' -or $want.Count -eq 0) { $r.phase = 'done'; $r.done = $true; W; exit 0 }
    $r.phase = 'download'; W
    $d = $s.CreateUpdateDownloader(); $d.Updates = $want
    $r.download_result = $d.Download().ResultCode
    $r.phase = 'install'; W
    $i = $s.CreateUpdateInstaller(); $i.Updates = $want
    $ir = $i.Install()
    $r.install_result = $ir.ResultCode          # 2 succeeded, 3 succeeded with errors, 4 failed, 5 aborted
    $r.reboot_required = [bool]$ir.RebootRequired
    $r.per_update = @(for ($k = 0; $k -lt $want.Count; $k++) {
        $x = $ir.GetUpdateResult($k)
        [ordered]@{ title = $want.Item($k).Title; result = $x.ResultCode; hresult = $x.HResult } })
    $r.phase = 'done'; $r.done = $true; W
} catch {
    $r.phase = 'error'; $r.error = $_.Exception.Message; $r.done = $true; W
    exit 1
}
