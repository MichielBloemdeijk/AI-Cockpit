# Start the AI Cockpit backend (FastAPI + uvicorn)
Set-Location "$PSScriptRoot\backend"
& "C:/Users/Sander/AppData/Local/Programs/Python/Python312/Scripts/uv.exe" run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
