' Softserve: starts the factory health daemon's .cmd with no console window (style 0) and does not wait; closing a
' console would stop the daemon. The task keeps the interactive token because the .cmd runs this user's Python.
Option Explicit
Dim shell, cmd
Set shell = CreateObject("WScript.Shell")
cmd = "E:\foundry\etc\softserve_health.cmd"
shell.Run "cmd /c """ & cmd & """", 0, False
