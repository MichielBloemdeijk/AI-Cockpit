[CmdletBinding()]
param(
  [string]$LlamaServerPath = "",
  [string]$GemmaModelPath = "",
  [string]$EnvPath = "",
  [switch]$GenerateVapidKeys,
  [switch]$CheckOnly,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backend = Join-Path $repo "backend"
$envFile = if ($EnvPath) { [IO.Path]::GetFullPath($EnvPath) } else { Join-Path $repo ".env" }
$readOnly = $CheckOnly -or $DryRun
$missing = [System.Collections.Generic.List[string]]::new()

function Find-CommandPath([string[]]$Names) {
  foreach ($name in $Names) {
    $command = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
  }
  return $null
}

function Find-FilePath([string]$Configured, [string[]]$EnvironmentNames, [string[]]$Candidates, [string]$Pattern) {
  $values = @()
  if ($Configured) { $values += $Configured }
  foreach ($environmentName in $EnvironmentNames) {
    if ($environmentName -and (Get-Item "Env:$environmentName" -ErrorAction SilentlyContinue)) {
      $values += (Get-Item "Env:$environmentName").Value
    }
  }
  foreach ($value in $values) {
    if ($value -and (Test-Path -LiteralPath $value -PathType Leaf)) { return (Resolve-Path -LiteralPath $value).Path }
  }
  foreach ($candidate in $Candidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return (Resolve-Path -LiteralPath $candidate).Path }
  }
  foreach ($directory in @((Join-Path $backend "models"), (Join-Path $repo "models"))) {
    if (Test-Path -LiteralPath $directory -PathType Container) {
      $found = Get-ChildItem -LiteralPath $directory -File -Filter $Pattern -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($found) { return $found.FullName }
    }
  }
  return $null
}

function Get-EnvValue([string]$Name, [string]$Text) {
  $match = [regex]::Match($Text, "(?m)^\s*" + [regex]::Escape($Name) + "\s*=\s*(.*)\s*$")
  if (-not $match.Success) { return "" }
  return $match.Groups[1].Value.Trim().Trim('"').Trim("'")
}

function Set-MissingEnvEntries([string]$Path, [hashtable]$Entries) {
  $text = if (Test-Path -LiteralPath $Path -PathType Leaf) { [IO.File]::ReadAllText($Path) } else { "" }
  $additions = [System.Collections.Generic.List[string]]::new()
  foreach ($name in $Entries.Keys) {
    if (-not (Get-EnvValue $name $text)) {
      $additions.Add($name)
      $entryPattern = "(?m)^\s*" + [regex]::Escape($name) + "\s*=.*$"
      $existingEntry = [regex]::Match($text, $entryPattern)
      $renderedEntry = "$name=$($Entries[$name])"
      if ($existingEntry.Success) {
        $text = $text.Substring(0, $existingEntry.Index) + $renderedEntry + $text.Substring($existingEntry.Index + $existingEntry.Length)
      } else {
        $text = $text.TrimEnd() + "`r`n$renderedEntry`r`n"
      }
    }
  }
  if ($additions.Count -eq 0) {
    Write-Host "Environment file unchanged (all requested entries already have values)."
    return
  }
  if ($readOnly) {
    Write-Host "Dry run: would add missing .env entries: $($additions -join ', ')"
    return
  }
  $directory = Split-Path -Parent $Path
  if (-not (Test-Path -LiteralPath $directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
  $temporary = Join-Path $directory ("." + [IO.Path]::GetRandomFileName() + ".tmp")
  try {
    [IO.File]::WriteAllText($temporary, $text, (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $Path -Force
    Write-Host "Updated .env with missing entries only: $($additions -join ', ')"
  } finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
  }
}

function New-VapidPair {
  $python = Find-CommandPath @("python", "python3")
  if (-not $python -and -not $uv) { throw "Python or uv is required to generate VAPID keys." }
  $code = @'
import base64, json
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

key = ec.generate_private_key(ec.SECP256R1())
private = key.private_numbers().private_value.to_bytes(32, "big")
public = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
b64 = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
print(json.dumps({"private": b64(private), "public": b64(public)}))
'@
  $temporary = Join-Path ([IO.Path]::GetTempPath()) ("jarvis-vapid-" + [guid]::NewGuid().ToString("N") + ".py")
  try {
    [IO.File]::WriteAllText($temporary, $code, (New-Object Text.UTF8Encoding($false)))
    if ($uv) { $output = & $uv run --project $backend --group notifications python $temporary 2>$null }
    else { $output = & $python $temporary 2>$null }
    if ($LASTEXITCODE -ne 0 -or -not $output) { throw "VAPID key generation failed. Run setup without -CheckOnly after syncing the notifications group." }
    return (($output -join "`n") | ConvertFrom-Json)
  } finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
  }
}

Write-Host "Jarvis preflight: $repo"

$llama = Find-FilePath $LlamaServerPath @("LLAMA_SERVER_PATH") @() "llama-server*"
if (-not $llama) { $llama = Find-CommandPath @("llama-server.exe", "llama-server") }
if (-not $llama) {
  foreach ($candidate in @(
    (Join-Path $repo "llama.cpp\build\bin\llama-server.exe"),
    (Join-Path $repo "tools\llama.cpp\build\bin\llama-server.exe")
  )) { if (Test-Path -LiteralPath $candidate -PathType Leaf) { $llama = (Resolve-Path $candidate).Path; break } }
}
if (-not $llama -and (Test-Path -LiteralPath (Join-Path $repo "tools\llama.cpp") -PathType Container)) {
  $llama = Get-ChildItem -LiteralPath (Join-Path $repo "tools\llama.cpp") -Recurse -File -Filter "llama-server.exe" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 -ExpandProperty FullName
}
$gemma = Find-FilePath $GemmaModelPath "GEMMA_MODEL_PATH" @((Join-Path $backend "models\gemma-4-12b-it-qat-q4_0.gguf")) "*.gguf"

if ($llama) { Write-Host "[ok] llama-server: $llama" } else { $missing.Add("llama-server (set -LlamaServerPath or LLAMA_SERVER_PATH)") }
if ($gemma) { Write-Host "[ok] Gemma model: $gemma" } else { $missing.Add("Gemma GGUF (set -GemmaModelPath or GEMMA_MODEL_PATH)") }

if (-not (Find-CommandPath @("uv.exe", "uv"))) { $missing.Add("uv package manager") }
if (-not (Find-CommandPath @("npm.cmd", "npm"))) { $missing.Add("npm") }

$envText = if (Test-Path -LiteralPath $envFile -PathType Leaf) { [IO.File]::ReadAllText($envFile) } else { "" }
$sessionSecret = Get-EnvValue "SESSION_SECRET_KEY" $envText
if (-not $sessionSecret -or $sessionSecret.Length -lt 32) { $missing.Add("SESSION_SECRET_KEY (32+ characters in .env)") }
if (-not (Get-EnvValue "OPENAI_API_KEY" $envText)) { $missing.Add("OPENAI_API_KEY (required for GPT-Live Jarvis in .env)") }
if (-not (Get-EnvValue "AUTH_PASSWORD" $envText)) { Write-Warning "AUTH_PASSWORD is empty; personal mode can use signed sessions without a password." }
if ((Get-EnvValue "SESSION_COOKIE_SECURE" $envText).ToLowerInvariant() -ne "true") { Write-Warning "SESSION_COOKIE_SECURE is not true; enable it before HTTPS phone testing." }

$tailscale = Find-CommandPath @("tailscale.exe", "tailscale")
if ($tailscale) {
  try {
    $state = (& $tailscale status --json 2>$null | ConvertFrom-Json).BackendState
    if ($state -eq "Running") { Write-Host "[ok] Tailscale: $state" }
    else { Write-Warning "Tailscale is installed but not connected (state: $state). Run 'tailscale up' before phone testing." }
  } catch { Write-Warning "Tailscale is installed but not running (run 'tailscale up' before phone testing)." }
} else { Write-Warning "Tailscale CLI not found; phone access will be limited to this machine." }

$uv = Find-CommandPath @("uv.exe", "uv")
if (-not $readOnly -and $uv) {
  $uvGroups = @("--group", "notifications")
  if (Select-String -LiteralPath (Join-Path $backend "pyproject.toml") -Pattern "^benchmark\s*=" -Quiet) {
    $uvGroups += @("--group", "benchmark")
  }
  Push-Location $backend
  try { & $uv sync @uvGroups } finally { Pop-Location }
} else { Write-Host "Dry run/check-only: skipped uv dependency sync." }

if ($GenerateVapidKeys) {
  if ($readOnly) {
    Write-Host "Dry run: would generate VAPID keys and fill only blank WEB_PUSH_ENABLED/VAPID_* entries."
  } elseif ((Get-EnvValue "VAPID_PUBLIC_KEY" $envText) -and (Get-EnvValue "VAPID_PRIVATE_KEY" $envText)) {
    Set-MissingEnvEntries $envFile @{ WEB_PUSH_ENABLED = "true" }
    Write-Host "VAPID keys already configured; preserving existing values."
  } else {
    $pair = New-VapidPair
    Set-MissingEnvEntries $envFile @{
      WEB_PUSH_ENABLED = "true"
      VAPID_PUBLIC_KEY = $pair.public
      VAPID_PRIVATE_KEY = $pair.private
    }
    Write-Host "VAPID keys generated without printing secret values."
  }
}

if ($missing.Count -gt 0) {
  Write-Warning "Remaining prerequisites:"
  foreach ($item in $missing) { Write-Warning "- $item" }
  if (-not $readOnly) { exit 1 }
} else { Write-Host "All discovered prerequisites are present." }
Write-Host "The launcher binds llama-server, backend, and frontend to loopback; use Tailscale Serve for HTTPS phone access."
