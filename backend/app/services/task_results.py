"""Task results that are records in an installed app's result collection.

A workflow (or a general agent asked for a maintained Document) publishes its
result as a record, for example a recipe in the Recipes app. The core keeps only
the link from the task to that record; the app owns and shows the content, and
the user may edit or delete it there. A follow-up on the task revises the same
record, guarded by the record's revision.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from sqlalchemy import select

from app import app_platform
from app.db.repositories.conversations import utc_now
from app.db.tables import TaskResult
from app.services.task_result_types import collection_output, record_value


def result_url(app_slug: str, collection: str, key: str) -> str:
    """The Cockpit page that opens the app on this record."""
    return f"/apps/{app_slug}?{urlencode({'collection': collection, 'key': key})}"


def present(result: TaskResult) -> dict[str, Any]:
    return {"result_id": result.id, "url": result_url(result.app_slug, result.collection, result.record_key),
            "app": result.app_slug, "collection": result.collection, "key": result.record_key,
            "kind": f"collection:{result.app_slug}.{result.collection}", "title": result.title}


async def current_record(result: TaskResult) -> dict[str, Any] | None:
    """The record as it is now (the user may have changed or deleted it), or None."""
    try:
        return await app_platform.get_record(result.app_slug, result.collection, result.record_key)
    except (app_platform.AppNotFound, app_platform.RecordError):
        return None


async def task_results(session, task_id: str) -> list[TaskResult]:
    return list((await session.execute(select(TaskResult).where(TaskResult.task_id == task_id)
        .order_by(TaskResult.created_at, TaskResult.id))).scalars())


async def write_result(session, *, task_id: str, run_id: str, schema: str, content: dict[str, Any],
                       target: TaskResult | None, expected_revision: int | None) -> TaskResult:
    """Write validated content as the result record and link it to the task.

    The record lives in apps.db, so it is written before the task transaction
    commits. If that commit fails after the write, a retry finds the identical
    record already in place and links it then.
    """
    slug, collection = collection_output(schema)  # type: ignore[misc]
    if target is not None and (target.app_slug, target.collection) != (slug, collection):
        raise ValueError("Follow-up result type differs from the existing result")
    key = target.record_key if target is not None else run_id
    value = record_value(content)
    expected = expected_revision if target is not None else 0
    try:
        await app_platform.put_record(slug, collection, key, value, expected_revision=expected)
    except app_platform.RevisionConflict:
        current = await app_platform.get_record(slug, collection, key)
        if current is None or current["value"] != value:
            raise ValueError("The result changed since it was read; read the current result and publish again") from None
    except (app_platform.AppNotFound, app_platform.RecordError) as exc:
        raise ValueError(str(exc)) from exc
    if target is None:
        target = (await session.execute(select(TaskResult).where(TaskResult.app_slug == slug,
            TaskResult.collection == collection, TaskResult.record_key == key))).scalar_one_or_none()
    if target is None:
        target = TaskResult(task_id=task_id, app_slug=slug, collection=collection, record_key=key)
        session.add(target)
    target.title = content["title"]
    target.execution_run_id = run_id
    target.updated_at = utc_now()
    await session.flush()
    return target
