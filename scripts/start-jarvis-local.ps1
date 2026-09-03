[CmdletBinding()]
param(
  [string]$LlamaServerPath = "",
  [string]$GemmaModelPath = "",
  [int]$LlamaPort = 8080,
  [int]$BackendPort = 8000,
  [int]$FrontendPort = 3000,
  [string]$BackendHost = "127.0.0.1",
  [string]$FrontendHost = "127.0.0.1",
  [int]$ContextSize = 16384,
  [string]$LogDirectory = "",
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backend = Join-Path $repo "backend"
$frontend = Join-Path $repo "frontend"
$pidFile = Join-Path $repo ".cockpit\jarvis\jarvis-processes.json"
$logs = if ($LogDirectory) { [IO.Path]::GetFullPath($LogDirectory) } else { Join-Path $repo ".cockpit\jarvis\logs" }

function Resolve-ConfiguredFile([string]$Configured, [string[]]$EnvironmentNames, [string[]]$Candidates, [string]$Pattern) {
  $values = @()
  if ($Configured) { $values += $Configured }
  foreach ($environmentName in $EnvironmentNames) {
    if ($environmentName -and (Get-Item "Env:$environmentName" -ErrorAction SilentlyContinue)) { $values += (Get-Item "Env:$environmentName").Value }
  }
  foreach ($value in $values) { if ($value -and (Test-Path -LiteralPath $value -PathType Leaf)) { return (Resolve-Path -LiteralPath $value).Path } }
  foreach ($candidate in $Candidates) { if (Test-Path -LiteralPath $candidate -PathType Leaf) { return (Resolve-Path -LiteralPath $candidate).Path } }
  foreach ($directory in @((Join-Path $backend "models"), (Join-Path $repo "models"))) {
    if (Test-Path -LiteralPath $directory -PathType Container) {
      $found = Get-ChildItem -LiteralPath $directory -File -Filter $Pattern -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($found) { return $found.FullName }
    }
  }
  return $null
}

function Resolve-Command([string[]]$Names) {
  foreach ($name in $Names) {
    $command = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
  }
  return $null
}

function Assert-File([string]$Path, [string]$Label) {
  if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label was not found. Run setup-jarvis.ps1 or pass an explicit path." }
}

if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
  try {
    $existing = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $running = @($existing.processes | Where-Object { Get-Process -Id $_.id -ErrorAction SilentlyContinue })
    if ($running.Count -gt 0) { throw "Jarvis is already running (PID file: $pidFile). Run stop-jarvis-local.ps1 first." }
  } catch [System.Management.Automation.RuntimeException] { if ($_.Exception.Message.StartsWith("Jarvis is already running")) { throw } }
}

$llama = Resolve-ConfiguredFile $LlamaServerPath "LLAMA_SERVER_PATH" @(
  (Join-Path $repo "llama.cpp\build\bin\llama-server.exe"),
  (Join-Path $repo "tools\llama.cpp\build\bin\llama-server.exe")
) "llama-server*"
if (-not $llama) { $llama = Resolve-Command @("llama-server.exe", "llama-server") }
if (-not $llama -and (Test-Path -LiteralPath (Join-Path $repo "tools\llama.cpp") -PathType Container)) {
  $llama = Get-ChildItem -LiteralPath (Join-Path $repo "tools\llama.cpp") -Recurse -File -Filter "llama-server.exe" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 -ExpandProperty FullName
}
$gemma = Resolve-ConfiguredFile $GemmaModelPath @("GEMMA_MODEL_PATH") @((Join-Path $backend "models\gemma-4-12b-it-qat-q4_0.gguf")) "*.gguf"
Assert-File $llama "llama-server"
Assert-File $gemma "Gemma model"
if (-not (Test-Path -LiteralPath (Join-Path $backend "pyproject.toml") -PathType Leaf)) { throw "Backend project was not found: $backend" }
if (-not (Test-Path -LiteralPath (Join-Path $frontend "package.json") -PathType Leaf)) { throw "Frontend project was not found: $frontend" }
$uv = Resolve-Command @("uv.exe", "uv")
$npm = Resolve-Command @("npm.cmd", "npm")
Assert-File $uv "uv"
Assert-File $npm "npm"

$llamaArgs = @("-m", $gemma, "--host", "127.0.0.1", "--port", "$LlamaPort", "--ctx-size", "$ContextSize", "--n-gpu-layers", "all", "--jinja", "--parallel", "1", "--reasoning", "off")
$backendArgs = @("run", "uvicorn", "app.main:app", "--host", $BackendHost, "--port", "$BackendPort")
$workerArgs = @("run", "python", "-m", "app.worker")
$frontendArgs = @("run", "dev", "--", "--hostname", $FrontendHost, "--port", "$FrontendPort")
Write-Host "llama-server: $llama $($llamaArgs -join ' ')"
Write-Host "backend: uv $($backendArgs -join ' ')"
Write-Host "worker: uv $($workerArgs -join ' ')"
Write-Host "frontend: npm $($frontendArgs -join ' ')"

if ($DryRun) {
  Write-Host "Dry run: validated paths and commands; no processes started and no PID file written."
  exit 0
}

New-Item -ItemType Directory -Path $logs -Force | Out-Null
$records = [System.Collections.Generic.List[object]]::new()
function Start-LoggedProcess([string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory, [string]$Name, [int]$StartupProbeMilliseconds = 750) {
  $stdout = Join-Path $logs "$Name.out.log"
  $stderr = Join-Path $logs "$Name.err.log"
  $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
  Start-Sleep -Milliseconds $StartupProbeMilliseconds
  if ($process.HasExited) {
    $tail = if (Test-Path -LiteralPath $stderr -PathType Leaf) { (Get-Content -LiteralPath $stderr -Tail 20 -ErrorAction SilentlyContinue) -join [Environment]::NewLine } else { "No stderr log was created." }
    throw "$Name exited during startup. Recent stderr from ${stderr}:$([Environment]::NewLine)$tail"
  }
  $startedUtc = $process.StartTime.ToUniversalTime()
  $records.Add([pscustomobject]@{
    name = $Name
    id = $process.Id
    path = $FilePath
    process_name = $process.ProcessName
    started_at = $startedUtc.ToString("o")
    started_filetime_utc = $startedUtc.ToFileTimeUtc()
  })
  Write-Host "$Name started and remained live for $StartupProbeMilliseconds ms (PID $($process.Id)); logs: $stdout"
}

function Stop-StartedProcessTree([int]$ProcessId) {
  $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if (-not $process) { return }
  & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) { Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue }
}

try {
  Start-LoggedProcess $llama $llamaArgs $repo "llama-server"
  Start-LoggedProcess $uv $backendArgs $backend "backend"
  Start-LoggedProcess $uv $workerArgs $backend "worker" 2500
  Start-LoggedProcess $npm $frontendArgs $frontend "frontend"
  foreach ($record in $records) {
    if (-not (Get-Process -Id $record.id -ErrorAction SilentlyContinue)) {
      throw "$($record.name) exited before startup completed; inspect the logs in $logs"
    }
  }
  New-Item -ItemType Directory -Path (Split-Path -Parent $pidFile) -Force | Out-Null
  $payload = [pscustomobject]@{ version = 1; processes = @($records) }
  [IO.File]::WriteAllText($pidFile, ($payload | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding($false)))
  Write-Host "Jarvis local services started. Stop them with scripts/stop-jarvis-local.ps1."
  Write-Host "Worker startup was verified. If a task shows 'waiting for worker', inspect $logs\worker.err.log immediately."
} catch {
  foreach ($record in $records) { Stop-StartedProcessTree ([int]$record.id) }
  throw
}
