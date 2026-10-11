[CmdletBinding()]
param([ValidateSet("Start", "Stop", "Status")][string]$Action = "Start")
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repo "backend\.venv\Scripts\python.exe"
$logs = Join-Path $repo ".cockpit\jarvis\logs"
Push-Location (Join-Path $repo "backend")
try {
    if ($Action -eq "Start") {
        $current = & $python -m app.jarvis_tasks.personal_host status | ConvertFrom-Json
        if ($current.status -eq "Stopped") {
            & $python -m app.jarvis_tasks.personal_host check
            if ($LASTEXITCODE -ne 0) { throw "Needs attention: Tailscale setup check failed." }
            New-Item -ItemType Directory -Path $logs -Force | Out-Null
            Start-Process -FilePath $python -ArgumentList @("-m", "app.jarvis_tasks.personal_host", "serve") -WorkingDirectory (Join-Path $repo "backend") -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "personal-host.out.log") -RedirectStandardError (Join-Path $logs "personal-host.err.log") | Out-Null
            $deadline = [DateTime]::UtcNow.AddSeconds(130)
            do {
                Start-Sleep -Milliseconds 700
                $current = & $python -m app.jarvis_tasks.personal_host status | ConvertFrom-Json
                if ($current.status -eq "Needs attention") { throw "Needs attention: inspect $logs" }
            } while ($current.status -ne "Ready" -and [DateTime]::UtcNow -lt $deadline)
        }
        if ($current.status -ne "Ready") { throw "Needs attention: startup did not reach Ready. Inspect $logs" }
        Write-Host "Ready - $($current.transport.public_url)/settings"
        Write-Host "Select Link phone. Stop with scripts/jarvis.ps1 Stop. Logs: $logs"
    } elseif ($Action -eq "Stop") {
        & $python -m app.jarvis_tasks.personal_host stop
        if ($LASTEXITCODE -ne 0) { throw "Shutdown was not confirmed." }
    } else {
        & $python -m app.jarvis_tasks.personal_host status
    }
} finally { Pop-Location }
