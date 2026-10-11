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
from app.services.worker_coordinator import worker_coordinator


@pytest.mark.asyncio
async def test_runtime_candidate_requires_authentication_and_the_unchanged_startup_database(monkeypatch):
    from app.config import settings
    from app.jarvis_tasks.acceptance import candidate_identity, database_identity
    runtime = {"candidate": candidate_identity(), "database_identity": database_identity(settings.database_path), "instance_id": "test-startup"}
    monkeypatch.setattr(app.state, "acceptance_runtime", runtime, raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        assert (await client.get("/api/jarvis/runtime-candidate")).status_code == 401
        await client.post("/api/auth/login", json={"password": ""})
        response = await client.get("/api/jarvis/runtime-candidate")
        assert response.status_code == 200 and response.json() == runtime
        assert response.headers["cache-control"] == "no-store"
        monkeypatch.setattr(app.state, "acceptance_runtime", {**runtime, "database_identity": "another-database"})
        assert (await client.get("/api/jarvis/runtime-candidate")).status_code == 409
        monkeypatch.setattr(app.state, "acceptance_runtime", runtime)
        monkeypatch.setattr(settings, "openai_live_reasoning_effort", "high" if settings.openai_live_reasoning_effort == "low" else "low")
        assert (await client.get("/api/jarvis/runtime-candidate")).status_code == 409
        monkeypatch.setattr(app.state, "acceptance_runtime", None)
        assert (await client.get("/api/jarvis/runtime-candidate")).status_code == 503


@pytest.mark.asyncio
async def test_session_replay_uses_session_identity_and_preserves_event_deduplication():
    from app.db.tables import Conversation, LiveSession
    from app.db.repositories.jarvis import JarvisDomainRepository
    async with session_scope() as session:
        conversation = Conversation(title="Session identity")
        session.add(conversation)
        await session.flush()
        live = LiveSession(conversation_id=conversation.id, provider_adapter="fake", lifecycle_state="ready")
        session.add(live)
        await session.flush()
        identity = live.id
        repository = JarvisDomainRepository(session)
        for kind in ("provider.ready", "brain.tool.result"):
            first = await repository.append_session_event_once(session_id=identity,
                event_type=kind, payload_json={"generation": 1}, idempotency_key=kind)
            repeated = await repository.append_session_event_once(session_id=identity,
                event_type=kind, payload_json={"generation": 1}, idempotency_key=kind)
            assert first.created and not repeated.created
            assert first.event.id == repeated.event.id
    _, events = await jarvis_live_service.replay()
    scoped = [event for event in events if event.source_domain == "session"]
    assert len(scoped) == 2
    assert {event.source_id for event in scoped} == {identity}
    assert [event.source_sequence for event in scoped] == [1, 2]


@pytest.mark.asyncio
async def test_preferences_are_durable_and_report_voice_change() -> None:
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
    assert await worker_coordinator.activate(process_identity="v2-live-worker") is not None
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": ""})
        response = await client.get("/api/jarvis/live/replay", params={"after": 0, "limit": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["latest_sequence"] == latest
    assert len(body["events"]) == 1
    assert body["events"][0]["sequence"] == events[0].sequence
    assert body["events"][0]["event_type"] == events[0].event_type

    # A subsequent Live activation reads state after admitted work exists.
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": ""})
        state = await client.get("/api/jarvis/state")
        direct = await client.get("/api/tasks")
    assert state.status_code == direct.status_code == 200
    live_task = next(item for item in state.json()["tasks"] if item["id"] == task.task.id)
    direct_task = next(item for item in direct.json() if item["id"] == task.task.id)
    for field in ("execution_kind", "current_run_id", "goal_revision", "control_revision", "workspace_generation", "queue_entered_at"):
        assert live_task[field] == direct_task[field]


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
async def test_descriptive_control_match_outranks_a_recent_partial_match():
    comparison = await task_service.dispatch(goal="Python comparison task: compare lists and tuples",
        origin_surface="jarvis", idempotency_key="specific-comparison")
    dictionaries = await task_service.dispatch(goal="Create an outline for a guide to using Python dictionaries",
        origin_surface="jarvis", idempotency_key="recent-dictionaries")
    await natural_task_resolver.touch(dictionaries.task.id)
    resolved = await natural_task_resolver.resolve("the Python comparison task", active_only=True)
    assert resolved.task is not None and resolved.task.id == comparison.task.id


@pytest.mark.asyncio
async def test_natural_controls_resolve_work_waiting_for_capacity_and_paused_work():
    task = await task_service.dispatch(goal="Python comparison task: compare lists and tuples",
        origin_surface="jarvis", idempotency_key="waiting-comparison")
    assert task.task.status == "waiting_for_worker"
    resolved = await natural_task_resolver.resolve("the active Python comparison task", active_only=True)
    assert resolved.task is not None and resolved.task.id == task.task.id
    await task_service.control(task.task.id, action="suspend", idempotency_key="pause-comparison")
    resolved = await natural_task_resolver.resolve("Python comparison task", active_only=True)
    assert resolved.task is not None and resolved.task.id == task.task.id and resolved.task.status == "paused"


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
async def test_reminders_deliver_once() -> None:
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



@pytest.mark.asyncio
async def test_pairing_credentials_are_scoped_to_the_paired_device() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as admin:
        await admin.post("/api/auth/login", json={"password": ""})
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
