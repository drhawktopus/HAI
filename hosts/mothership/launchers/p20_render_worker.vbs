' Mothership: starts the render worker's .cmd with no console window (style 0) and does not wait. Its settings live
' in the .cmd, so there is one place to edit.
CreateObject("Wscript.Shell").Run "cmd /c ""F:\foundry\etc\headless\p20_render_worker.cmd""", 0, False
