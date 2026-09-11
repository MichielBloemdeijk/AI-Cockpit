"""Application configuration loaded from environment variables."""
from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
import socket
from typing import List, Literal
from urllib.parse import urlparse

from sqlalchemy.engine import make_url

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


JarvisRealtimeTurnMode = Literal["server_vad", "semantic_vad", "client_vad"]
JarvisRealtimeCommitOwner = Literal["provider", "client"]


@dataclass(frozen=True, slots=True)
class RealtimeAudioPolicy:
    """Immutable turn-detection policy used for one Realtime session.

    The policy is deliberately small and serializable.  Keeping it separate
    from ``Settings`` prevents a running session from changing semantics when
    an experiment setting is edited or a process reloads.
    """

    configuration_id: str
    turn_mode: JarvisRealtimeTurnMode
    commit_owner: JarvisRealtimeCommitOwner
    silence_duration_ms: int | None
    vad_threshold: float | None
    prefix_padding_ms: int | None
    semantic_eagerness: Literal["low", "medium", "high", "auto"] | None
    create_response: Literal[False] = False
    interrupt_response: Literal[False] = False

    def __post_init__(self) -> None:
        if self.turn_mode not in {"server_vad", "semantic_vad", "client_vad"}:
            raise ValueError("Unsupported Realtime turn mode")
        if self.commit_owner not in {"provider", "client"}:
            raise ValueError("Unsupported Realtime commit owner")
        if self.semantic_eagerness is not None and self.semantic_eagerness not in {"low", "medium", "high", "auto"}:
            raise ValueError("Unsupported semantic VAD eagerness")
        if not self.configuration_id.strip():
            raise ValueError("Realtime audio policy requires a configuration id")
        if self.create_response or self.interrupt_response:
            raise ValueError("Realtime audio policy must disable provider-owned responses and interrupts")
        if self.turn_mode == "client_vad" and self.commit_owner != "client":
            raise ValueError("client_vad requires the client commit owner")
        if self.turn_mode != "client_vad" and self.commit_owner != "provider":
            raise ValueError("provider VAD modes require the provider commit owner")
        if self.commit_owner == "provider" and self.turn_mode == "client_vad":
            raise ValueError("client_vad cannot use provider-owned commits")
        if self.turn_mode == "server_vad":
            if self.silence_duration_ms is None or not 100 <= self.silence_duration_ms <= 2_000:
                raise ValueError("server VAD silence must be between 100 and 2000 ms")
            if self.vad_threshold is None or not 0.0 <= self.vad_threshold <= 1.0:
                raise ValueError("server VAD threshold must be between 0 and 1")
            if self.prefix_padding_ms is None or not 0 <= self.prefix_padding_ms <= 2_000:
                raise ValueError("server VAD prefix must be between 0 and 2000 ms")
            if self.semantic_eagerness is not None:
                raise ValueError("server_vad cannot include semantic eagerness")
        elif self.turn_mode == "semantic_vad":
            if self.semantic_eagerness is None:
                raise ValueError("semantic_vad requires semantic eagerness")
            if any(
                value is not None
                for value in (self.silence_duration_ms, self.vad_threshold, self.prefix_padding_ms)
            ):
                raise ValueError("semantic_vad cannot include server VAD parameters")
        elif any(
            value is not None
            for value in (
                self.silence_duration_ms,
                self.vad_threshold,
                self.prefix_padding_ms,
                self.semantic_eagerness,
            )
        ):
            raise ValueError("client_vad cannot include provider VAD parameters")

    def as_dict(self) -> dict[str, object]:
        return {
            "configuration_id": self.configuration_id,
            "turn_mode": self.turn_mode,
            "commit_owner": self.commit_owner,
            "silence_duration_ms": self.silence_duration_ms,
            "vad_threshold": self.vad_threshold,
            "prefix_padding_ms": self.prefix_padding_ms,
            "semantic_eagerness": self.semantic_eagerness,
            "create_response": False,
            "interrupt_response": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RealtimeAudioPolicy | None":
        if not isinstance(value, dict):
            return None
        try:
            return cls(
                configuration_id=str(value["configuration_id"]),
                turn_mode=value["turn_mode"],  # type: ignore[arg-type]
                commit_owner=value["commit_owner"],  # type: ignore[arg-type]
                silence_duration_ms=value.get("silence_duration_ms"),  # type: ignore[arg-type]
                vad_threshold=value.get("vad_threshold"),  # type: ignore[arg-type]
                prefix_padding_ms=value.get("prefix_padding_ms"),  # type: ignore[arg-type]
                semantic_eagerness=value.get("semantic_eagerness"),  # type: ignore[arg-type]
                create_response=value.get("create_response", False),  # type: ignore[arg-type]
                interrupt_response=value.get("interrupt_response", False),  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError):
            return None

    def provider_turn_detection(self) -> dict[str, object] | None:
        if self.turn_mode == "client_vad":
            return None
        if self.turn_mode == "semantic_vad":
            return {
                "type": "semantic_vad",
                "eagerness": self.semantic_eagerness,
                "create_response": False,
                "interrupt_response": False,
            }
        return {
            "type": "server_vad",
            "threshold": self.vad_threshold,
            "prefix_padding_ms": self.prefix_padding_ms,
            "silence_duration_ms": self.silence_duration_ms,
            "create_response": False,
            "interrupt_response": False,
        }


def build_realtime_audio_policy(config: "Settings") -> RealtimeAudioPolicy:
    """Build the effective immutable policy for a newly-created session."""

    turn_mode = config.jarvis_realtime_turn_mode
    if turn_mode == "server_vad":
        threshold = float(config.jarvis_realtime_server_vad_threshold)
        silence = int(config.jarvis_realtime_server_vad_silence_ms)
        prefix = int(config.jarvis_realtime_server_vad_prefix_ms)
        return RealtimeAudioPolicy(
            configuration_id=f"server_vad:s{silence}:t{threshold:g}:p{prefix}",
            turn_mode=turn_mode,
            commit_owner="provider",
            silence_duration_ms=silence,
            vad_threshold=threshold,
            prefix_padding_ms=prefix,
            semantic_eagerness=None,
        )
    if turn_mode == "semantic_vad":
        eagerness = config.openai_realtime_vad_eagerness
        return RealtimeAudioPolicy(
            configuration_id=f"semantic_vad:e{eagerness}",
            turn_mode=turn_mode,
            commit_owner="provider",
            silence_duration_ms=None,
            vad_threshold=None,
            prefix_padding_ms=None,
            semantic_eagerness=eagerness,
        )
    return RealtimeAudioPolicy(
        configuration_id="client_vad:v1",
        turn_mode="client_vad",
        commit_owner="client",
        silence_duration_ms=None,
        vad_threshold=None,
        prefix_padding_ms=None,
        semantic_eagerness=None,
    )


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

    # OpenAI Realtime (standard keys remain backend-only)
    openai_api_key: str = ""
    openai_realtime_base_url: str = "https://api.openai.com/v1"
    openai_realtime_websocket_url: str = "wss://api.openai.com/v1/realtime"
    openai_realtime_model: str = "gpt-realtime-2.1"
    openai_realtime_voice: str = "marin"
    openai_realtime_reasoning_effort: Literal["none", "low", "medium", "high"] = "low"
    openai_realtime_noise_reduction: Literal["near_field", "far_field"] = "near_field"
    openai_realtime_vad_eagerness: Literal["low", "medium", "high", "auto"] = "medium"
    jarvis_realtime_turn_mode: JarvisRealtimeTurnMode = "server_vad"
    jarvis_realtime_server_vad_silence_ms: int = 350
    jarvis_realtime_server_vad_threshold: float = 0.5
    jarvis_realtime_server_vad_prefix_ms: int = 300
    openai_realtime_transcription_model: str = "gpt-live-transcribe"
    openai_realtime_connect_timeout_seconds: float = 12.0
    openai_realtime_rotation_minutes: int = 55
    openai_realtime_transcript_margin_ms: int = 250
    jarvis_update_poll_seconds: float = 0.2
    jarvis_notification_grace_seconds: float = 8.0
    jarvis_notification_floor_stale_seconds: float = 30.0
    jarvis_component_stale_after_seconds: float = 5.0
    jarvis_watchdog_poll_seconds: float = 1.0

    # Local OpenAI-compatible model server (for example llama.cpp)
    local_llm_enabled: bool = True
    local_llm_base_url: str = "http://127.0.0.1:8080/v1"
    local_llm_api_key: str = "local"
    local_llm_alias: str = "local/gemma-4-12b"
    local_llm_model: str = "google/gemma-4-12B-it-qat-q4_0-gguf:Q4_0"
    local_llm_context_size: int = 16384
    local_llm_max_concurrency: int = 1

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
    task_workspaces_dir: str = "data/task-workspaces"
    worker_heartbeat_interval_seconds: float = 5.0
    worker_stale_after_seconds: float = 20.0
    worker_lease_ttl_seconds: float = 30.0
    worker_poll_interval_seconds: float = 0.5
    reminder_poll_interval_seconds: float = 1.0
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

    @field_validator("jarvis_realtime_server_vad_silence_ms")
    @classmethod
    def validate_jarvis_realtime_silence(cls, value: int) -> int:
        if not 100 <= value <= 2_000:
            raise ValueError("jarvis_realtime_server_vad_silence_ms must be between 100 and 2000")
        return value

    @field_validator("jarvis_realtime_server_vad_threshold")
    @classmethod
    def validate_jarvis_realtime_threshold(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("jarvis_realtime_server_vad_threshold must be between 0 and 1")
        return value

    @field_validator("jarvis_realtime_server_vad_prefix_ms")
    @classmethod
    def validate_jarvis_realtime_prefix(cls, value: int) -> int:
        if not 0 <= value <= 2_000:
            raise ValueError("jarvis_realtime_server_vad_prefix_ms must be between 0 and 2000")
        return value

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
    def task_workspaces_path(self) -> Path:
        configured_path = Path(self.task_workspaces_dir)
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
