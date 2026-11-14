Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -match "streamlit\s+run\s+src[/\\]main\.py" -and
        $_.Name -match "^python(w)?\.exe$"
    } |
    ForEach-Object {
        Write-Host "Stopping Streamlit PID:" $_.ProcessId
        Stop-Process -Id $_.ProcessId -Force
    }