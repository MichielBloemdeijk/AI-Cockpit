"""What can an unauthenticated caller do against the documented default configuration?

From repository root: backend/.venv/Scripts/python.exe docs/audits/repo-maintainability/evidence/default-access-probe.py
In-process ASGI calls only (no socket is opened). Settings mirror .env.example:
JARVIS_ACCESS_MODE=personal, AUTH_PASSWORD empty. Uses a fresh temporary DB and
temporary workspace roots. The Python code run is `print(6*7)`.
"""
from pathlib import Path
import asyncio
import json
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[4]
os.environ["AI_COCKPIT_ENV_FILE"] = str(ROOT / "docs/audits/repo-maintainability/evidence/no-personal-env")
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.session import close_engine, ensure_database_ready, initialize_engine, run_migrations  # noqa: E402


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="cockpit-access-", ignore_cleanup_errors=True) as folder:
        base = Path(folder)
        settings.database_url = "sqlite+aiosqlite:///" + (base / "access.db").as_posix()
        settings.conversation_workspaces_dir = str(base / "conversations")
        settings.jarvis_access_mode = "personal"
        assert settings.auth_password.get_secret_value() == ""
        await ensure_database_ready()
        run_migrations()
        initialize_engine(settings.database_url)
        from app.main import app  # lifespan is not run by ASGITransport
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://lan-peer") as client:
                before = await client.get("/api/conversations/")
                login = await client.post("/api/auth/login", json={"password": ""})
                created = await client.post("/api/conversations/", json={"title": "probe"})
                conversation_id = created.json()["conversation"]["id"]
                tool = await client.post(f"/api/conversations/{conversation_id}/tools",
                                         json={"tool": "python_execution", "code": "print(6*7)"})
            print(json.dumps({
                "unauthenticated_list_status": before.status_code,
                "empty_password_login_status": login.status_code,
                "conversation_create_status": created.status_code,
                "python_tool_status": tool.status_code,
                "python_tool_output": tool.json()["result"]["output"] if tool.status_code == 200 else tool.text[:200],
            }, indent=1))
        finally:
            await close_engine()


asyncio.run(main())
