"""Device-scoped, fail-closed delivery of a run's required JPEG context."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select, update

from app.config import settings
from app.db.repositories.tasks import TaskRepository
from app.db.session import session_scope
from app.db.tables import (
    JarvisDevice, Task, TaskArtifact, TaskCaptureReport, TaskContextBlob, TaskContextRequest,
    TaskExecutionRun, WorkflowRun,
)
from app.services.task_service import task_service
from app.services.task_results import fail_document_result


MAX_JPEG_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 32_000_000
FAILURE_REASONS = {
    "callback_null", "android_screenshot_disabled", "platform_or_app_policy",
    "locked", "activation_replaced", "session_unavailable", "encoding_failed",
    "upload_failed",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret_key, salt="jarvis-task-context-upload-v1")


def _token(row: TaskContextRequest) -> str:
    return _signer().dumps({
        "request_id": row.id, "device_id": row.device_id,
        "activation_id": row.activation_id, "capture_id": row.capture_id,
        "generation": row.generation, "execution_run_id": row.execution_run_id,
    })


def _validate_token(token: str, row: TaskContextRequest) -> None:
    try:
        claims = _signer().loads(token, max_age=120)
    except (BadSignature, SignatureExpired) as exc:
        raise ValueError("Upload token is invalid or expired") from exc
    expected = {
        "request_id": row.id, "device_id": row.device_id,
        "activation_id": row.activation_id, "capture_id": row.capture_id,
        "generation": row.generation, "execution_run_id": row.execution_run_id,
    }
    if claims != expected:
        raise ValueError("Upload token does not match this context request")


def _validate_jpeg(content: bytes) -> tuple[int, int, str]:
    if not content or len(content) > MAX_JPEG_BYTES:
        raise ValueError("JPEG exceeds the five MiB upload limit")
    if not content.startswith(b"\xff\xd8\xff"):
        raise ValueError("Context content is not JPEG")
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format != "JPEG":
                raise ValueError("Context content is not JPEG")
            width, height = image.size
            if (width < 1 or height < 1 or width > 8192 or height > 8192
                    or width * height > MAX_PIXELS):
                raise ValueError("JPEG dimensions are outside the supported range")
            image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("JPEG content is malformed") from exc
    return width, height, sha256(content).hexdigest()


class TaskContextUploadService:
    async def pending(self, *, device_id: str) -> list[dict]:
        await self.expire_pending()
        async with session_scope() as session:
            device = await session.get(JarvisDevice, device_id)
            if device is None or device.revoked_at is not None or device.lock_state == "locked":
                return []
            rows = list((await session.execute(select(TaskContextRequest).where(
                TaskContextRequest.device_id == device_id,
                TaskContextRequest.state == "pending",
            ).order_by(TaskContextRequest.requested_at.asc()))).scalars().all())
            return [{
                "request_id": row.id, "task_id": row.task_id,
                "execution_run_id": row.execution_run_id,
                "activation_id": row.activation_id, "capture_id": row.capture_id,
                "capture_revision": row.capture_revision, "generation": row.generation,
                "capture_mode": row.capture_mode, "expires_at": row.expires_at,
                "upload_token": _token(row),
            } for row in rows]

    async def accept(
        self, *, request_id: str, device_id: str, token: str,
        content: bytes, content_sha256: str, capture_id: str,
        activation_id: str, generation: int, captured_at: datetime,
        capture_monotonic_ms: float,
    ) -> dict:
        width, height, digest = _validate_jpeg(content)
        if digest != content_sha256.lower():
            raise ValueError("JPEG checksum does not match the declared SHA-256")
        async with session_scope() as session:
            # Serialize uploads for the run before reading fulfillment state.
            await session.execute(update(TaskExecutionRun).where(TaskExecutionRun.id.in_(
                select(TaskContextRequest.execution_run_id).where(TaskContextRequest.id == request_id,
                    TaskContextRequest.device_id == device_id))).values(status=TaskExecutionRun.status))
            row = await session.get(TaskContextRequest, request_id)
            if row is None or row.device_id != device_id:
                raise ValueError("Context request is unavailable to this device")
            _validate_token(token, row)
            if (row.capture_id != capture_id or row.activation_id != activation_id
                    or row.generation != generation):
                raise ValueError("Upload identity does not match the context request")
            device = await session.get(JarvisDevice, device_id)
            if device is None or device.revoked_at is not None or device.lock_state == "locked":
                raise ValueError("Upload device is unavailable or locked")
            if row.state == "ready":
                blob = await session.get(TaskContextBlob, row.blob_id)
                if blob is None or blob.sha256 != digest:
                    raise ValueError("Fulfilled context request has different bytes")
                return {"state": "ready", "context_reference": f"context://{blob.id}", "sha256": digest}
            if row.state != "pending":
                raise ValueError("Context request is no longer pending")
            if _aware(row.expires_at) <= _now():
                raise ValueError("Context request has expired")
            capture = await session.get(TaskCaptureReport, row.capture_id)
            if (capture is None or capture.captured_at is None
                    or capture.status != "captured" or capture.revision < row.capture_revision
                    or _aware(capture.captured_at) != _aware(captured_at)
                    or capture_monotonic_ms < 0):
                raise ValueError("Capture timing is invalid")
            task = (await session.execute(select(Task).where(Task.id == row.task_id))).scalar_one()
            run = await session.get(TaskExecutionRun, row.execution_run_id)
            current = run is not None and task.current_run_id == run.id
            if (run is None or (not current and run.status != "pending") or
                    (current and (task.status != "waiting_for_input" or task.status_reason != "context_upload_pending"))):
                raise ValueError("Task is no longer waiting for this context")
            blob = (await session.execute(select(TaskContextBlob).where(
                TaskContextBlob.sha256 == digest,
            ))).scalar_one_or_none()
            if blob is None:
                folder = (settings.backend_root / settings.task_context_blobs_dir).resolve()
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f"{digest}.jpg"
                try:
                    with path.open("xb") as output:
                        output.write(content)
                except FileExistsError:
                    if sha256(path.read_bytes()).hexdigest() != digest:
                        raise ValueError("Stored context checksum conflict")
                blob = TaskContextBlob(
                    sha256=digest, storage_path=str(path), byte_length=len(content),
                    mime_type="image/jpeg", width=width, height=height,
                )
                session.add(blob)
                await session.flush()
            reference = f"context://{blob.id}"
            roles = [image["role"] for image in (run.configuration_json or {}).get("screen_context", {}).get("images", [])
                if image["capture_id"] == row.capture_id]
            artifact = TaskArtifact(
                task_id=row.task_id, execution_run_id=run.id,
                artifact_type="task_context_images", mime_type="application/json",
                title=" / ".join(roles) + " screen", uri=reference,
                content_json={"schema": "task-context-image.v2", "reference": reference, "roles": roles,
                              "capture_id": row.capture_id, "session_id": row.session_id,
                              "activation_id": row.activation_id, "generation": row.generation,
                              "sha256": digest, "width": width, "height": height,
                              "captured_at": _aware(captured_at).isoformat(),
                              "capture_monotonic_ms": capture_monotonic_ms},
            )
            session.add(artifact)
            await session.flush()
            row.state = "ready"
            row.blob_id = blob.id
            row.artifact_id = artifact.id
            row.fulfilled_at = _now()
            await session.flush()
            outstanding = await session.scalar(select(TaskContextRequest.id).where(
                TaskContextRequest.execution_run_id == run.id, TaskContextRequest.state != "ready").limit(1))
            complete = outstanding is None
            if current and complete:
                run.status = "queued"
            workflow_run = await session.get(WorkflowRun, row.workflow_run_id)
            if workflow_run is not None:
                workflow_run.context_reference = reference
                workflow_run.stage = "ready" if complete else "context_pending"
            if current and complete:
                task.status = "queued"
                task.status_reason = None
            await TaskRepository(session).append_event(
                task_id=task.id, attempt_id=task.current_attempt_id if current else None,
                execution_run_id=run.id, event_type="task.context.ready",
                payload_json={"request_id": row.id, "context_reference": reference},
                idempotency_key=f"task.context.ready:{row.id}",
            )
            if complete:
                await task_service._assign_worker_eligibility(
                    session, task, repo=TaskRepository(session),
                    idempotency_key=f"task.context.waiting-for-worker:{row.id}",
                )
            return {"state": "ready", "context_reference": reference, "sha256": digest}

    async def fail(self, *, request_id: str, device_id: str, reason: str) -> dict:
        if reason not in FAILURE_REASONS:
            raise ValueError("Context failure reason is invalid")
        async with session_scope() as session:
            row = await session.get(TaskContextRequest, request_id)
            if row is None or row.device_id != device_id:
                raise ValueError("Context request is unavailable to this device")
            if row.state == "ready":
                return {"state": "ready", "failure_reason": None}
            if row.state in {"unavailable", "expired"}:
                return {"state": row.state, "failure_reason": row.failure_reason}
            await self._fail_in_session(session, row, reason)
            return {"state": row.state, "failure_reason": row.failure_reason}

    async def expire_pending(self) -> int:
        async with session_scope() as session:
            rows = list((await session.execute(select(TaskContextRequest).where(
                TaskContextRequest.state == "pending", TaskContextRequest.expires_at <= _now(),
            ))).scalars().all())
            for row in rows:
                await self._fail_in_session(session, row, "expired")
            return len(rows)

    async def _fail_in_session(self, session, row: TaskContextRequest, reason: str) -> None:
        now = _now()
        row.state = "expired" if reason == "expired" else "unavailable"
        row.failure_reason = reason
        row.failed_at = now
        task = (await session.execute(select(Task).where(Task.id == row.task_id))).scalar_one()
        run = await session.get(TaskExecutionRun, row.execution_run_id)
        if run is not None and run.status in {"waiting_for_input", "pending"}:
            run.status = "failed"
            run.terminal_summary = f"Required screen context failed: {reason}"
            run.finished_at = now
        failed_current = task.current_run_id == row.execution_run_id and task.status == "waiting_for_input"
        if failed_current:
            task.status = "failed"
            task.status_reason = reason
            task.terminal_summary = f"Required screen context failed: {reason}"
            task.finished_at = now
        await fail_document_result(
            session, task_id=row.task_id, run_id=row.execution_run_id,
            reason=f"Required screen context failed: {reason}",
        )
        await TaskRepository(session).append_event(
            task_id=row.task_id, attempt_id=task.current_attempt_id if task.current_run_id == row.execution_run_id else None,
            execution_run_id=row.execution_run_id, event_type="task.context.failed",
            payload_json={"request_id": row.id, "reason": reason},
            idempotency_key=f"task.context.failed:{row.id}",
        )
        if failed_current:
            await TaskRepository(session).append_event(
                task_id=row.task_id, attempt_id=task.current_attempt_id,
                execution_run_id=row.execution_run_id, event_type="task.failed",
                payload_json={"status": "failed", "summary": "Required screen context could not be delivered."},
                idempotency_key=f"task.failed:context:{row.id}",
            )


task_context_upload_service = TaskContextUploadService()
