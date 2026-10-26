' Start dafeiyu (AstrBot + NapCat) in the background with no visible window.
' Logs: runtime\astrbot\data\logs\astrbot.log and runtime\napcat-shell\logs\
Set sh = CreateObject("WScript.Shell")
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.Run "cmd /c """ & dir & "\start-dafeiyu.bat""", 0, False
