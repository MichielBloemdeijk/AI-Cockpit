# Start the AI Cockpit frontend (Next.js dev server)
$ErrorActionPreference = "Stop"

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm is not installed or is not on PATH. Install Node.js 20.19+ or 22.13+."
}

Set-Location "$PSScriptRoot\frontend"
npm run dev
