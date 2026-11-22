# Put one ollama model blob on the box's NVMe behind its original path in the model store, which ollama keeps reading
# (a file symbolic link). For a model whose cold load is a disk read on a SATA store (McCoy's tev1 on Softserve: 17.2 s
# from the SATA SSD vs 1.3 s cached; the load took 26+ s and timed out). Refuses while the blob's model is loaded (an
# open file cannot be renamed) and when the NVMe copy does not hash to its own name. The original is kept as a backup
# beside the store until a load from the NVMe is proven; ollama blobs are re-pullable, so it may then be removed.
#   powershell -File model_to_nvme.ps1 -Store E:\ollama-models -Nvme C:\ollama-nvme -Blob sha256-<64 hex> -Model tev1
param([Parameter(Mandatory)][string]$Store, [Parameter(Mandatory)][string]$Nvme, [Parameter(Mandatory)][string]$Blob,
      [Parameter(Mandatory)][string]$Model)
$ErrorActionPreference = 'Stop'
$orig = Join-Path $Store "blobs\$Blob"
$dst  = Join-Path $Nvme "blobs\$Blob"
$bak  = Join-Path $Store ("blobs-bak-nvme\" + $Blob)
$loaded = (Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/ps' -TimeoutSec 5).models | Where-Object { $_.name -like "$Model*" }
if ($loaded) { "REFUSED: $Model is loaded"; exit 2 }
if ((Get-Item $orig).LinkType -eq 'SymbolicLink') { "already linked -> " + (Get-Item $orig).Target; exit 0 }
New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
if (-not (Test-Path $dst)) { Copy-Item $orig $dst }
if ((Get-FileHash $dst -Algorithm SHA256).Hash.ToLower() -ne $Blob.Substring(7)) { "REFUSED: NVMe copy does not hash to its name"; exit 3 }
New-Item -ItemType Directory -Force (Split-Path $bak) | Out-Null
Move-Item $orig $bak
New-Item -ItemType SymbolicLink -Path $orig -Target $dst | Out-Null
$it = Get-Item $orig
"linked: $orig -> $($it.Target) ($($it.LinkType))"
"backup: $bak ($((Get-Item $bak).Length) bytes)"
