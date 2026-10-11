"""Transaction-aware maintained results and safe, durable presentation events."""
from __future__ import annotations

from uuid import uuid4
from time import perf_counter

from sqlalchemy import select, update

from app.db.repositories.conversations import utc_now
from app.db.tables import ResultPage, ResultPageRevision, ResultPageEvent, TaskArtifact, Task, TaskAttempt, TaskExecutionRun
from app.services.app_catalog import collection_app, read_data
from app.services.task_result_types import result_type


async def allocate_result(session, *, task_id: str, run_id: str, schema: str = "document.v1") -> ResultPage:
    started = perf_counter()
    registered = result_type(schema)
    if registered.app_slug is None:
        raise ValueError("Result type has no maintained page")
    existing = (await session.execute(select(ResultPage).where(
        ResultPage.task_id == task_id,
        ResultPage.initial_execution_run_id == run_id,
    ))).scalar_one_or_none()
    if existing is not None:
        if existing.result_kind != schema:
            raise ValueError("Allocation identity already belongs to another result kind")
        return existing
    page = ResultPage(
        id=str(uuid4()), public_id=str(uuid4()), task_id=task_id,
        initial_execution_run_id=run_id, active_execution_run_id=run_id,
        app_slug=registered.app_slug, result_kind=schema, status="building",
        revision_number=0,
        cockpit_app_id=(await collection_app(session, registered.app_slug)).id,
    )
    session.add(page)
    await session.flush()
    await append_result_event(session, page, "allocated", event_key=f"allocated:{run_id}")
    from app.services.result_telemetry import allocated
    allocated(page, started)
    return page


async def publish_result(
    session, *, task_id: str, run_id: str, attempt_id: str | None,
    artifact: TaskArtifact, content: dict, schema: str = "document.v1",
    publication_key: str | None = None,
) -> ResultPage:
    content = result_type(schema).validate(content)
    await validate_attachments(session, task_id, content)
    page = (await session.execute(select(ResultPage).where(
        ResultPage.task_id == task_id,
        ResultPage.active_execution_run_id == run_id,
        ResultPage.result_kind == schema,
    ))).scalar_one_or_none()
    if page is None or page.status not in {"building", "updating"}:
        raise ValueError("Typed result was not allocated for this run")
    revision = ResultPageRevision(
        id=str(uuid4()), result_page_id=page.id,
        revision_number=page.revision_number + 1,
        execution_run_id=run_id, attempt_id=attempt_id,
        artifact_id=artifact.id, content_json=content,
        schema_id=schema, base_revision_id=page.current_revision_id,
        base_revision_number=page.revision_number,
        source_task_event_id=artifact.source_task_event_id,
        publication_key=f"{run_id}:{publication_key}" if publication_key else f"artifact:{artifact.id}",
        provenance_json={"task_id": task_id, "run_id": run_id, "artifact_id": artifact.id},
    )
    session.add(revision)
    await session.flush()
    page.current_revision_id = revision.id
    page.revision_number = revision.revision_number
    page.title = content["title"]
    page.summary = content.get("summary")
    page.status = "ready"
    page.warning = None
    page.first_published_at = page.first_published_at or utc_now()
    page.last_published_at = utc_now()
    await session.flush()
    await append_result_event(session, page, "published", event_key=f"published:{revision.id}",
        task_event_id=artifact.source_task_event_id, revision_id=revision.id)
    return page


# Existing callers keep their established document-only defaults.
allocate_document_result = allocate_result
publish_document_result = publish_result


async def fail_document_result(session, *, task_id: str, run_id: str, reason: str, cancelled: bool = False) -> None:
    page = (await session.execute(select(ResultPage).where(
        ResultPage.task_id == task_id,
        ResultPage.active_execution_run_id == run_id,
    ))).scalar_one_or_none()
    if page is None or page.status not in {"building", "updating"}:
        return
    if page.current_revision_id is None:
        page.status = "cancelled" if cancelled else "failed"
    else:
        page.status = "ready"
        page.warning = reason[:255]
    await append_result_event(session, page, "cancelled" if cancelled else "failed",
        event_key=f"outcome:{run_id}:{'cancelled' if cancelled else 'failed'}")


LABELS = {
    "building": "Preparing result", "updating": "Updating result", "ready": "Ready",
    "failed": "Task could not finish", "cancelled": "Task cancelled",
    "queued": "Waiting to start", "waiting_for_worker": "Waiting for a worker",
    "pending": "Waiting for earlier task work",
    "running": "Working", "waiting_for_input": "Waiting for input",
    "waiting_for_upload": "Waiting for screen upload", "waiting_until": "Scheduled",
    "paused": "Paused", "interrupted": "Execution interrupted",
}

ATTACHMENT_MIMES = {"text/plain": ".txt", "text/markdown": ".md", "application/json": ".json"}
ATTACHMENT_TYPES = {"result", "workflow_result", "document_attachment"}


async def validate_attachments(session, task_id, content):
    for reference in content.get("attachments", []):
        artifact = await session.get(TaskArtifact, reference["artifact_id"])
        if (artifact is None or artifact.task_id != task_id or artifact.artifact_type not in ATTACHMENT_TYPES
                or artifact.mime_type not in ATTACHMENT_MIMES
                or (artifact.content_text is None and artifact.content_json is None)):
            raise ValueError("Attachment must be an owned, retained text/JSON artifact; private context and filesystem URIs are not attachments")


async def display_state(session, page):
    task = (await session.execute(select(Task).where(Task.id == page.task_id))).scalar_one()
    run = await session.get(TaskExecutionRun, page.active_execution_run_id) if page.active_execution_run_id else None
    status = run.status if run else page.status
    reason = None
    if page.status in {"building", "updating"}:
        if status == "waiting_for_input" and task.current_run_id == page.active_execution_run_id and task.status_reason == "context_upload_pending":
            state, reason = "waiting_for_upload", "context_upload_pending"
        else:
            state = status if status in LABELS else page.status
    else:
        state = page.status
    return state, status, reason


def safe_warning(page):
    if not page.warning:
        return None
    # Raw worker/provider text stays in internal evidence, not presentation APIs.
    return "The latest update did not publish a new result. Your previous result is unchanged." if page.current_revision_id else "No result was published. Open the task for details."


async def append_result_event(session, page, event_type, *, event_key, task_event_id=None, revision_id=None):
    prior = (await session.execute(select(ResultPageEvent).where(
        ResultPageEvent.result_page_id == page.id, ResultPageEvent.event_key == event_key))).scalar_one_or_none()
    if prior is not None:
        return prior
    await session.flush()
    sequence = (await session.execute(update(ResultPage).where(ResultPage.id == page.id)
        .values(state_revision=ResultPage.state_revision + 1, updated_at=utc_now())
        .returning(ResultPage.state_revision))).scalar_one()
    state, _, _ = await display_state(session, page)
    event = ResultPageEvent(result_page_id=page.id, sequence=sequence, event_key=event_key,
        event_type=event_type, source_task_event_id=task_event_id, revision_id=revision_id,
        payload_json={"status": page.status, "display_state": state, "progress_label": LABELS[state],
            "revision_number": page.revision_number, "state_revision": sequence,
            "warning": safe_warning(page)})
    session.add(event)
    await session.flush()
    return event


async def begin_result_update(session, page, run_id):
    page.active_execution_run_id = run_id
    page.status = "updating" if page.current_revision_id else "building"
    page.warning = None
    await append_result_event(session, page, "update_started", event_key=f"update:{run_id}")


async def project_task_event(session, event):
    """Project only factual lifecycle changes; never worker prose/tool payloads."""
    allowed = {"task.queued", "task.claimed", "task.progress", "task.requires_input",
        "task.question.answered", "task.context.ready", "task.context.failed", "task.waiting_for_worker",
        "task.waiting_until", "task.control.confirmed", "task.requeued", "task.interrupted",
        "task.completed", "task.failed", "task.cancelled", "task.recovery.queued", "task.recovery.needed"}
    if event.event_type not in allowed:
        return
    run_id = event.execution_run_id
    if run_id is None and event.attempt_id:
        attempt = await session.get(TaskAttempt, event.attempt_id)
        run_id = attempt.execution_run_id if attempt else None
    if run_id is None:
        task = (await session.execute(select(Task).where(Task.id == event.task_id))).scalar_one()
        run_id = task.current_run_id
    pages = (await session.execute(select(ResultPage).where(ResultPage.task_id == event.task_id,
        ResultPage.active_execution_run_id == run_id))).scalars().all()
    for page in pages:
        if event.event_type in {"task.failed", "task.cancelled"}:
            await fail_document_result(session, task_id=event.task_id, run_id=run_id,
                reason="Task cancelled by user." if event.event_type == "task.cancelled" else "Task failed.",
                cancelled=event.event_type == "task.cancelled")
        await append_result_event(session, page, "state", event_key=f"task:{event.id}", task_event_id=event.id)


async def result_snapshot(session, page):
    revision = await session.get(ResultPageRevision, page.current_revision_id) if page.current_revision_id else None
    state, run_status, reason = await display_state(session, page)
    registered = result_type(page.result_kind)
    app = await collection_app(session, page.app_slug)
    saved = await read_data(session, app=app, namespace="saved", key=page.public_id)
    return {
        "public_id": page.public_id, "url": f"/results/{page.public_id}",
        "app_slug": page.app_slug, "app": {"id": app.id, "slug": app.slug, "title": app.title, "route_path": app.route_path},
        "result_kind": page.result_kind, "status": page.status, "display_state": state,
        "progress_label": LABELS[state], "task_status": run_status, "task_status_reason": reason,
        "task_id": page.task_id, "task_url": f"/tasks/{page.task_id}",
        "initial_execution_run_id": page.initial_execution_run_id,
        "active_execution_run_id": page.active_execution_run_id,
        "revision_number": page.revision_number, "state_revision": page.state_revision,
        "title": page.title or registered.label, "summary": page.summary,
        "warning": safe_warning(page), "content": revision.content_json if revision else None,
        "actions": [{"id": "set_saved", "value": saved["value"], "revision": saved["revision"]}],
        "created_at": page.created_at, "updated_at": page.updated_at,
        "first_published_at": page.first_published_at, "last_published_at": page.last_published_at,
    }
