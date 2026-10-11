"""Authenticated current-only results, safe replay and registered viewer actions."""
from __future__ import annotations

import json
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from hashlib import sha256
import time
from uuid import UUID, uuid4
from typing import Literal

from anyio import CancelScope

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from pydantic import Field, StrictInt, BaseModel, ConfigDict
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from app.db.session import session_scope, read_session_scope
from app.db.tables import ResultPage, ResultPageEvent, ResultPageRevision, TaskArtifact
from app.services.app_catalog import SavedValue, write_data
from app.services.jarvis_devices import require_jarvis_auth, require_unlocked_device
from app.services.task_results import append_result_event, result_snapshot
from app.services.task_result_types import result_type
from app.services import result_telemetry
from app.services.commit_signals import result_event_committed

RESULT_RECOVERY_SCAN_SECONDS = .5

router = APIRouter(prefix="/api/results", tags=["results"], dependencies=[Depends(require_jarvis_auth)])
PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow",
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
_write_windows: OrderedDict[str, deque] = OrderedDict()


async def protect_state_write(request: Request):
    await require_unlocked_device(request)
    """Bound abuse without retaining cookies/device credentials in memory/logs."""
    identity = request.headers.get("authorization") or request.cookies.get("cockpit_session") or (request.client.host if request.client else "local")
    key = sha256(identity.encode()).hexdigest()
    now = time.monotonic()
    times = _write_windows.setdefault(key, deque())
    _write_windows.move_to_end(key)
    while times and times[0] <= now - 60:
        times.popleft()
    if len(times) >= 120:
        raise HTTPException(429, "Too many app state writes", headers={"Retry-After": "60"})
    times.append(now)
    while len(_write_windows) > 4096:
        _write_windows.popitem(last=False)
    if len(await request.body()) > 256 * 1024:
        raise HTTPException(413, "App state request too large")


async def find_result(session, public_id):
    page = (await session.execute(select(ResultPage).where(ResultPage.public_id == public_id))).scalar_one_or_none()
    if page is None:
        raise HTTPException(404, "Result not found")
    return page


@router.get("/{public_id}")
async def get_result(public_id: str) -> dict:
    started = time.perf_counter()
    async with session_scope() as session:
        page = await find_result(session, public_id)
        snapshot = await result_snapshot(session, page)
    await result_telemetry.observe(page.id, kind="first_api_snapshot", key=f"api-snapshot:{page.id}",
        run_id=page.active_execution_run_id, revision_number=page.revision_number,
        duration_ms=(time.perf_counter() - started) * 1000, values={"clock": "server_monotonic"})
    return snapshot


class ClientObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view_id: UUID
    sequence: StrictInt = Field(ge=0, le=1000)
    kind: Literal["first_snapshot", "first_ready", "sse_connect", "sse_reconnect", "sse_fallback"]
    duration_ms: float = Field(ge=0, le=3600000, allow_inf_nan=False, strict=True)
    revision_number: StrictInt = Field(ge=0)


@router.post("/{public_id}/observations", dependencies=[Depends(protect_state_write)], status_code=202)
async def client_observation(public_id: str, body: ClientObservation):
    observations = []
    async with session_scope() as session:
        page = await find_result(session, public_id)
        if body.revision_number > page.revision_number:
            raise HTTPException(409, "Observation refers to a future revision")
        # A view's initial milestones are deduplicated even if retried with a
        # different sequence; connection counts use bounded occurrence IDs.
        occurrence = 0 if body.kind in {"first_snapshot", "first_ready"} else body.sequence
        observations.append(dict(kind=f"client_{body.kind}",
            key=f"client:{page.id}:{body.view_id}:{body.kind}:{occurrence}", duration_ms=body.duration_ms,
            revision_number=body.revision_number, values={"source": "client_reported", "clock": "browser_monotonic", "measurement": "view_mount_to_milestone"}))
        if body.kind == "first_snapshot":
            observations.append(dict(kind="allocation_to_view_receipt", key=f"view-receipt:{page.id}",
                duration_ms=result_telemetry.age_ms(page.created_at),
                values={"source": "server_receipt_proxy", "clock": "server_wall", "includes_network": True}))
        elif body.kind == "first_ready" and body.revision_number > 0:
            revision = (await session.execute(select(ResultPageRevision).where(
                ResultPageRevision.result_page_id == page.id,
                ResultPageRevision.revision_number == body.revision_number))).scalar_one_or_none()
            if revision is not None:
                observations.append(dict(kind="publication_to_ready_receipt",
                    key=f"ready-receipt:{revision.id}", duration_ms=result_telemetry.age_ms(revision.created_at),
                    run_id=revision.execution_run_id, attempt_id=revision.attempt_id, revision_number=revision.revision_number,
                    values={"source": "server_receipt_proxy", "clock": "server_wall", "includes_network": True,
                        "measurement": "revision_created_to_ready_receipt"}))
    # Receipts are best-effort metadata, not state writes or page authority.
    # Reuse bounded independent writes and post-commit structured logging.
    for observation in observations:
        observation.setdefault("run_id", page.active_execution_run_id)
        observation.setdefault("revision_number", page.revision_number)
        await result_telemetry.observe(page.id, **observation)
    return {"accepted": True}


@router.get("/{public_id}/observability")
async def result_observability(public_id: str):
    async with session_scope() as session:
        return await result_telemetry.snapshot(session, await find_result(session, public_id))


@router.get("/{public_id}/attachments/{artifact_id}")
async def get_attachment(public_id: str, artifact_id: str):
    from app.services.task_results import validate_attachments, ATTACHMENT_MIMES
    async with session_scope() as session:
        page = await find_result(session, public_id)
        revision = await session.get(ResultPageRevision, page.current_revision_id) if page.current_revision_id else None
        reference = next((item for item in (revision.content_json.get("attachments", []) if revision else []) if item["artifact_id"] == artifact_id), None)
        if reference is None:
            raise HTTPException(404, "Attachment not found in current result")
        try:
            await validate_attachments(session, page.task_id, {"attachments": [reference]})
        except ValueError:
            raise HTTPException(404, "Attachment is unavailable") from None
        artifact = await session.get(TaskArtifact, artifact_id)
        content = artifact.content_text.encode("utf-8") if artifact.content_text is not None else json.dumps(artifact.content_json, ensure_ascii=False).encode("utf-8")
        # Never follow worker-supplied URI/file paths or serve active HTML inline.
        return Response(content, media_type=artifact.mime_type, headers={**PRIVATE_HEADERS,
            "Content-Disposition": f'attachment; filename="attachment{ATTACHMENT_MIMES[artifact.mime_type]}"'})


class SavedAction(SavedValue):
    expected_revision: StrictInt = Field(ge=0)


@router.post("/{public_id}/actions/{action_id}", dependencies=[Depends(protect_state_write)])
async def act_result(public_id: str, action_id: str, body: SavedAction):
    if action_id != "set_saved":
        raise HTTPException(404, "Action not registered")
    async with session_scope() as session:
        # A write lock before any reads avoids SQLite upgrade/deadlock races.
        from sqlalchemy import update
        from app.db.tables import CockpitApp
        await session.execute(update(CockpitApp).where(CockpitApp.id == select(ResultPage.cockpit_app_id)
            .where(ResultPage.public_id == public_id).scalar_subquery()).values(storage_bytes=CockpitApp.storage_bytes))
        page = await find_result(session, public_id)
        if action_id not in result_type(page.result_kind).actions:
            raise HTTPException(404, "Action not registered for this result")
        result = await write_data(session, slug=page.app_slug, namespace="saved", key=public_id,
            value={"saved": body.saved}, expected_revision=body.expected_revision)
        await append_result_event(session, page, "action_state", event_key=f"saved:{result['revision']}")
        return result


def parse_cursor(value: str | None, latest: int) -> tuple[int, bool]:
    if value is None:
        return latest, False
    try:
        cursor = int(value)
        if str(cursor) != value or cursor < 0 or cursor > latest:
            raise ValueError
        return cursor, False
    except ValueError:
        return latest, True


@asynccontextmanager
async def stream_read_session():
    # An EventSource disconnect uses an AnyIO cancellation scope. Finish the
    # short read/close before propagating it, or SQLite connection cleanup can
    # itself be cancelled. Never shield the live stream, sleeps or publications.
    with CancelScope(shield=True):
        async with read_session_scope() as session:
            yield session


async def result_events(public_id: str, request: Request, cursor: str | None):
    started = time.perf_counter()
    connection_id = str(uuid4())
    async with stream_read_session() as session:
        page = await find_result(session, public_id)
        snapshot = await result_snapshot(session, page)
        after, reset = parse_cursor(cursor, page.state_revision)
        oldest = (await session.execute(select(ResultPageEvent.sequence).where(ResultPageEvent.result_page_id == page.id)
            .order_by(ResultPageEvent.sequence).limit(1))).scalar_one_or_none()
        if oldest is not None and after < oldest - 1:
            after, reset = page.state_revision, True
        page_id = page.id
        latest = page.state_revision
        from sqlalchemy import func
        replay_count = (await session.execute(select(func.count()).select_from(ResultPageEvent).where(
            ResultPageEvent.result_page_id == page_id, ResultPageEvent.sequence > after,
            ResultPageEvent.sequence <= latest))).scalar_one()
    await result_telemetry.observe(page_id, kind="sse_connection", key=f"sse:{connection_id}",
        run_id=page.active_execution_run_id, revision_number=page.revision_number,
        duration_ms=(time.perf_counter() - started) * 1000, values={"cursor_provided": cursor is not None,
            "cursor_reset": reset, "available_replay_events": replay_count, "clock": "server_monotonic"})
    yield {"event": "snapshot", "id": str(after), "data": json.dumps(jsonable_encoder({**snapshot, "cursor_reset": reset}))}
    while not await request.is_disconnected():
        observed = result_event_committed.snapshot()
        async with stream_read_session() as session:
            events = list((await session.execute(select(ResultPageEvent).where(ResultPageEvent.result_page_id == page_id,
                ResultPageEvent.sequence > after).order_by(ResultPageEvent.sequence).limit(100))).scalars())
        replayed = 0
        for event in events:
            after = event.sequence
            yield {"event": "result", "id": str(after), "data": json.dumps({"type": event.event_type, **event.payload_json})}
            if after <= latest:
                replayed += 1
        if replayed:
            await result_telemetry.observe(page_id, kind="sse_replay", key=f"replay:{connection_id}:{after}",
                run_id=page.active_execution_run_id, revision_number=page.revision_number,
                values={"events": replayed, "source": "server_emission", "client_receipt_verified": False})
        if len(events) < 100:
            await result_event_committed.wait(observed, timeout=RESULT_RECOVERY_SCAN_SECONDS)


@router.get("/{public_id}/events")
async def stream_result(public_id: str, request: Request, cursor: str | None = Query(default=None, max_length=32)):
    async with session_scope() as session:
        await find_result(session, public_id)
    return EventSourceResponse(result_events(public_id, request, cursor if cursor is not None else request.headers.get("last-event-id")),
        ping=15, headers={**PRIVATE_HEADERS, "X-Accel-Buffering": "no"})
