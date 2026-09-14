from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.jarvis_brain import restricted_brain_gateway
from app.services.jarvis_daily import jarvis_daily_service
from app.services.jarvis_situation import jarvis_situation_projector
from app.services.task_error_messages import public_failure_summary, public_tool_error
from app.services.task_presentations import task_presentation_service
from app.services.task_service import task_service


RAW_DATABASE_ERROR = (
    "(sqlite3.IntegrityError) FOREIGN KEY constraint failed "
    "[SQL: INSERT INTO generated_apps (id, task_id) VALUES (?, ?)] "
    "[parameters: ('app-1', 'task-1')]"
)


def test_technical_errors_are_classified_without_exposing_diagnostics() -> None:
    assert public_failure_summary(RAW_DATABASE_ERROR) == (
        "The task failed because of a database error."
    )
    assert public_tool_error(RAW_DATABASE_ERROR) == {
        "error": "The task failed because of a database error.",
        "diagnostics_available": True,
    }
    assert public_failure_summary("The source document was empty.") == (
        "The source document was empty."
    )


@pytest.mark.asyncio
async def test_failed_task_defaults_to_brief_error_but_diagnostics_remain_retrievable() -> None:
    accepted = await jarvis_daily_service.accept_text_turn(
        "Research the healthiest food in the background",
        idempotency_key="failure-origin",
    )
    snapshot = await task_service.dispatch(
        goal="Perform a comprehensive evaluation of foods to identify a single best choice for nutrition",
        origin_surface="jarvis",
        origin_conversation_id=accepted.conversation.id,
        origin_turn_id=accepted.message.id,
        idempotency_key="failure-task",
    )
    await task_service.complete(
        snapshot.task.id,
        status="failed",
        summary=RAW_DATABASE_ERROR,
        idempotency_key="failure-terminal",
    )
    await task_presentation_service.sync_candidates()

    detail = await task_service.get_task(snapshot.task.id)
    assert detail is not None
    assert detail.task.terminal_summary == RAW_DATABASE_ERROR
    assert detail.events[-1].payload_json["summary"] == RAW_DATABASE_ERROR
    facts = detail.presentations[0].required_facts_json
    assert facts["summary"] == "The task failed because of a database error."
    assert "sqlite" not in str(facts).casefold()
    assert "insert into" not in str(facts).casefold()
    assert "required_spoken_phrase" not in facts

    situation = await jarvis_situation_projector.project(
        conversation_id=accepted.conversation.id,
        now=datetime.now(timezone.utc),
    )
    recent = situation.payload["recent_terminal_tasks"][0]
    assert recent["summary"] == "The task failed because of a database error."
    assert recent["diagnostics_available"] is True

    brief = await restricted_brain_gateway.execute(
        tool_name="get_task",
        arguments={"task_reference": snapshot.task.id},
        call_id="brief-error",
        conversation_id=accepted.conversation.id,
        origin_turn_id=accepted.message.id,
    )
    assert brief.output["summary"] == "The task failed because of a database error."
    assert brief.output["diagnostics_available"] is True
    assert "diagnostic_error" not in brief.output

    detailed = await restricted_brain_gateway.execute(
        tool_name="get_task",
        arguments={"task_reference": snapshot.task.id, "include_diagnostics": True},
        call_id="detailed-error",
        conversation_id=accepted.conversation.id,
        origin_turn_id=accepted.message.id,
    )
    assert detailed.output["diagnostic_error"] == RAW_DATABASE_ERROR
