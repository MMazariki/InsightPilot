' Runs one of the InsightPilot PowerShell scripts with no console window.
' Usage: wscript run-hidden.vbs <Script.ps1>
Option Explicit
Dim shell, fso, folder, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & _
    folder & "\" & WScript.Arguments(0) & """"
WScript.Quit shell.Run(command, 0, True)
