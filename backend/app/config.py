"""Application configuration loaded from environment variables."""
from __future__ import annotations

from ipaddress import ip_address
from pathlib import Path
import socket
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

    # Local OpenAI-compatible model server (for example llama.cpp)
    local_llm_enabled: bool = True
    local_llm_base_url: str = "http://127.0.0.1:8080/v1"
    local_llm_api_key: str = "local"
    local_llm_alias: str = "local/gemma-4-12b"
    local_llm_model: str = "google/gemma-4-12B-it-qat-q4_0-gguf:Q4_0"
    local_llm_context_size: int = 16384
    local_llm_max_concurrency: int = 1

    # Voice pipeline (optional local dependencies are loaded lazily)
    voice_enabled: bool = True
    voice_wake_word: str = "jarvis"
    # faster-whisper expects a CTranslate2 checkpoint.  This built-in model
    # name resolves to mobiuslabsgmbh/faster-whisper-large-v3-turbo; the
    # original OpenAI repository contains Transformers weights instead.
    voice_stt_model: str = "large-v3-turbo"
    voice_stt_device: str = "cuda"
    voice_stt_compute_type: str = "int8_float16"
    voice_stt_language: str = "en"
    voice_stt_beam_size: int = 1
    voice_tts_enabled: bool = True
    voice_tts_model_path: str = "models/kokoro-v1.0.onnx"
    voice_tts_voices_path: str = "models/voices-v1.0.bin"
    voice_tts_voice: str = "af_heart"
    voice_tts_speed: float = 1.05
    voice_cloud_model: str = "openai/gpt-5.6-terra"
    voice_max_segment_seconds: float = 30.0
    voice_max_upload_bytes: int = 4_000_000

    # Auth
    auth_password_hash: str = ""
    session_secret_key: str = "dev-secret-change-me"
    session_cookie_secure: bool = False

    # LLM Council
    available_models: str = (
        "openai/gpt-5.6-luna,"
        "openai/gpt-5.6-terra,"
        "openai/gpt-5.6-sol,"
        "google/gemini-3.5-flash,"
        "anthropic/claude-sonnet-5,"
        "anthropic/claude-opus-5,"
        "x-ai/grok-4.5"
    )
    council_models: str = (
        "openai/gpt-5.6-terra,"
        "google/gemini-3.5-flash,"
        "anthropic/claude-sonnet-5"
    )
    synthesizer_model: str = "anthropic/claude-sonnet-5"
    task_agent_model: str = "local/gemma-4-12b"
    native_tool_model_prefixes: str = (
        "local/,"
        "moonshotai/kimi-k2.6,"
        "anthropic/claude,"
        "openai/gpt-5,"
        "google/gemini-3.5,"
        "x-ai/grok-4.5,"
        "deepseek/"
    )

    # File browser
    allowed_dirs: str = ""

    # Notifications
    ntfy_topic: str = ""
    web_push_enabled: bool = False
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:admin@localhost"
    notification_retry_limit: int = 3

    # Tailscale
    tailscale_host: str = ""

    # App
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    # Persistence
    # Phase 1 intentionally starts from a clean Jarvis-era database. The
    # pre-Jarvis database remains untouched as the reversible POC fallback.
    database_url: str = "sqlite+aiosqlite:///./data/ai_cockpit_jarvis.db"
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
    def available_model_list(self) -> List[str]:
        values = [m.strip() for m in self.available_models.split(",") if m.strip()]
        if self.local_llm_enabled and self.local_llm_alias.strip():
            values.insert(0, self.local_llm_alias.strip())
        return list(dict.fromkeys(values))

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
    def local_ipv4_host_list(self) -> List[str]:
        """Return non-loopback IPv4 addresses currently assigned to this host."""
        try:
            address_info = socket.getaddrinfo(
                socket.gethostname(),
                None,
                family=socket.AF_INET,
            )
        except OSError:
            return []

        hosts: list[str] = []
        seen: set[str] = set()
        for entry in address_info:
            host = entry[4][0]
            try:
                address = ip_address(host)
            except ValueError:
                continue
            if address.version != 4 or address.is_loopback or address.is_unspecified:
                continue
            if host not in seen:
                hosts.append(host)
                seen.add(host)
        return hosts

    @property
    def cors_origins(self) -> List[str]:
        origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
        remote_hosts = [*self.tailscale_host_list, *self.local_ipv4_host_list]
        for host in remote_hosts:
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

    @property
    def cors_origin_regex(self) -> str:
        """Allow credentialed direct API requests from localhost and tailnet origins."""
        tailscale_ipv4 = r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])(?:\.\d{1,3}){2}"
        tailscale_dns = r"(?:[a-zA-Z0-9-]+\.)+ts\.net"
        return rf"^https?://(?:localhost|127\.0\.0\.1|{tailscale_ipv4}|{tailscale_dns})(?::\d+)?$"


settings = Settings()
