Option Explicit
Dim shell, fso, dir, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "pythonw.exe """ & dir & "\\faithful_remaster.py"""
shell.CurrentDirectory = dir
shell.Run cmd, 0, False
