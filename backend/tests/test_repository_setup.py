from __future__ import annotations

from pathlib import Path

from app.config import Settings


REPO_ROOT = Path(__file__).resolve().parents[2]


def _example_environment() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def test_example_environment_is_safe_and_machine_independent() -> None:
    values = _example_environment()

    assert values["AUTH_PASSWORD_HASH"] == ""
    assert values["ALLOWED_DIRS"] == ""
    assert values["SESSION_SECRET_KEY"]
    assert values["TASK_AGENT_MODEL"] == "anthropic/claude-haiku-4.5"
    assert "Sander" not in (REPO_ROOT / ".env.example").read_text(encoding="utf-8")


def test_backend_launcher_resolves_uv_from_path() -> None:
    launcher = (REPO_ROOT / "start-backend.ps1").read_text(encoding="utf-8")

    assert "Get-Command uv" in launcher
    assert "uv run uvicorn" in launcher
    assert "C:/Users/" not in launcher


def test_readme_quick_start_is_copyable_and_current() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "git clone https://github.com/MichielBloemdeijk/AI-Cockpit.git" in readme
    assert "uv sync" in readme
    assert "npm ci" in readme
    assert "frontend/generated-apps/<slug>" in readme
    assert "<repo-url>" not in readme
    assert "PLAN.md" not in readme


def test_default_council_models_avoid_retired_gemini_slug() -> None:
    default_models = str(Settings.model_fields["council_models"].default)

    assert "google/gemini-2.5-pro" in default_models
    assert "google/gemini-pro-1.5" not in default_models
