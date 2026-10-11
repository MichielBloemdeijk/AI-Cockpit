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

    assert values["AUTH_PASSWORD"] == ""
    assert values["ALLOWED_DIRS"] == ""
    assert values["SESSION_SECRET_KEY"]
    assert values["TASK_AGENT_MODEL"] == "local/gemma-4-12b"
    assert values["AVAILABLE_MODELS"].split(",") == Settings().available_model_list
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
    assert "](PLAN.md)" not in readme


def test_default_models_use_current_openrouter_slugs() -> None:
    default_models = str(Settings.model_fields["council_models"].default)

    assert "openai/gpt-5.6-terra" in default_models
    assert "google/gemini-3.5-flash" in default_models
    assert "anthropic/claude-sonnet-5" in default_models
    assert Settings.model_fields["task_agent_model"].default == "local/gemma-4-12b"
