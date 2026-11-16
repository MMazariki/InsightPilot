# Stops the InsightPilot background server. It starts again at the next
# sign-in, or when the InsightPilot shortcut is used.

param([switch]$Quiet)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'InsightPilot-Common.ps1')

Stop-IPServer
if (-not $Quiet) {
    Show-IPMessage 'InsightPilot has been stopped.' 4
}
