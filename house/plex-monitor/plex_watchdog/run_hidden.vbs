' Launches the Plex watchdog PowerShell script with NO visible window.
' Called by the "PlexWatchdog" scheduled task, which runs under an INTERACTIVE
' logon so a relaunched Plex lands in the desktop session (with its tray icon).
' Calling powershell.exe directly from an interactive task flashes a conhost
' window every minute on the RDP desktop; routing it through wscript with
' window-style 0 (hidden) suppresses that flash entirely. See RUNBOOK.
Option Explicit
Dim shell, scriptDir, ps1
Set shell = CreateObject("WScript.Shell")
scriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
ps1 = scriptDir & "plex_watchdog.ps1"
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & ps1 & """", 0, False
