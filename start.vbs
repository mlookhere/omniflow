' OmniFlow launcher: no visible console window.
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
py = folder & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then
    MsgBox "Run setup.bat before launching OmniFlow.", 48, "OmniFlow"
    WScript.Quit 1
End If
shell.CurrentDirectory = folder
shell.Run Chr(34) & py & Chr(34) & " " & Chr(34) & folder & "\omniflow_watchdog.py" & Chr(34), 0, False
