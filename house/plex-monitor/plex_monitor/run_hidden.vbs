' Launches the Plex monitor with NO visible window.
'
' WHY: a task that runs `cmd.exe /c run.bat` on an INTERACTIVE principal draws a console on the
' desktop every fire. Closing one of those windows does not merely dismiss it -- it kills the
' process inside with 0xC000013A: the daemon dies silently and the task reads as "ran fine".
'
' Window style 0 = hidden, wait = False.
'
' SELF-LOCATING, deliberately: the path comes from WScript.ScriptFullName, not a literal. That
' is the shape plex_watchdog\run_hidden.vbs uses too, so the two copies stay byte-identical. A
' hardcoded path would need a second, divergent copy per location -- one idea, two derivations,
' which always drift.
Option Explicit
Dim shell, scriptDir, bat
Set shell = CreateObject("WScript.Shell")
scriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
bat = scriptDir & "run.bat"
shell.Run "cmd /c """ & bat & """", 0, False
