' Start the Bingo Helper with no console window.
' Double-click this file, or use the desktop shortcut created by install_shortcut.ps1.
Option Explicit

Dim fso, sh, base
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base

On Error Resume Next
sh.Run "pythonw.exe """ & base & "\app.py""", 0, False
If Err.Number <> 0 Then
    MsgBox "Could not start pythonw.exe." & vbCrLf & vbCrLf & _
           "Run start.bat from this folder instead, or make sure Python is on PATH.", _
           16, "Bingo Helper"
End If
