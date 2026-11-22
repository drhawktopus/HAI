# Read-only, 60 s: every process START on this box, grouped by (child name, parent name, parent's script).
# One WMI event subscription; nothing is changed. Prints the count per minute, largest first.
$ErrorActionPreference = 'Stop'
$starts = New-Object System.Collections.ArrayList
$q = 'SELECT * FROM Win32_ProcessStartTrace'
Register-WmiEvent -Query $q -SourceIdentifier spawnCensus -Action {
    [void]$Event.MessageData.Add(@($Event.SourceEventArgs.NewEvent.ProcessName, [int]$Event.SourceEventArgs.NewEvent.ParentProcessID))
} -MessageData $starts | Out-Null
$t0 = Get-Date
Start-Sleep -Seconds 60
Unregister-Event -SourceIdentifier spawnCensus
$secs = [math]::Round(((Get-Date) - $t0).TotalSeconds)
$procs = @{}
foreach ($p in Get-CimInstance Win32_Process) { $procs[[int]$p.ProcessId] = $p }
$groups = @{}
foreach ($s in $starts) {
    $parent = $procs[$s[1]]
    $pname = if ($parent) { $parent.Name } else { '(gone)' }
    $script = ''
    if ($parent -and $parent.CommandLine) {
        $m = [regex]::Match($parent.CommandLine, '[\w\-\.]+\.(py|ps1|vbs|cmd|bat)')
        if ($m.Success) { $script = $m.Value }
    }
    $key = '{0} <- {1} {2}' -f $s[0], $pname, $script
    $groups[$key] = 1 + [int]$groups[$key]
}
'process starts in {0} s: {1}' -f $secs, $starts.Count
$groups.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 22 | ForEach-Object { '{0,5}  {1}' -f $_.Value, $_.Key }
