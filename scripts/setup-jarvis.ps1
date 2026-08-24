param(
  [string]$GemmaModelPath = "",
  [string]$KokoroModelPath = "",
  [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repo "backend"

function Require-File([string]$Path, [string]$Label) {
  if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "$Label was not found. Supply its path explicitly; model binaries are never committed."
  }
}

if ($GemmaModelPath) { Require-File $GemmaModelPath "Gemma GGUF" }
if ($KokoroModelPath) { Require-File $KokoroModelPath "Kokoro ONNX model" }

if (-not $CheckOnly) {
  Push-Location $backend
  try { uv sync --group voice --group notifications } finally { Pop-Location }
}

if (-not $env:SESSION_SECRET_KEY -or $env:SESSION_SECRET_KEY.Length -lt 32) {
  Write-Warning "Set a non-default SESSION_SECRET_KEY of at least 32 characters before phone testing."
}
if (-not $env:AUTH_PASSWORD_HASH) {
  Write-Warning "Set AUTH_PASSWORD_HASH to a real bcrypt hash before phone testing."
}
if ($env:SESSION_COOKIE_SECURE -ne "true") {
  Write-Warning "Set SESSION_COOKIE_SECURE=true when using the Tailscale HTTPS origin."
}
Write-Host "Jarvis prerequisites checked. Keep llama.cpp bound to 127.0.0.1:8080 and expose only AI-Cockpit through Tailscale Serve."
