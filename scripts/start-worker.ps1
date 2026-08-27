[CmdletBinding()]
param(
  [string]$LogDirectory = "",
  [switch]$Foreground
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backend = Join-Path $repo "backend"
$logs = if ($LogDirectory) { [IO.Path]::GetFullPath($LogDirectory) } else { Join-Path $repo ".cockpit\jarvis\logs" }
$uvCommand = Get-Command "uv" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $uvCommand) { throw "uv was not found on PATH." }
if (-not (Test-Path -LiteralPath (Join-Path $backend "pyproject.toml") -PathType Leaf)) { throw "Backend project was not found: $backend" }

if ($Foreground) {
  Push-Location $backend
  try { & $uvCommand.Source run python -m app.worker } finally { Pop-Location }
  exit $LASTEXITCODE
}

New-Item -ItemType Directory -Path $logs -Force | Out-Null
$stdout = Join-Path $logs "worker.out.log"
$stderr = Join-Path $logs "worker.err.log"
$process = Start-Process -FilePath $uvCommand.Source -ArgumentList @("run", "python", "-m", "app.worker") -WorkingDirectory $backend -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Start-Sleep -Milliseconds 750
if ($process.HasExited) { throw "Task worker exited during startup; inspect $stderr" }
Write-Host "Task worker started (PID $($process.Id)); logs: $stdout"
Write-Output $process
