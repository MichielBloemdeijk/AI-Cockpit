from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.session import session_scope
from app.db.tables import JarvisLiveEvent, NotificationOutbox, TaskEvent
from app.main import app
from app.services.history_retrieval import history_retrieval_service
from app.services.jarvis_live import jarvis_live_service
from app.services.jarvis_preferences import jarvis_preferences_service
from app.services.natural_task_resolver import natural_task_resolver
from app.services.reminder_scheduler import reminder_scheduler
from app.services.task_service import task_service


@pytest.mark.asyncio
async def test_preferences_are_durable_and_report_voice_rotation() -> None:
    initial = await jarvis_preferences_service.get()
    assert initial.voice == "marin"
    changed, voice_changed = await jarvis_preferences_service.patch({
        "voice": "cedar",
        "concision": "balanced",
        "android_privacy": {"assist_structure": True, "screenshots": False},
    })
    restored = await jarvis_preferences_service.get()

    assert voice_changed is True
    assert changed.voice == restored.voice == "cedar"
    assert restored.concision == "balanced"
    assert restored.android_privacy == {"assist_structure": True, "screenshots": False}


@pytest.mark.asyncio
async def test_live_replay_unifies_task_events_with_monotonic_cursor() -> None:
    task = await task_service.dispatch(
        goal="Index the heliotrope launch notes",
        origin_surface="jarvis",
        idempotency_key="v2-live-task",
    )
    await task_service.publish_progress(
        task.task.id,
        summary="Heliotrope notes indexed",
        idempotency_key="v2-live-progress",
    )

    latest, events = await jarvis_live_service.replay(after=0)

    assert latest >= 2
    assert [item.sequence for item in events] == sorted(item.sequence for item in events)
    assert {item.event_type for item in events} >= {"task.queued", "task.progress"}
    async with session_scope() as session:
        assert await session.scalar(select(func.count()).select_from(JarvisLiveEvent)) == len(events)


@pytest.mark.asyncio
async def test_natural_task_focus_and_ambiguity_never_require_ids() -> None:
    wake = await task_service.dispatch(
        goal="Tune the wake sensitivity model",
        origin_surface="jarvis",
        idempotency_key="v2-focus-wake",
    )
    android = await task_service.dispatch(
        goal="Tune the Android sensitivity controls",
        origin_surface="jarvis",
        idempotency_key="v2-focus-android",
    )
    ambiguous = await natural_task_resolver.resolve("sensitivity")
    await natural_task_resolver.touch(wake.task.id)
    focused = await natural_task_resolver.resolve("How is that going?")
    descriptive = await natural_task_resolver.resolve("wake sensitivity")

    assert focused.task is not None and focused.task.id == wake.task.id
    assert descriptive.task is not None and descriptive.task.id == wake.task.id
    assert ambiguous.ambiguous is True
    assert ambiguous.clarification is not None
    assert "wake" in ambiguous.clarification.lower()
    assert "android" in ambiguous.clarification.lower()
    assert wake.task.id not in ambiguous.clarification
    assert android.task.id not in ambiguous.clarification
    assert re.search(r"[0-9a-f]{8}-[0-9a-f-]{27,}", ambiguous.clarification) is None


@pytest.mark.asyncio
async def test_history_grep_is_bounded_path_safe_and_transactionally_current() -> None:
    task = await task_service.dispatch(
        goal="Preserve the cobalt orchard launch checklist",
        origin_surface="jarvis",
        idempotency_key="v2-recall-task",
    )

    matches, truncated, _elapsed = await history_retrieval_service.search(
        '-F -i -w -m 5 -g "task/**" "cobalt" task'
    )

    assert truncated is False
    assert matches
    assert matches[0].internal_source_id == task.task.id
    assert matches[0].virtual_path.startswith("task/")
    with pytest.raises(ValueError, match="stay within"):
        history_retrieval_service.parse('secret ../data')
    with pytest.raises(ValueError, match="Lookarounds"):
        history_retrieval_service.parse('"(?=secret)"')
    with pytest.raises(ValueError, match="Unsupported"):
        history_retrieval_service.parse('--files secret')


@pytest.mark.asyncio
async def test_reminders_deliver_once_and_snooze_replaces_pending_delivery() -> None:
    now = datetime.now(timezone.utc)
    reminder = await reminder_scheduler.create(
        text="water the basil",
        due_at=now + timedelta(minutes=1),
        idempotency_key="v2-reminder-once",
    )

    assert await reminder_scheduler.run_due(now=now + timedelta(minutes=2)) == 1
    assert await reminder_scheduler.run_due(now=now + timedelta(minutes=3)) == 0
    delivered = await task_service.get_task(reminder.task.id)
    assert delivered is not None and delivered.task.status == "completed"
    async with session_scope() as session:
        due_events = await session.scalar(select(func.count()).select_from(TaskEvent).where(
            TaskEvent.task_id == reminder.task.id,
            TaskEvent.event_type == "task.reminder.due",
        ))
        outbox = await session.scalar(select(func.count()).select_from(NotificationOutbox).where(
            NotificationOutbox.task_id == reminder.task.id
        ))
    assert due_events == 1
    assert outbox == 1

    pending = await reminder_scheduler.create(
        text="check the oven",
        due_at=now + timedelta(hours=1),
        idempotency_key="v2-reminder-pending",
    )
    snoozed = await reminder_scheduler.snooze(
        pending.task.id,
        due_at=now + timedelta(hours=2),
        idempotency_key="v2-snooze",
    )
    replaced = await task_service.get_task(pending.task.id)
    assert replaced is not None and replaced.task.status == "cancelled"
    assert snoozed.task.id != pending.task.id
    assert snoozed.task.status == "waiting_until"


@pytest.mark.asyncio
async def test_pairing_credentials_are_scoped_to_the_paired_device() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as admin:
        await admin.post("/api/auth/login", json={"password": "dev"})
        code_response = await admin.post("/api/jarvis/devices/pairing-code")
        assert code_response.status_code == 200
        code = code_response.json()["code"]
    expected_cursor, _ = await jarvis_live_service.replay(after=0, limit=1)

    async with AsyncClient(transport=transport, base_url="http://testserver") as device:
        pairing = await device.post("/api/jarvis/devices/pair", json={"code": code, "label": "Pixel"})
        assert pairing.status_code == 200
        identity = pairing.json()
        assert identity["event_cursor"] == expected_cursor
        device.headers["Authorization"] = f"Bearer {identity['credential']}"
        own = await device.patch(
            f"/api/jarvis/devices/{identity['device_id']}", json={"lock_state": "locked", "event_cursor": 7}
        )
        other = await device.patch(
            "/api/jarvis/devices/00000000-0000-0000-0000-000000000000",
            json={"lock_state": "unlocked"},
        )

    assert own.status_code == 200
    assert own.json()["lock_state"] == "locked"
    assert other.status_code == 403
