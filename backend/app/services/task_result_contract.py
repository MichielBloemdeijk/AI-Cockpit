"""Run-owned output intent, independent of model preferences and workflow syntax."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from sqlalchemy import select

from app.db.tables import TaskResult
from app.services.task_result_types import DOCUMENT_OUTPUT, collection_output
from app.services.task_results import current_record, present, task_results
from app.services.admission_timing import timed


class ResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["new", "update", "answer"]
    result_id: str | None = Field(default=None, min_length=1, max_length=36)
    expected_revision: StrictInt | None = Field(default=None, ge=0)


async def resolve_result_request(session, request, *, task_id=None, workflow=None):
    try:
        value = ResultRequest.model_validate(request).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise ValueError(f"Invalid result request: {exc}") from exc
    mode = value["mode"]
    if mode == "answer":
        if "expected_revision" in value:
            raise ValueError("Answer-only runs cannot declare publication fields")
        if workflow and "task.finish@1" not in workflow["capabilities"]:
            raise ValueError("Workflow does not support answer-only completion")
        # An answer may name the result it is about, without revising it.
        if "result_id" in value and (await session.get(TaskResult, value["result_id"]) or TaskResult()).task_id != task_id:
            raise ValueError("Result does not belong to this task")
        return value
    if mode == "new" and ("result_id" in value or "expected_revision" in value):
        raise ValueError("New result cannot target an existing revision")
    # General agents publish Documents; a workflow publishes its pinned output.
    kind = workflow["output_schema"] if workflow else DOCUMENT_OUTPUT
    output = collection_output(kind)
    if output is None:
        raise ValueError("This workflow's result is not kept as an app record")
    if mode == "update":
        if "result_id" in value:
            result = (await session.execute(select(TaskResult).where(TaskResult.id == value["result_id"],
                TaskResult.task_id == task_id))).scalar_one_or_none()
            if result is None:
                raise ValueError("Result does not belong to this task")
        else:
            # "Update the recipe" names the task's result, not an ID.
            results = await task_results(session, task_id) if task_id else []
            if not results:
                raise ValueError("This task has no result to update")
            if len(results) > 1:
                raise ValueError("Task has multiple results; say which one to update")
            result = results[0]
        if (result.app_slug, result.collection) != output:
            raise ValueError("Result kind differs from the existing result")
        record = await current_record(result)
        if record is None:
            raise ValueError("The result was deleted from its app; ask for a new one")
        # Without an explicit base the edit applies to the record as it is at
        # admission; publication still fences it against later changes.
        value.setdefault("expected_revision", record["revision"])
        if value["expected_revision"] != record["revision"]:
            raise ValueError("Result update expected revision is stale")
        value["result_id"] = result.id
    return {**value, "kind": kind}


def publication_profile(run):
    """Return a trusted output profile, never one selected by a worker payload."""
    from app.services.task_workflows import validate_pinned_workflow
    config = dict(run.configuration_json or {})
    request = config.get("result")
    if run.execution_kind == "workflow":
        if not config.get("workflow"):
            return None  # legacy deterministic fixture route
        profile = validate_pinned_workflow(config)
    elif request and request["mode"] != "answer":
        profile = {"output_schema": request["kind"], "schema": "task-result.v1", "capabilities": []}
    else:
        return None
    if request and request["mode"] == "answer":
        return None
    return profile


async def context_result(session, task_id, run_id) -> TaskResult | None:
    """Prefer the run's selected result; answer requests may refer without rebinding."""
    from app.db.tables import TaskExecutionRun
    run = await session.get(TaskExecutionRun, run_id)
    request = (run.configuration_json or {}).get("result") if run else None
    query = select(TaskResult).where(TaskResult.task_id == task_id)
    if request and request.get("result_id"):
        query = query.where(TaskResult.id == request["result_id"])
    elif request and request["mode"] == "new":
        # A new result's record is keyed by the run that created it.
        query = query.where(TaskResult.record_key == run_id)
    else:
        query = query.order_by((TaskResult.execution_run_id == run_id).desc(), TaskResult.updated_at.desc(), TaskResult.id)
    return (await session.execute(query.limit(1))).scalar_one_or_none()


async def maintained_view(result: TaskResult | None, *, content_limit: int):
    """What a worker needs to revise a result: its identity, revision and current content."""
    if result is None:
        return None
    import json
    view = {key: value for key, value in present(result).items() if key != "url"}
    record = await current_record(result)
    if record is None:
        view["deleted"] = "The user deleted this record; publish a new result if one is still wanted."
        return view
    view["revision"] = record["revision"]
    if len(json.dumps(record["value"], ensure_ascii=False)) <= content_limit:
        view["current_content"] = record["value"]
    else:
        view["current_content_omitted"] = "Read jarvis_task.task_history for the full current content."
    return view


async def result_links(session, task_id):
    return [present(result) for result in await task_results(session, task_id)]


@timed("result_lookup")
async def admission_result_url(session, task_id, identity, *, turn=None):
    """The page of the result this turn revises; a new result has no page until it is published."""
    from app.db.tables import TaskTurn, TaskExecutionRun
    if turn is None:
        turn = (await session.execute(select(TaskTurn).where(TaskTurn.task_id == task_id,
            TaskTurn.request_identity == identity))).scalar_one_or_none()
    if turn is None:
        return None
    if turn.task_id != task_id or turn.request_identity != identity:
        raise ValueError("Admission result lookup requires its originating turn")
    run = await session.get(TaskExecutionRun, turn.execution_run_id)
    request = (run.configuration_json or {}).get("result") if run else None
    result = await session.get(TaskResult, request["result_id"]) if request and request.get("result_id") else None
    if result is None and run is not None:
        result = (await session.execute(select(TaskResult).where(TaskResult.task_id == task_id,
            TaskResult.record_key == run.id))).scalar_one_or_none()
    return present(result)["url"] if result is not None and result.task_id == task_id else None
