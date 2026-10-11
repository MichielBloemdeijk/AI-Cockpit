"""Run-owned output intent, independent of model preferences and workflow syntax."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from sqlalchemy import select

from app.db.tables import ResultPage
from app.services.task_result_types import result_type
from app.services.admission_timing import timed


class ResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["new", "update", "answer"]
    kind: str | None = Field(default=None, min_length=1, max_length=80)
    public_id: str | None = Field(default=None, min_length=1, max_length=36)
    expected_revision: StrictInt | None = Field(default=None, ge=0)


async def resolve_result_request(session, request, *, task_id=None, workflow=None):
    try:
        value = ResultRequest.model_validate(request).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise ValueError(f"Invalid result request: {exc}") from exc
    mode = value["mode"]
    if mode == "update" and (not value.get("public_id") or "expected_revision" not in value):
        raise ValueError("Result update requires public_id and expected_revision")
    if mode == "new" and ("public_id" in value or "expected_revision" in value):
        raise ValueError("New result cannot target an existing revision")
    if mode == "answer" and ("kind" in value or "expected_revision" in value):
        raise ValueError("Answer-only runs cannot declare publication fields")
    page = None
    if "public_id" in value:
        page = (await session.execute(select(ResultPage).where(ResultPage.public_id == value["public_id"],
            ResultPage.task_id == task_id))).scalar_one_or_none()
        if page is None:
            raise ValueError("Result does not belong to this task")
    if mode == "answer":
        if workflow and "task.finish@1" not in workflow["capabilities"]:
            raise ValueError("Workflow does not support answer-only completion")
        return value
    kind = value.get("kind") or (page.result_kind if page else workflow["output_schema"] if workflow else "document.v1")
    if result_type(kind).app_slug is None:
        raise ValueError("Result kind has no maintained page")
    if workflow and workflow["output_schema"] != kind:
        raise ValueError("Result kind differs from the pinned workflow output")
    if not workflow and kind != "document.v1":
        raise ValueError("General agents publish the document.v1 contract")
    if page and (page.result_kind != kind or page.revision_number != value["expected_revision"]):
        raise ValueError("Result update kind or expected revision is stale")
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


async def context_result(session, task_id, run_id):
    """Prefer the run's selected result; answer requests may refer without rebinding."""
    from app.db.tables import TaskExecutionRun
    run = await session.get(TaskExecutionRun, run_id)
    request = (run.configuration_json or {}).get("result") if run else None
    query = select(ResultPage).where(ResultPage.task_id == task_id)
    if request and request.get("public_id"):
        query = query.where(ResultPage.public_id == request["public_id"])
    elif request and request["mode"] == "new":
        query = query.where(ResultPage.initial_execution_run_id == run_id)
    else:
        query = query.order_by((ResultPage.active_execution_run_id == run_id).desc(), ResultPage.created_at.desc(), ResultPage.id)
    return (await session.execute(query.limit(1))).scalar_one_or_none()


async def result_links(session, task_id):
    pages = (await session.execute(select(ResultPage).where(ResultPage.task_id == task_id)
        .order_by(ResultPage.created_at, ResultPage.id))).scalars().all()
    return [{"public_id": page.public_id, "url": f"/results/{page.public_id}",
        "result_kind": page.result_kind, "revision_number": page.revision_number,
        "title": page.title or result_type(page.result_kind).label, "status": page.status} for page in pages]


@timed("result_lookup")
async def admission_result_url(session, task_id, identity, *, turn=None):
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
    if request:
        page = await context_result(session, task_id, turn.execution_run_id)
    else:
        # An old admission must not start returning some later run's result.
        query = select(ResultPage).where(ResultPage.task_id == task_id,
            (ResultPage.initial_execution_run_id == turn.execution_run_id)
            | (ResultPage.active_execution_run_id == turn.execution_run_id))
        page = (await session.execute(query)).scalar_one_or_none()
    return f"/results/{page.public_id}" if page else None
