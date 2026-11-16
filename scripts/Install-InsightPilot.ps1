# One-time setup. Run again after moving or renaming the project folder.
#   * Registers the "InsightPilot" scheduled task, which starts the server
#     hidden every time you sign in to Windows and keeps it running.
#   * Creates Desktop and Start Menu shortcuts (open, stop, restart).
#   * Starts the server now.
# Remove everything with Uninstall-InsightPilot.ps1.

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'InsightPilot-Common.ps1')

if (-not (Test-Path $IPPython)) { throw "Python environment not found: $IPPython" }

# ---- Always-on: start at sign-in ------------------------------------
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute 'wscript.exe' `
    -Argument "`"$IPHidden`" InsightPilot-Server.ps1" -WorkingDirectory $IPRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

$startup = Join-Path ([Environment]::GetFolderPath('Startup')) "$IPName server.lnk"
$shell = New-Object -ComObject WScript.Shell

function New-IPShortcut([string]$path, [string]$script, [string]$description) {
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
    $shortcut.Arguments = "`"$IPHidden`" $script"
    $shortcut.WorkingDirectory = $IPRoot
    $shortcut.IconLocation = "$IPIcon,0"
    $shortcut.Description = $description
    $shortcut.Save()
    Write-Host "  shortcut: $path"
}

try {
    Register-ScheduledTask -TaskName $IPName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force `
        -Description 'Keeps the InsightPilot app available at http://localhost:8501.' | Out-Null
    Remove-Item $startup -ErrorAction SilentlyContinue
    Write-Host "Auto-start: scheduled task '$IPName' (runs at sign-in)."
} catch {
    # No rights to create a task: fall back to the per-user Startup folder.
    Write-Host "Scheduled task not created ($($_.Exception.Message)); using the Startup folder."
    New-IPShortcut $startup 'InsightPilot-Server.ps1' 'Starts the InsightPilot server at sign-in'
}

# ---- Buttons ---------------------------------------------------------
$desktop = [Environment]::GetFolderPath('Desktop')
$menu = Join-Path ([Environment]::GetFolderPath('Programs')) $IPName
New-Item -ItemType Directory -Force -Path $menu | Out-Null

Write-Host 'Shortcuts:'
New-IPShortcut (Join-Path $desktop "$IPName.lnk") 'Start-InsightPilot.ps1' 'Open InsightPilot'
New-IPShortcut (Join-Path $menu "$IPName.lnk") 'Start-InsightPilot.ps1' 'Open InsightPilot'
New-IPShortcut (Join-Path $menu "Restart $IPName.lnk") 'Restart-InsightPilot.ps1' 'Restart the InsightPilot server'
New-IPShortcut (Join-Path $menu "Stop $IPName.lnk") 'Stop-InsightPilot.ps1' 'Stop the InsightPilot server'

# ---- Start now -------------------------------------------------------
if (-not (Test-IPRunning)) {
    Start-IPServer
    Write-Host 'Starting the server...'
    if (Wait-IPRunning 90) { Write-Host "Running at $IPUrl" }
    else { Write-Host "Not responding yet. Check the logs in $IPLogDir" }
} else {
    Write-Host "Already running at $IPUrl"
}
