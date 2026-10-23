Option Explicit
Dim fso, sh, dir, pythonw
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
pythonw = dir & "\.venv\Scripts\pythonw.exe"
If fso.FileExists(pythonw) Then
    sh.Run """" & pythonw & """ launcher.py", 0, False
Else
    sh.Run "pythonw launcher.py", 0, False
End If
