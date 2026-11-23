' llama-swap's headless launcher: runs llama_swap_serve.cmd from this file's own folder with no console window (style
' 0), and waits (True), so the task's 15-minute repeat with IgnoreNew re-fires only when llama-swap has gone.
Set fso = CreateObject("Scripting.FileSystemObject")
CreateObject("Wscript.Shell").Run "cmd /c """ & fso.BuildPath(fso.GetParentFolderName(WScript.ScriptFullName), "llama_swap_serve.cmd") & """", 0, True
