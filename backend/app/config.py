"""Application configuration loaded from environment variables."""
from __future__ import annotations

from pathlib import Path
from typing import List, Literal
from urllib.parse import urlparse

from sqlalchemy.engine import make_url

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent.parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_prompt_caching_enabled: bool = True
    openrouter_prompt_cache_ttl: Literal["5m", "1h"] = "5m"
    openrouter_explicit_cache_breakpoint_min_bytes: int = 4096

    # Auth
    auth_password_hash: str = ""
    session_secret_key: str = "dev-secret-change-me"

    # LLM Council
    council_models: str = (
        "anthropic/claude-sonnet-4-5,"
        "google/gemini-pro-1.5,"
        "openai/gpt-4o"
    )
    synthesizer_model: str = "anthropic/claude-sonnet-4-5"
    task_agent_model: str = "anthropic/claude-haiku-4.5"
    native_tool_model_prefixes: str = "moonshotai/kimi-k2.6,anthropic/claude,openai/gpt-5,deepseek/"

    # File browser
    allowed_dirs: str = ""

    # Notifications
    ntfy_topic: str = ""

    # Tailscale
    tailscale_host: str = ""

    # App
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    # Persistence
    database_url: str = "sqlite+aiosqlite:///./data/ai_cockpit.db"
    alembic_ini_path: str = "alembic.ini"
    conversation_workspaces_dir: str = "../.cockpit/conversations"
    knowledge_notes_dir: str = "../knowledge/notes"
    knowledge_preferences_file: str = "../knowledge/preferences.yaml"
    verifier_generated_apps_dir: str = "data/verifier-runs"
    conversation_context_message_limit: int = 24
    conversation_compaction_enabled: bool = True
    conversation_compaction_trigger_messages: int = 18
    conversation_compaction_keep_tail_messages: int = 8
    conversation_microcompact_keep_recent_messages: int = 6
    conversation_microcompact_max_chars: int = 1200
    conversation_compaction_summary_max_tokens: int = 700
    conversation_cache_cold_keep_recent_messages: int = 2
    conversation_cache_cold_message_limit: int = 12
    conversation_compaction_guardrail_max_chars: int = 4000
    llm_transport_retry_attempts: int = 2
    llm_transport_retry_base_delay_ms: int = 400

    @property
    def council_model_list(self) -> List[str]:
        return [m.strip() for m in self.council_models.split(",") if m.strip()]

    @property
    def native_tool_model_prefix_list(self) -> List[str]:
        return [m.strip().lower() for m in self.native_tool_model_prefixes.split(",") if m.strip()]

    @property
    def backend_root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    @property
    def alembic_config_path(self) -> Path:
        configured_path = Path(self.alembic_ini_path)
        if configured_path.is_absolute():
            return configured_path
        return self.backend_root / configured_path

    @property
    def database_path(self) -> Path | None:
        url = make_url(self.database_url)
        if not url.drivername.startswith("sqlite"):
            return None
        if url.database in (None, "", ":memory:"):
            return None

        db_path = Path(url.database)
        if db_path.is_absolute():
            return db_path
        return self.backend_root / db_path

    @property
    def knowledge_notes_path(self) -> Path:
        configured_path = Path(self.knowledge_notes_dir)
        if configured_path.is_absolute():
            return configured_path
        return self.backend_root / configured_path

    @property
    def conversation_workspaces_path(self) -> Path:
        configured_path = Path(self.conversation_workspaces_dir)
        if configured_path.is_absolute():
            return configured_path
        return self.backend_root / configured_path

    @property
    def knowledge_preferences_path(self) -> Path:
        configured_path = Path(self.knowledge_preferences_file)
        if configured_path.is_absolute():
            return configured_path
        return self.backend_root / configured_path

    @property
    def verifier_generated_apps_path(self) -> Path:
        configured_path = Path(self.verifier_generated_apps_dir)
        if configured_path.is_absolute():
            return configured_path
        return self.backend_root / configured_path

    @property
    def allowed_dir_list(self) -> List[Path]:
        if not self.allowed_dirs:
            return []
        return [Path(d.strip()) for d in self.allowed_dirs.split(",") if d.strip()]

    @property
    def tailscale_host_list(self) -> List[str]:
        if not self.tailscale_host:
            return []

        hosts: list[str] = []
        seen: set[str] = set()
        for raw_value in self.tailscale_host.split(","):
            candidate = raw_value.strip()
            if not candidate:
                continue

            if "://" in candidate:
                parsed = urlparse(candidate)
                normalized_host = parsed.hostname or ""
            else:
                normalized_host = candidate.split("/", 1)[0]
                if normalized_host.startswith("[") and "]" in normalized_host:
                    normalized_host = normalized_host[1:].split("]", 1)[0]
                elif normalized_host.count(":") == 1:
                    normalized_host = normalized_host.rsplit(":", 1)[0]

            normalized_host = normalized_host.strip()
            if normalized_host and normalized_host not in seen:
                hosts.append(normalized_host)
                seen.add(normalized_host)

        return hosts

    @property
    def cors_origins(self) -> List[str]:
        origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
        for host in self.tailscale_host_list:
            origins.extend(
                [
                    f"http://{host}",
                    f"http://{host}:3000",
                    f"https://{host}",
                    f"https://{host}:3000",
                ]
            )
        origins = list(dict.fromkeys(origins))
        return origins


settings = Settings()
