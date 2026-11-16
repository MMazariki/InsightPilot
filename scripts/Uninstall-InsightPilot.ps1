# Removes the auto-start task and the shortcuts, and stops the server.
# The project itself is not touched.

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'InsightPilot-Common.ps1')

Stop-IPServer
if (Get-ScheduledTask -TaskName $IPName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $IPName -Confirm:$false
}
$paths = @(
    (Join-Path ([Environment]::GetFolderPath('Startup')) "$IPName server.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) "$IPName.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Programs')) $IPName)
)
foreach ($path in $paths) {
    if (Test-Path $path) { Remove-Item $path -Recurse -Force }
}
Write-Host 'InsightPilot auto-start and shortcuts removed.'
