# InsightPilot background server.
# Keeps the Streamlit app running on http://localhost:8501 and restarts it if
# it exits. Started hidden at logon by the "InsightPilot" scheduled task (see
# Install-InsightPilot.ps1). Stop it with Stop-InsightPilot.ps1.

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'InsightPilot-Common.ps1')

# Only one server per user session.
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\InsightPilotServer', [ref]$createdNew)
if (-not $createdNew) { exit 0 }

Set-Location $IPRoot
New-Item -ItemType Directory -Force -Path $IPLogDir | Out-Null
Remove-Item $IPStopFlag -ErrorAction SilentlyContinue

# Local structured pipeline unless the user chose another agent.
if (-not $env:INSIGHTPILOT_AGENT) { $env:INSIGHTPILOT_AGENT = 'structured' }
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'

function Write-ServerLog([string]$message) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message
    Add-Content -Path (Join-Path $IPLogDir 'server.log') -Value $line -Encoding UTF8
}

function Start-OllamaIfNeeded {
    if (Test-IPUrl 'http://localhost:11434/api/tags') { return }
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollama) {
        Write-ServerLog 'Ollama is not responding and was not found on PATH.'
        return
    }
    Write-ServerLog 'Ollama is not responding; starting "ollama serve".'
    Start-Process -FilePath $ollama.Source -ArgumentList 'serve' -WindowStyle Hidden
}

$streamlitArgs = @(
    '-m', 'streamlit', 'run', 'src/main.py',
    '--server.port', "$IPPort",
    '--server.address', 'localhost',
    '--server.headless', 'true',
    '--server.enableStaticServing', 'true',
    '--browser.gatherUsageStats', 'false',
    '--theme.base', 'light',
    '--theme.primaryColor', '#2251D1',
    '--theme.backgroundColor', '#F4F6FB',
    '--theme.secondaryBackgroundColor', '#EEF1F7',
    '--theme.textColor', '#101828'
)

Write-ServerLog "Server supervisor started (agent: $env:INSIGHTPILOT_AGENT)."

# Alerts run in a process of their own, so they work even when nobody has
# the app open. Started when none is running (the worker also refuses to
# run twice: it holds a lock). A failure here never stops the app.
function Start-AlertsWorker {
    try {
        $running = Get-CimInstance Win32_Process | Where-Object {
            $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -and
            $_.CommandLine -match 'alerts_worker\.py' }
        if ($running) { return }
        Start-Process -FilePath $IPPython -ArgumentList @('scripts/alerts_worker.py') `
            -WorkingDirectory $IPRoot -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $IPLogDir 'alerts_worker.log') `
            -RedirectStandardError (Join-Path $IPLogDir 'alerts_worker.err.log') | Out-Null
        Write-ServerLog 'Alerts worker started.'
    } catch {
        Write-ServerLog "Alerts worker could not start: $($_.Exception.Message)"
    }
}

try {
    while (-not (Test-Path $IPStopFlag)) {
        Start-OllamaIfNeeded
        Start-AlertsWorker

        $out = Join-Path $IPLogDir 'app.out.log'
        $err = Join-Path $IPLogDir 'app.err.log'
        foreach ($file in @($out, $err)) {
            if (Test-Path $file) { Move-Item $file "$file.prev" -Force }
        }

        $started = Get-Date
        $process = Start-Process -FilePath $IPPython -ArgumentList $streamlitArgs `
            -WorkingDirectory $IPRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $out -RedirectStandardError $err
        Write-ServerLog "App started (PID $($process.Id))."
        $process.WaitForExit()
        Write-ServerLog "App exited with code $($process.ExitCode)."

        if (Test-Path $IPStopFlag) { break }
        # Back off if the app cannot stay up, so a broken start does not spin.
        $ranFor = ((Get-Date) - $started).TotalSeconds
        if ($ranFor -lt 30) { Start-Sleep -Seconds 30 } else { Start-Sleep -Seconds 3 }
    }
}
finally {
    Write-ServerLog 'Server supervisor stopped.'
    $mutex.ReleaseMutex()
}
