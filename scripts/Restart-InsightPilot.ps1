# Restarts the InsightPilot background server and opens it in the browser.

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'InsightPilot-Common.ps1')

Stop-IPServer
Start-Sleep -Seconds 2
& (Join-Path $PSScriptRoot 'Start-InsightPilot.ps1')
