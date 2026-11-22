' Mothership: starts the order taker's .cmd with no console window (style 0) and does not wait, so wscript exits at
' once (no wscript process is the healthy state). The task runs as the signed-in user: the .cmd uses that user's Python.
CreateObject("Wscript.Shell").Run "cmd /c ""F:\foundry\etc\run_foundry_queue.cmd""", 0, False
