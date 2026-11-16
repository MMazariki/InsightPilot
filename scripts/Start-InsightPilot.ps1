# Opens InsightPilot in the browser, starting the background server first if
# it is not already running. This is what the desktop shortcut runs.

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'InsightPilot-Common.ps1')

if (-not (Test-IPRunning)) {
    if (-not (Test-Path $IPPython)) {
        Show-IPMessage "Python environment not found:`n$IPPython" 0 48
        exit 1
    }
    Start-IPServer
    if (-not (Wait-IPRunning 90)) {
        Show-IPMessage ("InsightPilot did not start within 90 seconds.`n`n" +
            "See the log files in:`n$IPLogDir") 0 48
        exit 1
    }
}

Start-Process $IPUrl
