"""Shared process setup for the Android verification harnesses.

Every backend a harness starts, in this process or as a child, reads its
configuration from the fixture env file below instead of the personal `.env`.
Each scenario passes the settings it needs explicitly; a paid scenario names
the one provider credential it uses (`personal_credential`).
Import this module before anything from `app`.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
HARNESS_ENV_FILE = Path(__file__).with_name("harness.env")

os.environ["AI_COCKPIT_ENV_FILE"] = str(HARNESS_ENV_FILE)


def personal_credential(name: str) -> str:
    """One named provider credential for a paid scenario: from the environment, else the personal .env."""
    from dotenv import dotenv_values

    value = os.environ.get(name) or dotenv_values(ROOT / ".env").get(name) or ""
    if not value:
        raise RuntimeError(f"This paid scenario needs {name} in the environment or the personal .env")
    return value


def start_owned_process(args: list[str], *, cwd: Path = BACKEND,
                        env: dict[str, str] | None = None, **streams) -> subprocess.Popen:
    """Use the existing Windows Job Object host for harness-owned descendants."""
    environment = {**os.environ, **(env or {}), "AI_COCKPIT_ENV_FILE": str(HARNESS_ENV_FILE)}
    environment["PYTHONPATH"] = str(BACKEND) + os.pathsep + environment.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [sys.executable, "-m", "app.jarvis_tasks.acceptance_command_host",
         "--parent-pid", str(os.getpid()), "--", *args],
        cwd=cwd, env=environment, creationflags=subprocess.CREATE_NO_WINDOW, **streams)
    process._jarvis_job_contained = True
    return process


def start_service(args: list[str], env: dict[str, str], log_path: Path) -> subprocess.Popen:
    log = log_path.open("wb")
    try:
        process = start_owned_process([sys.executable, *args], cwd=BACKEND, env=env,
                                      stdout=log, stderr=subprocess.STDOUT)
    finally:
        log.close()
    return process
