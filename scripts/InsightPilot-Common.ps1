# Shared settings and helpers for the InsightPilot launcher scripts.

$IPName     = 'InsightPilot'
$IPRoot     = Split-Path -Parent $PSScriptRoot
$IPPort     = 8501   # fixed: the Power BI sign-in redirect is registered for it
$IPUrl      = "http://localhost:$IPPort"
$IPPython   = Join-Path $IPRoot '.venv\Scripts\python.exe'
$IPLogDir   = Join-Path $IPRoot 'logs'
$IPStopFlag = Join-Path $IPLogDir 'stop.flag'
$IPIcon     = Join-Path $IPRoot 'assets\insightpilot.ico'
$IPHidden   = Join-Path $PSScriptRoot 'run-hidden.vbs'

function Test-IPUrl([string]$url) {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Test-IPRunning {
    return (Test-IPUrl "$IPUrl/_stcore/health")
}

function Show-IPMessage([string]$text, [int]$seconds = 0, [int]$icon = 64) {
    # 64 = information, 48 = warning. seconds = 0 waits for OK.
    $shell = New-Object -ComObject WScript.Shell
    [void]$shell.Popup($text, $seconds, $IPName, $icon)
}

function Start-IPServer {
    $task = Get-ScheduledTask -TaskName $IPName -ErrorAction SilentlyContinue
    if ($task) {
        Start-ScheduledTask -TaskName $IPName
    } else {
        Start-Process -FilePath 'wscript.exe' `
            -ArgumentList "`"$IPHidden`" InsightPilot-Server.ps1"
    }
}

function Stop-IPServer {
    New-Item -ItemType Directory -Force -Path $IPLogDir | Out-Null
    Set-Content -Path $IPStopFlag -Value 'stop' -Encoding ASCII

    $task = Get-ScheduledTask -TaskName $IPName -ErrorAction SilentlyContinue
    if ($task -and $task.State -eq 'Running') {
        Stop-ScheduledTask -TaskName $IPName -ErrorAction SilentlyContinue
    }

    Get-CimInstance Win32_Process | Where-Object {
        $_.ProcessId -ne $PID -and $_.CommandLine -and (
            ($_.CommandLine -match 'InsightPilot-Server\.ps1') -or
            ($_.Name -match '^python(w)?\.exe$' -and
             ($_.CommandLine -match 'streamlit\s+run\s+src[/\\]main\.py' -or
              $_.CommandLine -match 'alerts_worker\.py'))
        )
    } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Wait-IPRunning([int]$timeoutSeconds = 90) {
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-IPRunning) { return $true }
        Start-Sleep -Milliseconds 700
    }
    return $false
}
