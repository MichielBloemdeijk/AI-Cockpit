from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.jarvis_brain import restricted_brain_gateway
from app.services.jarvis_coordinator import jarvis_coordinator
from app.services.jarvis_daily import jarvis_daily_service
from app.services.jarvis_time import resolve_reminder_time
from app.services.task_service import task_service


def test_relative_reminder_uses_backend_now_exactly() -> None:
    now = datetime(2026, 9, 2, 20, 16, 32, tzinfo=timezone.utc)

    resolved = resolve_reminder_time(
        timing={"kind": "after", "seconds": 60},
        active_time_zone="Europe/Amsterdam",
        now=now,
    )

    assert resolved.due_at == now + timedelta(seconds=60)
    assert resolved.timing == {"kind": "after", "seconds": 60}


def test_local_time_uses_next_future_occurrence_and_fixed_offsets() -> None:
    now = datetime(2026, 9, 2, 20, 30, tzinfo=timezone.utc)

    local = resolve_reminder_time(
        timing={"kind": "local", "time": "22:15"},
        active_time_zone="Europe/Amsterdam",
        now=now,
    )
    fixed = resolve_reminder_time(
        timing={"kind": "local", "date": "2026-09-03", "time": "09:00", "time_zone": "+03:00"},
        active_time_zone="Europe/Amsterdam",
        now=now,
    )

    assert local.due_at == datetime(2026, 9, 3, 20, 15, tzinfo=timezone.utc)
    assert fixed.due_at == datetime(2026, 9, 3, 6, 0, tzinfo=timezone.utc)
    assert fixed.time_zone == "UTC+03:00"


@pytest.mark.parametrize(
    ("day", "message"),
    [
        ("2026-03-29", "does not exist"),
        ("2026-10-25", "occurs twice"),
    ],
)
def test_dst_gap_and_overlap_require_clarification(day: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        resolve_reminder_time(
            timing={"kind": "local", "date": day, "time": "02:30"},
            active_time_zone="Europe/Amsterdam",
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_timed_reminder_cannot_be_dispatched_as_generic_task() -> None:
    accepted = await jarvis_daily_service.accept_text_turn(
        "Remind me in one minute to check the oven.",
        idempotency_key="timed-reminder-guard",
    )

    with pytest.raises(ValueError, match="timed reminder"):
        await restricted_brain_gateway.execute(
            tool_name="dispatch_task",
            arguments={"goal": "Create a reminder in one minute to check the oven"},
            call_id="wrong-tool",
            conversation_id=accepted.conversation.id,
            origin_turn_id=accepted.message.id,
        )

    assert await task_service.list_tasks() == []


@pytest.mark.asyncio
async def test_structured_reminder_tool_persists_resolved_timing() -> None:
    accepted = await jarvis_daily_service.accept_text_turn(
        "Remind me in one minute.",
        idempotency_key="structured-reminder",
    )
    now = datetime.now(timezone.utc) + timedelta(days=1)

    result = await restricted_brain_gateway.execute(
        tool_name="create_reminder",
        arguments={"timing": {"kind": "after", "seconds": 60}},
        call_id="correct-tool",
        conversation_id=accepted.conversation.id,
        origin_turn_id=accepted.message.id,
        active_time_zone="Europe/Amsterdam",
        now=now,
    )

    tasks = await task_service.list_tasks()
    assert result.output["accepted"] is True
    assert len(tasks) == 1
    reminder = tasks[0].task
    assert reminder.resume_at == (now + timedelta(seconds=60)).replace(tzinfo=None)
    assert reminder.scheduled_delivery_json == {
        "kind": "reminder",
        "text": "This is your reminder.",
        "timing": {"kind": "after", "seconds": 60},
    }


@pytest.mark.asyncio
async def test_realtime_session_persists_device_timezone_in_every_context() -> None:
    accepted = await jarvis_daily_service.accept_text_turn(
        "Hello",
        idempotency_key="timezone-session",
    )
    observed_at = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)

    bootstrap = await jarvis_coordinator.bootstrap_controller(
        conversation_id=accepted.conversation.id,
        controller_id="phone-timezone",
        adapter_name="fake",
        client_context={
            "time_zone": "Europe/Amsterdam",
            "utc_offset_minutes": 120,
            "observed_at": observed_at,
        },
    )
    context = await jarvis_coordinator.context(bootstrap.session.id)

    assert bootstrap.session.client_time_zone == "Europe/Amsterdam"
    assert bootstrap.session.client_utc_offset_minutes == 120
    assert bootstrap.session.client_observed_at == observed_at.replace(tzinfo=None)
    assert context is not None
    assert context["temporal"]["time_zone"] == "Europe/Amsterdam"
    assert context["temporal"]["as_of_utc"]
    assert context["temporal"]["local_time"]
