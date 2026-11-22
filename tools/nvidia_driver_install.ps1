# GeForce driver package install on a GPU box: Authenticode check, silent install (-s -noreboot) as a one-shot
# scheduled task (survives the ssh session), wait for the installer to exit, re-apply the clock-lock task if the box
# has one, read back driver version and clocks. Usage: nvidia_driver_install.ps1 -Package <exe> -Log <log> [-LockTask 'BH GPU Clock Lock']
param(
    [Parameter(Mandatory)][string]$Package,
    [Parameter(Mandatory)][string]$Log,
    [string]$LockTask = "",
    [string]$TaskName = "NVIDIA Driver Install"
)
$ErrorActionPreference = "Stop"
function Say($m) { $l = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $m; Write-Output $l; Add-Content -Path $Log -Value $l }

$sig = Get-AuthenticodeSignature $Package
Say ("package {0} bytes; signature {1}; signer {2}" -f (Get-Item $Package).Length, $sig.Status, $sig.SignerCertificate.Subject)
if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch "NVIDIA") { throw "package signature not a valid NVIDIA signature" }

$before = (nvidia-smi --query-gpu=driver_version --format=csv,noheader).Trim()
Say "driver before: $before"

schtasks /create /f /tn $TaskName /sc once /sd 01/01/2020 /st 00:00 /ru SYSTEM /rl HIGHEST /tr "`"$Package`" -s -noreboot" | Out-Null
schtasks /run /tn $TaskName | Out-Null
Say "installer launched as task '$TaskName' (-s -noreboot)"
$deadline = (Get-Date).AddMinutes(25)
do {
    Start-Sleep 15
    $st = (schtasks /query /tn $TaskName /fo list | Select-String "^Status:").ToString()
} while ($st -match "Running" -and (Get-Date) -lt $deadline)
$rc = (schtasks /query /tn $TaskName /fo list /v | Select-String "Last Result:").ToString()
Say ("installer task ended: {0} | {1}" -f $st.Trim(), $rc.Trim())
schtasks /delete /f /tn $TaskName | Out-Null

$after = (nvidia-smi --query-gpu=driver_version --format=csv,noheader).Trim()
Say "driver after: $after"
if ($LockTask) {
    schtasks /run /tn $LockTask | Out-Null
    Start-Sleep 10
    Say ("re-applied '{0}'" -f $LockTask)
}
Say ("clocks: " + (nvidia-smi --query-gpu=clocks.gr,clocks.max.graphics,clocks.applications.graphics --format=csv,noheader))
Say ("reboot required: " + (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"))
if ($after -eq $before) { throw "driver version unchanged ($after)" }
Say "DRIVER INSTALL OK"
