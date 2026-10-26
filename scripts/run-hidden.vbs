' Run a .bat from this folder with no visible window, e.g.: wscript run-hidden.vbs backup.bat
Set sh = CreateObject("WScript.Shell")
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.Run "cmd /c """ & dir & "\" & WScript.Arguments(0) & """", 0, False
