"""Subscription-first selection at a safe execution boundary, never voice admission."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import socket

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert

from app.config import settings
from app.db.session import session_scope
from app.db.tables import RuntimeCredentialLease, TaskAttempt, WorkerInstance, TaskEvent
from app.services.windows_job import process_has_exited


@dataclass(frozen=True)
class AuthRoute:
    route: str
    reason: str
    profile: Path
    profile_key: str | None = None


def subscription_profile() -> Path:
    return (settings.backend_root / settings.codex_subscription_profile).resolve()


async def select_route(*, owner_kind: str, owner_id: str, pid: int, stamp: int | str,
                       api_profile: Path, contained: bool, unavailable_reason: str | None = None) -> AuthRoute:
    subscription = subscription_profile()
    desktop = (Path.home() / ".codex").resolve()
    if subscription == desktop or subscription.is_relative_to(desktop) or desktop.is_relative_to(subscription):
        raise ValueError("Jarvis subscription profile must be separate from the desktop profile")
    if subscription == api_profile.resolve():
        raise ValueError("Subscription and API profiles must be separate")
    reason = "subscription_disabled"
    if unavailable_reason:
        reason = unavailable_reason
    elif settings.codex_subscription_enabled:
        reason = "subscription_profile_unavailable"
        if contained and pid > 0 and stamp and (subscription / "auth.json").is_file():
            key = sha256(str(subscription).casefold().encode()).hexdigest()
            async with session_scope() as session:
                # Recover an abandoned credential owner only after exact exit
                # proof. No TTL, heartbeat, or PID-only inference is sufficient.
                from sqlalchemy import update
                await session.execute(update(RuntimeCredentialLease).where(RuntimeCredentialLease.profile_key == key)
                    .values(acquired_at=RuntimeCredentialLease.acquired_at))
                previous = await session.get(RuntimeCredentialLease, key)
                if (previous is not None and previous.host == socket.gethostname()
                        and process_has_exited(previous.pid, int(previous.creation_stamp)) is True):
                    await session.delete(previous)
                    await session.flush()
                await session.execute(insert(RuntimeCredentialLease).values(profile_key=key,
                    owner_kind=owner_kind, owner_id=owner_id, pid=pid, creation_stamp=str(stamp),
                    host=socket.gethostname()).on_conflict_do_nothing(index_elements=["profile_key"]))
                lease = await session.get(RuntimeCredentialLease, key)
                if (lease.host == socket.gethostname() and lease.owner_kind == owner_kind
                        and lease.owner_id == owner_id and lease.pid == pid and lease.creation_stamp == str(stamp)):
                    return AuthRoute("subscription", "eligible_subscription_owner", subscription, key)
                reason = "subscription_busy"
    if not settings.openai_api_key and not (api_profile / "auth.json").is_file():
        raise RuntimeError(f"No authorized API fallback ({reason})")
    return AuthRoute("api", reason, api_profile)


def provider_denial(error: dict | None) -> str | None:
    """Only the pinned structured protocol classifies a known provider denial."""
    if not isinstance(error, dict):
        return None
    code = error.get("codexErrorInfo") or error.get("codex_error_info")
    return {"unauthorized": "subscription_auth_denied", "usageLimitExceeded": "subscription_quota_exhausted",
        "rateLimitExceeded": "subscription_rate_limited"}.get(code) if isinstance(code, str) else None


def profile_revision() -> int | None:
    try:
        return (subscription_profile() / "auth.json").stat().st_mtime_ns
    except OSError:
        return None


async def task_subscription_denial(task_id: str) -> str | None:
    # A terminal denial changes the NEXT explicit retry/continuation boundary.
    # The failed turn is never replayed here. Account refresh changes the private
    # profile revision; no credentials or token hashes are stored in task history.
    async with session_scope() as session:
        event = (await session.execute(select(TaskEvent).where(TaskEvent.task_id == task_id,
            TaskEvent.event_type == "task.auth.unavailable").order_by(TaskEvent.sequence.desc()).limit(1))).scalar_one_or_none()
        if event and event.payload_json.get("profile_revision") == profile_revision():
            return event.payload_json.get("reason")
    return None


def api_fallback(context, *, reason: str) -> AuthRoute:
    profile = (settings.codex_profiles_path / context.task_id).resolve()
    if not settings.openai_api_key and not (profile / "auth.json").is_file():
        raise RuntimeError("Subscription unavailable and no authorized API fallback")
    return AuthRoute("api", reason, profile)


async def release_stopped_owner(*, owner_kind: str, owner_id: str, pid: int | None = None) -> bool:
    """Exact Windows process exit is required; stale timestamps cannot release."""
    async with session_scope() as session:
        leases = (await session.execute(select(RuntimeCredentialLease).where(
            RuntimeCredentialLease.owner_kind == owner_kind, RuntimeCredentialLease.owner_id == owner_id))).scalars().all()
        for lease in leases:
            if (lease.host != socket.gethostname() or (pid is not None and lease.pid != pid)
                    or process_has_exited(lease.pid, int(lease.creation_stamp)) is not True):
                return False
        for lease in leases:
            await session.delete(lease)
        return True


async def worker_route(context) -> AuthRoute:
    async with session_scope() as session:
        attempt = await session.get(TaskAttempt, context.attempt_id)
        worker = await session.get(WorkerInstance, attempt.worker_id) if attempt and attempt.worker_id else None
        metadata = (worker.metadata_json or {}) if worker else {}
    return await select_route(owner_kind="worker", owner_id=worker.id if worker else context.attempt_id,
        pid=int(metadata.get("pid") or 0), stamp=metadata.get("process_creation_stamp") or 0,
        contained=bool(metadata.get("supervised")),
        unavailable_reason=await task_subscription_denial(context.task_id),
        api_profile=(settings.codex_profiles_path / context.task_id).resolve())
