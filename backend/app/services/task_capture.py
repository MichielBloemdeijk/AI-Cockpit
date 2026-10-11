"""Authenticated, revisioned Android screenshot readiness before task admission."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic
from hashlib import sha256

from sqlalchemy import select, update

from app.db.session import session_scope
from app.db.tables import JarvisDevice, LiveSession, TaskCaptureReport, JarvisLiveEvent
from app.services.result_telemetry import observe_preflight
from app.services.admission_timing import timed
from app.services.commit_signals import capture_state_committed


CAPTURE_LIFETIME = timedelta(seconds=90)
PREFLIGHT_WAIT_SECONDS = 2.0
ACTIVE_LIVE_STATES = {"connecting", "ready"}
CAPTURE_STATUSES = {"pending", "captured", "unavailable", "expired"}
CAPTURE_REASONS = {
    "callback_null", "callback_timeout", "android_screenshot_disabled", "platform_or_app_policy",
    "locked", "activation_replaced", "session_unavailable", "encoding_failed",
}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class CaptureBinding:
    capture_id: str
    capture_revision: int
    device_id: str
    session_id: str
    generation: int
    activation_id: str
    capture_mode: str
    captured_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CaptureCollection:
    """Frozen roles and provenance; requests continue to be per capture."""
    captures: tuple[tuple[str, CaptureBinding], ...]
    missing_roles: tuple[str, ...] = ()

    def manifest(self) -> dict:
        return {"schema": "task-screen-context.v2", "missing_roles": list(self.missing_roles),
            "images": [{"role": role, "capture_id": item.capture_id,
                "captured_at": item.captured_at.isoformat(), "expires_at": item.expires_at.isoformat(),
                "session_id": item.session_id, "generation": item.generation,
                "activation_id": item.activation_id, "capture_mode": item.capture_mode,
                "device_id": item.device_id, "capture_revision": item.capture_revision}
                for role, item in self.captures]}


class TaskCaptureService:
    async def request_browser_activation(self, *, session_id: str, generation: int) -> TaskCaptureReport:
        """Ask the authenticated phone to capture without creating another voice call."""
        now = _now()
        capture_id = sha256(f"{session_id}:{generation}:browser-activation".encode()).hexdigest()
        async with session_scope() as session:
            live = await session.get(LiveSession, session_id)
            device = await session.get(JarvisDevice, live.controller_id) if live and live.controller_id else None
            if (live is None or live.generation != generation or not live.activation_id
                    or live.lifecycle_state not in ACTIVE_LIVE_STATES or device is None or device.revoked_at is not None):
                raise ValueError("Phone capture session is unavailable")
            existing = await session.get(TaskCaptureReport, capture_id)
            if existing:
                return existing
            row = TaskCaptureReport(capture_id=capture_id, device_id=device.id, session_id=session_id,
                generation=generation, activation_id=live.activation_id, capture_mode="activation",
                status="pending", revision=1, expires_at=now + CAPTURE_LIFETIME,
                created_at=now, updated_at=now)
            session.add(row)
            session.add(JarvisLiveEvent(event_type="capture.activation_requested", source_domain="live",
                source_id=session_id, target_device_id=device.id,
                idempotency_key=f"capture.activation:{capture_id}", payload_json={
                    "capture_id": capture_id, "activation_id": live.activation_id,
                    "session_id": session_id, "generation": generation,
                    "capture_transport": "phone_browser", "capture_mode": "activation",
                    "expires_at": row.expires_at.isoformat()}))
            await session.flush()
            return row

    async def request_fresh(self, *, session_id: str, generation: int, request_identity: str) -> TaskCaptureReport:
        now = _now()
        capture_id = sha256(f"{session_id}:{generation}:{request_identity}".encode()).hexdigest()
        async with session_scope() as session:
            await session.execute(update(LiveSession).where(LiveSession.id == session_id).values(
                generation=LiveSession.generation))
            live = await session.get(LiveSession, session_id)
            device = await session.get(JarvisDevice, live.controller_id) if live and live.controller_id else None
            if (live is None or live.generation != generation or not live.activation_id
                    or live.lifecycle_state not in ACTIVE_LIVE_STATES or device is None
                    or device.revoked_at is not None or device.lock_state == "locked"):
                raise ValueError("Fresh capture session is unavailable")
            existing = await session.get(TaskCaptureReport, capture_id)
            if existing:
                return existing
            row = TaskCaptureReport(capture_id=capture_id, device_id=device.id, session_id=session_id,
                generation=generation, activation_id=live.activation_id, capture_mode="fresh",
                status="pending", revision=1, expires_at=now + CAPTURE_LIFETIME,
                created_at=now, updated_at=now)
            session.add(row)
            browser_capture = (await session.execute(select(JarvisLiveEvent.id).where(
                JarvisLiveEvent.source_id == session_id,
                JarvisLiveEvent.event_type == "capture.activation_requested").limit(1))).scalar_one_or_none() is not None
            session.add(JarvisLiveEvent(event_type="capture.fresh_requested", source_domain="live",
                source_id=session_id, target_device_id=device.id,
                idempotency_key=f"capture.fresh:{capture_id}", payload_json={
                    "capture_id": capture_id, "activation_id": live.activation_id,
                    "session_id": session_id, "generation": generation,
                    "delegated_turn": request_identity,
                    "capture_mode": "fresh",
                    "capture_transport": "phone_browser" if browser_capture else "native_voice",
                    "expires_at": row.expires_at.isoformat()}))
            await session.flush()
            return row

    @timed("capture_preflight")
    async def preflight_collection(self, *, request_identity: str, **options) -> CaptureCollection:
        mode = options.pop("capture_mode", "activation")
        capture_id = options.pop("capture_id", None)
        fresh = await self.request_fresh(session_id=options["session_id"],
            generation=options["generation"], request_identity=request_identity)
        if mode == "fresh" and capture_id and capture_id != fresh.capture_id:
            raise ValueError("Current capture does not belong to this delegated turn")
        async def observe(role, capture_mode, selected_id):
            try:
                required = (mode == "fresh" and role == "latest") or (mode == "activation" and role == "invocation")
                role_options = options if required else {**options, "wait_seconds": 0.}
                return role, await self.preflight(**role_options, capture_mode=capture_mode, capture_id=selected_id)
            except ValueError:
                if (mode == "fresh" and role == "latest") or (mode == "activation" and role == "invocation"):
                    raise
                return role, None
        roles = {"invocation": ("activation", capture_id if mode == "activation" else None),
            "latest": ("fresh", capture_id if mode == "fresh" and capture_id else fresh.capture_id)}
        required_role = "latest" if mode == "fresh" else "invocation"
        optional_role = "invocation" if mode == "fresh" else "latest"
        required = await observe(required_role, *roles[required_role])
        # Inspect optional context only after required readiness, when freezing
        # the collection. It may have arrived while the required role waited.
        optional = await observe(optional_role, *roles[optional_role])
        observed = sorted((required, optional), key=lambda item: item[0])
        return CaptureCollection(tuple((role, image) for role, image in observed if image is not None),
            tuple(role for role, image in observed if image is None))

    async def report(
        self, *, device_id: str, capture_id: str, activation_id: str,
        capture_mode: str, status: str, revision: int, expires_at: datetime,
        captured_at: datetime | None = None, reason: str | None = None,
        callback_monotonic_ms: float | None = None,
        session_id: str | None = None, generation: int | None = None,
    ) -> TaskCaptureReport:
        if (not capture_id or len(capture_id) > 64 or not activation_id or len(activation_id) > 64
                or capture_mode not in {"activation", "fresh"} or status not in CAPTURE_STATUSES
                or revision < 1 or (session_id is None) != (generation is None)):
            raise ValueError("Capture report identity or state is invalid")
        now = _now()
        expiry = _aware(expires_at)
        captured = _aware(captured_at) if captured_at is not None else None
        if (expiry <= now or expiry > now + CAPTURE_LIFETIME + timedelta(seconds=5)) and status in {"pending", "captured"}:
            raise ValueError("Capture report expiry is invalid")
        if status == "captured" and (captured is None or captured > now + timedelta(seconds=5)
                                     or expiry > captured + CAPTURE_LIFETIME + timedelta(seconds=5)):
            raise ValueError("Captured status requires a bounded capture time")
        if status in {"unavailable", "expired"} and reason not in CAPTURE_REASONS:
            raise ValueError("Unavailable capture needs a classified reason")
        if status in {"pending", "captured"} and reason is not None:
            raise ValueError("Ready capture cannot have a failure reason")
        async with session_scope() as session:
            device = await session.get(JarvisDevice, device_id)
            if device is None or device.revoked_at is not None:
                raise ValueError("Capture device is unavailable")
            if device.lock_state == "locked" and status in {"pending", "captured"}:
                raise ValueError("A locked device cannot report usable pixels")
            if session_id is not None:
                live = await session.get(LiveSession, session_id)
                if (live is None or live.controller_id != device_id or live.generation != generation
                        or live.activation_id != activation_id or live.lifecycle_state not in ACTIVE_LIVE_STATES):
                    raise ValueError("Capture does not match the active Live session")
            await session.execute(update(JarvisDevice).where(JarvisDevice.id == device_id).values(last_seen_at=now))
            row = await session.get(TaskCaptureReport, capture_id)
            if row is None:
                row = TaskCaptureReport(
                    capture_id=capture_id, device_id=device_id,
                    session_id=session_id, generation=generation,
                    activation_id=activation_id, capture_mode=capture_mode,
                    status=status, revision=revision, reason=reason,
                    captured_at=captured, expires_at=expiry,
                    callback_monotonic_ms=callback_monotonic_ms,
                    created_at=now, updated_at=now,
                )
                session.add(row)
            else:
                if (row.device_id != device_id or row.activation_id != activation_id
                        or row.capture_mode != capture_mode or
                        (row.session_id is not None and row.session_id != session_id and session_id is not None)):
                    raise ValueError("Capture report identity is immutable")
                if revision < row.revision:
                    raise ValueError("Stale capture report revision")
                if revision == row.revision:
                    if (row.status != status or row.reason != reason
                            or _aware(row.expires_at) != expiry
                            or (None if row.captured_at is None else _aware(row.captured_at)) != captured):
                        raise ValueError("Capture report revision conflicts with its prior value")
                    return row
                # A callback can finish before Live connects. Permit only the
                # later session binding of the identical terminal observation;
                # never revive pixels or extend its lifetime.
                terminal_binding = (
                    row.status in {"expired", "unavailable"} and status == row.status
                    and row.session_id is None and session_id is not None
                    and row.reason == reason and _aware(row.expires_at) == expiry
                    and (None if row.captured_at is None else _aware(row.captured_at)) == captured
                    and row.callback_monotonic_ms == callback_monotonic_ms
                )
                if (row.status in {"expired", "unavailable"} and not terminal_binding) or (row.status == "captured" and status == "pending"):
                    raise ValueError("Capture terminal state cannot be resurrected")
                if row.status == "captured" and (
                    _aware(row.expires_at) != expiry
                    or (None if row.captured_at is None else _aware(row.captured_at)) != captured
                ):
                    raise ValueError("Captured image identity and lifetime cannot change")
                row.status = status
                row.revision = revision
                row.reason = reason
                row.captured_at = captured
                row.expires_at = expiry
                row.callback_monotonic_ms = callback_monotonic_ms
                row.updated_at = now
                if session_id is not None and row.session_id is None:
                    row.session_id = session_id
                    row.generation = generation
            await session.flush()
            return row

    async def latest(
        self, *, device_id: str, activation_id: str, capture_mode: str = "activation",
    ) -> TaskCaptureReport | None:
        async with session_scope() as session:
            return (await session.execute(select(TaskCaptureReport).where(
                TaskCaptureReport.device_id == device_id,
                TaskCaptureReport.activation_id == activation_id,
                TaskCaptureReport.capture_mode == capture_mode,
            ).order_by(TaskCaptureReport.created_at.desc(), TaskCaptureReport.capture_id.desc()).limit(1))).scalar_one_or_none()

    @observe_preflight
    async def preflight(
        self, *, device_id: str, session_id: str, generation: int,
        activation_id: str, capture_mode: str = "activation", capture_id: str | None = None,
        wait_seconds: float = PREFLIGHT_WAIT_SECONDS,
    ) -> CaptureBinding:
        if capture_mode not in {"activation", "fresh"} or wait_seconds < 0 or wait_seconds > PREFLIGHT_WAIT_SECONDS:
            raise ValueError("Capture preflight options are invalid")
        deadline = monotonic() + wait_seconds
        selected_id = capture_id
        while True:
            observed = capture_state_committed.snapshot()
            async with session_scope() as session:
                live = await session.get(LiveSession, session_id)
                device = await session.get(JarvisDevice, device_id)
                if (live is None or device is None or device.revoked_at is not None
                        or device.lock_state == "locked" or live.controller_id != device_id
                        or live.generation != generation or live.activation_id != activation_id
                        or live.lifecycle_state not in ACTIVE_LIVE_STATES):
                    raise ValueError("Capture session or device is unavailable")
                query = select(TaskCaptureReport).where(
                    TaskCaptureReport.device_id == device_id,
                    TaskCaptureReport.activation_id == activation_id,
                    TaskCaptureReport.capture_mode == capture_mode,
                )
                if selected_id is not None:
                    query = query.where(TaskCaptureReport.capture_id == selected_id)
                row = (await session.execute(query.order_by(
                    TaskCaptureReport.created_at.desc(), TaskCaptureReport.capture_id.desc(),
                ).limit(1))).scalar_one_or_none()
                if row is not None:
                    selected_id = row.capture_id
                    if row.session_id not in {None, session_id} or row.generation not in {None, generation}:
                        raise ValueError("Capture belongs to another Live session")
                    if row.status in {"unavailable", "expired"}:
                        raise ValueError(f"Capture is {row.status}: {row.reason}")
                    if _aware(row.expires_at) <= _now():
                        raise ValueError("Capture expired before admission")
                    if row.status == "captured" and row.captured_at is not None:
                        return CaptureBinding(
                            capture_id=row.capture_id, capture_revision=row.revision,
                            device_id=device_id, session_id=session_id, generation=generation,
                            activation_id=activation_id, capture_mode=capture_mode,
                            captured_at=_aware(row.captured_at), expires_at=_aware(row.expires_at),
                        )
            if monotonic() >= deadline:
                raise ValueError("Capture was not ready before the pre-admission deadline")
            # Ready/status/lock/session commits wake this immediately. The
            # rescan only covers changes from another process or missed hints.
            await capture_state_committed.wait(observed, timeout=min(.05, max(0., deadline - monotonic())))


task_capture_service = TaskCaptureService()
