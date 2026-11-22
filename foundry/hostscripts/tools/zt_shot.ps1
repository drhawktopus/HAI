# READ-ONLY: open ZenTimings in the logged-in session, screenshot its window, close it. Runs as an interactive
# scheduled task (the console session owns the desktop).
$ErrorActionPreference = 'Stop'
$out = 'C:\ProgramData\HAI\tools\zt_shot.png'
$log = 'C:\ProgramData\HAI\tools\zt_shot.log'
try {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class W {
  [StructLayout(LayoutKind.Sequential)] public struct R { public int L, T, Ri, B; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out R r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
}
'@
    $p = Start-Process 'C:\ProgramData\HAI\tools\ZenTimings\ZenTimings.exe' -PassThru
    $h = [IntPtr]::Zero
    for ($i = 0; $i -lt 40 -and $h -eq [IntPtr]::Zero; $i++) { Start-Sleep -Milliseconds 500; $p.Refresh(); $h = $p.MainWindowHandle }
    Start-Sleep -Seconds 4
    $r = New-Object W+R
    if ($h -ne [IntPtr]::Zero) { [W]::SetForegroundWindow($h) | Out-Null; Start-Sleep -Milliseconds 500; [W]::GetWindowRect($h, [ref]$r) | Out-Null }
    if ($h -eq [IntPtr]::Zero -or ($r.Ri - $r.L) -lt 50) {
        $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; $r.L = $b.X; $r.T = $b.Y; $r.Ri = $b.Right; $r.B = $b.Bottom
    }
    $bmp = New-Object System.Drawing.Bitmap ($r.Ri - $r.L), ($r.B - $r.T)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($r.L, $r.T, 0, 0, $bmp.Size)
    $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
    "ok window=$h rect=$($r.L),$($r.T),$($r.Ri),$($r.B)" | Out-File $log -Encoding utf8
    if (-not $p.HasExited) { $p.CloseMainWindow() | Out-Null; Start-Sleep -Seconds 2; if (-not $p.HasExited) { $p.Kill() } }
} catch {
    "error: $_" | Out-File $log -Encoding utf8
}
