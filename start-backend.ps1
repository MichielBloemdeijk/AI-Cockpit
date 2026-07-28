# Start the AI Cockpit backend (FastAPI + uvicorn)
$ErrorActionPreference = "Stop"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is not installed or is not on PATH. Install it with: python -m pip install uv"
}

Set-Location "$PSScriptRoot\backend"
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
