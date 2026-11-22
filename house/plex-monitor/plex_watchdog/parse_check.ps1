# Parses plex_watchdog.ps1 the way the scheduled task's Windows PowerShell 5.1 does: a file with a UTF-8 byte-order
# mark is read as UTF-8, a file without one as the ANSI code page. Reports parse errors, string tokens that span lines
# (code swallowed into a string) and whether the commands the wedge branch depends on survive as commands. Run it after
# any edit of the watchdog; exit 1 on a bad parse.
param([string]$Path = (Join-Path $PSScriptRoot 'plex_watchdog.ps1'))
$bytes = [System.IO.File]::ReadAllBytes($Path)
$bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
$text = if ($bom) { [System.Text.Encoding]::UTF8.GetString($bytes, 3, $bytes.Length - 3) } else { [System.Text.Encoding]::GetEncoding(1252).GetString($bytes) }
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$tokens, [ref]$errors)
$multi = @($tokens | Where-Object { $_.Kind -in 'StringLiteral','StringExpandable' -and $_.Extent.EndLineNumber -gt $_.Extent.StartLineNumber })
$cmds = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true) | ForEach-Object { $_.GetCommandName() })
$count = @{}
foreach ($c in $cmds) { if ($c) { $count[$c] = 1 + [int]$count[$c] } }
$want = @{ 'Stop-Process' = 1; 'Start-Process' = 2; 'Log' = 6; 'Save-State' = 3 }
$bad = @()
foreach ($k in $want.Keys) { if ([int]$count[$k] -ne $want[$k]) { $bad += "$k=$([int]$count[$k]) (want $($want[$k]))" } }
"decoded as: $(if ($bom) { 'UTF-8 (byte-order mark)' } else { 'ANSI 1252 (no byte-order mark)' })"
"parse errors: $($errors.Count); multi-line strings: $($multi.Count); commands: Stop-Process=$([int]$count['Stop-Process']) Start-Process=$([int]$count['Start-Process']) Log=$([int]$count['Log']) Save-State=$([int]$count['Save-State'])"
if ($errors.Count -or $multi.Count -or $bad.Count) { "BAD PARSE: $($bad -join ', ')"; exit 1 }
"OK: the watchdog parses as written"
