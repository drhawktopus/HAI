' Softserve: starts the order taker's .cmd with no console window (style 0) and does not wait; closing a console
' would stop it. The task keeps the interactive token: the .cmd starts ollama from this user's profile, where its
' model store is, and a service logon would find an empty store instead.
Option Explicit
Dim shell, cmd
Set shell = CreateObject("WScript.Shell")
cmd = "E:\foundry\etc\softserve_runner.cmd"
shell.Run "cmd /c """ & cmd & """", 0, False
