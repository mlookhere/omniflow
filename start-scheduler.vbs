' Headless schedule agent, no GUI. Start once at Windows logon.
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
py = folder & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then
    MsgBox "Run setup.bat before starting the scheduler.", 48, "OmniFlow"
    WScript.Quit 1
End If
shell.CurrentDirectory = folder
shell.Run Chr(34) & py & Chr(34) & " " & Chr(34) & folder & "\schedule_agent.py" & Chr(34), 0, False
