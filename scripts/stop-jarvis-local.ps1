[CmdletBinding()]
param([string]$PidFile = "")

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pidPath = if ($PidFile) { [IO.Path]::GetFullPath($PidFile) } else { Join-Path $repo ".cockpit\jarvis\jarvis-processes.json" }
$allowedPidRoot = [IO.Path]::GetFullPath((Join-Path $repo ".cockpit"))
if (-not $pidPath.StartsWith($allowedPidRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
  throw "PidFile must be inside the repository .cockpit directory."
}
if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) { Write-Host "No Jarvis PID file found; nothing to stop."; exit 0 }

$payload = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
foreach ($record in @($payload.processes)) {
  $process = Get-Process -Id ([int]$record.id) -ErrorAction SilentlyContinue
  if (-not $process) { Write-Host "$($record.name) is not running."; continue }
  $sameStart = $false
  try {
    if ($record.started_filetime_utc) {
      $recordedFileTime = [long]$record.started_filetime_utc
      $actualFileTime = $process.StartTime.ToUniversalTime().ToFileTimeUtc()
      $sameStart = [math]::Abs($actualFileTime - $recordedFileTime) -lt 50000000
    } else {
      $recordedStart = [datetimeoffset]::Parse([string]$record.started_at).UtcDateTime
      $sameStart = [math]::Abs(($process.StartTime.ToUniversalTime() - $recordedStart).TotalSeconds) -lt 5
    }
  } catch { $sameStart = $false }
  if ($record.process_name -and $process.ProcessName -ne [string]$record.process_name) { $sameStart = $false }
  if (-not $sameStart) { Write-Warning "Skipping PID $($record.id): it appears to have been reused."; continue }
  & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
  Write-Host "Stopped $($record.name) (PID $($record.id))."
}
Remove-Item -LiteralPath $pidPath -Force
Write-Host "Jarvis PID file removed. Logs were preserved."
