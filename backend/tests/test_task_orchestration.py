from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.config import settings
from app.db.session import session_scope
from app.db.tables import (
    OrchestrationAction, OrchestrationDecision, Task, TaskProjectionCheckpoint,
    TaskQuestion, TaskSupervisorLease,
)
from app.services.orchestration_authority import StaleDecision
from app.services.task_orchestration import (
    CURSOR_KEY, LEASE_KEY, DecisionTools, TaskOrchestrationService, now,
)
from app.services.task_presentations import task_presentation_service
from app.services.task_service import task_service


@pytest.fixture(autouse=True)
def orchestration_flag(monkeypatch):
    monkeypatch.setattr(settings, "task_orchestration_enabled", True)


class FixtureRuntime:
    def __init__(self, action):
        self.action = action
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def decide(self, claim, tools):
        self.calls.append(claim)
        self.started.set()
        return await self.action(claim, tools)

    async def stop(self):
        self.release.set()


async def rows(model):
    async with session_scope() as session:
        return list((await session.execute(select(model))).scalars().all())


async def question(goal="Use staging as the environment. Deploy the report."):
    task = (await task_service.dispatch(goal=goal, origin_surface="test", idempotency_key="question-task")).task
    q = await task_service.ask_question(task.id, question="Which environment should I use?", idempotency_key="environment")
    return task, q


async def failure():
    task = (await task_service.dispatch(goal="Fetch the report", origin_surface="test", idempotency_key="failure-task")).task
    await task_service.complete(task.id, summary="Transient fetch failure", status="failed", idempotency_key="first-failure")
    return task


async def ready(runtime=None):
    service = TaskOrchestrationService(runtime)
    assert await service.acquire()
    await service.intake()
    assert await rows(OrchestrationDecision)
    return service


@pytest.mark.asyncio
async def test_context_answer_uses_exact_question_and_asking_run_without_voice():
    task, q = await question()

    async def decide(claim, tools):
        assert "staging" in claim.context["task"]["goal"]
        inspection = await tools.call("inspect", {})
        assert inspection["status"] == "waiting_for_input"
        return await tools.call("answer", {"answer": "staging", "operation_key": "answer-environment", "reason": "The user's goal explicitly says staging."})

    runtime = FixtureRuntime(decide)
    service = await ready(runtime)
    assert await task_presentation_service.list_pending() == ()
    assert await service.run_one()
    detail = await task_service.get_task(task.id)
    assert detail.task.current_run_id == task.current_run_id
    assert detail.task.status in {"queued", "waiting_for_worker"}
    assert detail.questions[0].answer == "staging"
    assert detail.questions[0].asking_run_id == task.current_run_id
    assert len(runtime.calls) == 1
    assert (await rows(OrchestrationDecision))[0].status == "completed"
    assert len(await rows(OrchestrationAction)) == 1
    await service.intake()
    assert not await service.run_one()  # no self-trigger from its own answer


@pytest.mark.asyncio
async def test_unresolved_question_reuses_attention_authority_and_never_answers_on_announcement():
    task, q = await question("Deploy a report; ask me which environment.")

    async def decide(claim, tools):
        return await tools.call("retain", {"reason": "Only the user can choose this environment.", "operation_key": "retain-question"})

    service = await ready(FixtureRuntime(decide))
    assert await task_presentation_service.list_pending() == ()
    assert await service.run_one()
    pending = await task_presentation_service.list_pending()
    assert len(pending) == 1
    assert pending[0].required_facts_json["question_id"] == q.id
    await service.intake()
    await task_presentation_service.sync_candidates()
    assert [row.id for row in await task_presentation_service.list_pending()] == [pending[0].id]
    assert (await rows(TaskQuestion))[0].state == "asked"
    await task_service.answer_question(task.id, q.id, answer="production", idempotency_key="direct-user")
    await service.intake()
    assert await task_presentation_service.list_pending() == ()


@pytest.mark.asyncio
async def test_blocked_model_leaves_intake_shared_voice_actions_and_history_independent():
    await question()
    runtime = None

    async def block(claim, tools):
        await runtime.release.wait()
        return await tools.call("retain", {"reason": "Waiting for user choice.", "operation_key": "retain"})

    runtime = FixtureRuntime(block)
    service = await ready(runtime)
    background = asyncio.create_task(service.run_one())
    try:
        await asyncio.wait_for(runtime.started.wait(), 2)
        from test_m3_preflight import exercise_code_only_services, saturate_pool
        occupied = await saturate_pool()
        await asyncio.wait_for(exercise_code_only_services(occupied), 5)
        await asyncio.wait_for(service.intake(), 2)
        assert not background.done()
        pending = await task_presentation_service.list_pending()
        assert any(row.candidate_type == "terminal_result" for row in pending)
        assert len(runtime.calls) == 1
    finally:
        runtime.release.set()
        await asyncio.wait_for(background, 3)


@pytest.mark.asyncio
async def test_feedback_cursor_projection_and_issue_roll_back_together():
    await question()
    completed = (await task_service.dispatch(goal="Factual result", origin_surface="test", idempotency_key="result")).task
    await task_service.complete(completed.id, summary="Done", idempotency_key="done")
    service = TaskOrchestrationService()

    async def crash():
        raise RuntimeError("Crash before feedback commit")

    with pytest.raises(RuntimeError, match="before feedback"):
        await service.intake(before_commit=crash)
    assert await rows(OrchestrationDecision) == []
    assert await rows(TaskProjectionCheckpoint) == []
    assert await task_presentation_service.list_pending() == ()
    assert await service.intake() == 1
    cursor = (await rows(TaskProjectionCheckpoint))[0].last_sequence
    assert cursor > 0
    assert len(await rows(OrchestrationDecision)) == 1
    assert await service.intake() == 0
    assert len(await task_presentation_service.list_pending()) == 1


@pytest.mark.asyncio
async def test_atomic_single_flight_claim_and_no_takeover_on_expiry_alone():
    await question()
    first = await ready()
    competitor = TaskOrchestrationService(owner_has_exited=lambda owner: False)
    async with session_scope() as session:
        await session.execute(update(TaskSupervisorLease).where(TaskSupervisorLease.lease_key == LEASE_KEY)
                              .values(expires_at=now() - timedelta(days=1)))
    assert not await competitor.acquire()
    claims = await asyncio.gather(first.claim(), first.claim())
    assert sum(claim is not None for claim in claims) == 1
    assert not await first.claim()


@pytest.mark.asyncio
@pytest.mark.parametrize("user_action", ["answer", "cancel", "correction", "replace_run"])
async def test_stale_model_cannot_override_user_answer_cancel_correction_or_run(user_action):
    task, q = await question()
    service = await ready()
    claim = await service.claim()
    tools = DecisionTools(claim)
    if user_action == "answer":
        await task_service.answer_question(task.id, q.id, answer="production", idempotency_key="user-answer")
    elif user_action == "cancel":
        await task_service.cancel(task.id, idempotency_key="user-cancel")
    elif user_action == "correction":
        await task_service.continue_task(task.id, content="Use production instead", origin="jarvis", idempotency_key="user-correction")
    else:
        await task_service.complete(task.id, summary="No longer needed", idempotency_key="end-run")
        await task_service.continue_task(task.id, content="New goal", origin="jarvis", idempotency_key="new-run")
    with pytest.raises(StaleDecision):
        await tools.call("answer", {"answer": "staging", "operation_key": "answer", "reason": "Old model context."})
    assert await rows(OrchestrationAction) == []


@pytest.mark.asyncio
async def test_action_commit_crash_replay_and_changed_arguments():
    task = await failure()
    service = await ready()
    claim = await service.claim()
    tools = DecisionTools(claim)
    args = {"goal": "Repair the fetch endpoint", "reason": "The endpoint needs substantive repair.", "operation_key": "repair"}
    first = await tools.call("delegate", args)
    duplicate = await tools.call("delegate", args)
    assert first == duplicate
    with pytest.raises(ValueError, match="changed arguments"):
        await tools.call("delegate", {**args, "goal": "Different repair"})
    with pytest.raises(ValueError, match="changed arguments"):
        await tools.call("delegate", {**args, "reason": "Different reason"})
    # Simulated verified host exit, not expiry-based takeover.
    replacement = TaskOrchestrationService(owner_has_exited=lambda owner: True)
    assert await replacement.acquire()
    assert not await replacement.run_one()
    assert (await rows(OrchestrationDecision))[0].status == "completed"
    assert len(await rows(Task)) == 2
    assert len(await rows(OrchestrationAction)) == 1
    with pytest.raises(StaleDecision, match="claim"):
        await tools.call("retain", {"reason": "Old host must be fenced.", "operation_key": "old"})
    await task_service.complete(first["task_id"], summary="Endpoint repaired", idempotency_key="repair-result")
    await replacement.intake()
    decisions = await rows(OrchestrationDecision)
    assert len(decisions) == 2
    subsequent = await replacement.claim()
    assert subsequent.context["repair_result"]["task_id"] == first["task_id"]
    assert subsequent.context["task"]["id"] == task.id


@pytest.mark.asyncio
async def test_failure_retry_is_autonomous_but_unchanged_failure_never_loops():
    task = await failure()

    async def decide(claim, tools):
        return await tools.call("control", {"action": "retry", "reason": "Transient fetch can be retried once.", "operation_key": "retry"})

    runtime = FixtureRuntime(decide)
    service = await ready(runtime)
    assert await service.run_one()
    detail = await task_service.get_task(task.id)
    assert len(detail.attempts) == 2
    await task_service.complete(task.id, summary="Transient fetch failure", status="failed", idempotency_key="second-failure")
    for i in range(10):
        await task_service.record_event(task.id, event_type="task.heartbeat", payload={"tick": i}, idempotency_key=f"heartbeat:{i}")
    await service.intake()
    assert not await service.run_one()
    assert len(runtime.calls) == 1
    assert len(await rows(OrchestrationDecision)) == 1


@pytest.mark.asyncio
async def test_billing_denial_retains_context_and_does_not_poll_billable_retries():
    await question()

    async def denial(claim, tools):
        raise RuntimeError("insufficient_quota: credit exhausted")

    runtime = FixtureRuntime(denial)
    service = await ready(runtime)
    assert await service.run_one()
    for _ in range(3):
        await service.intake()
        assert not await service.run_one()
    decision = (await rows(OrchestrationDecision))[0]
    assert decision.status == "deferred"
    assert decision.due_at is None
    assert decision.last_error == "billing_exhausted"
    assert decision.context_digest
    assert len(runtime.calls) == 1
    assert (await rows(TaskQuestion))[0].state == "asked"


@pytest.mark.asyncio
async def test_model_cannot_supply_authority_or_native_capabilities():
    await question()
    service = await ready()
    tools = DecisionTools(await service.claim())
    for name, args in [("shell", {}), ("delegate", {"task_id": "other"}),
                       ("answer", {"expected_revision": 0}), ("retain", {"generation": 100})]:
        with pytest.raises(ValueError, match="capability|authority"):
            await tools.call(name, args)
    assert await rows(OrchestrationAction) == []


@pytest.mark.asyncio
async def test_two_running_loops_and_readonly_diagnostics_remain_live_when_model_is_blocked():
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    runtime = None

    async def blocked(claim, tools):
        await runtime.release.wait()
        return await tools.call("retain", {"reason": "Needs a user choice", "operation_key": "retain"})

    runtime = FixtureRuntime(blocked)
    service = TaskOrchestrationService(runtime)
    await question()
    await service.start()
    try:
        await asyncio.wait_for(runtime.started.wait(), 3)
        result = (await task_service.dispatch(goal="A completed fact", origin_surface="test", idempotency_key="loop-result")).task
        await task_service.complete(result.id, summary="Done", idempotency_key="loop-done")

        async def wait_for_fact():
            while not any(row.task_id == result.id for row in await task_presentation_service.list_pending()):
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_fact(), 2)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/api/auth/login", json={"password": ""})).status_code == 200
            response = await client.get("/api/orchestration")
        assert response.status_code == 200
        assert response.json()["decision_counts"]["running"] == 1
        assert response.json()["committed_action_count"] == 0
    finally:
        await service.stop()
    assert not service._loops


@pytest.mark.asyncio
async def test_native_uncertain_turn_is_not_restarted_on_owner_exit():
    await question()
    service = await ready()
    claim = await service.claim()
    async with session_scope() as session:
        row = await session.get(OrchestrationDecision, claim.id)
        row.native_binding_json = {"thread_id": "persisted-thread", "turn_id": "uncertain-turn"}
    replacement = TaskOrchestrationService(owner_has_exited=lambda owner: True)
    assert await replacement.acquire()
    assert not await replacement.run_one()
    row = (await rows(OrchestrationDecision))[0]
    assert row.status == "deferred" and row.last_error == "runtime_uncertain"
    assert row.native_binding_json["turn_id"] == "uncertain-turn"


@pytest.mark.asyncio
async def test_host_error_after_action_commit_completes_from_receipt_without_second_call():
    await question()

    async def lost_completion(claim, tools):
        await tools.call("answer", {"answer": "staging", "reason": "Explicit user instruction", "operation_key": "answer"})
        raise RuntimeError("Lost native completion after committed answer")

    runtime = FixtureRuntime(lost_completion)
    service = await ready(runtime)
    assert await service.run_one()
    row = (await rows(OrchestrationDecision))[0]
    assert row.status == "completed"
    assert row.outcome_json["reconciled_actions"]
    assert not await service.run_one()
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_supervisor_recovery_needed_retains_failure_in_the_existing_inbox():
    from app.db.repositories.tasks import TaskRepository
    task = (await task_service.dispatch(goal="Recover a failed report", origin_surface="test", idempotency_key="recovery-needed")).task
    # Represent the supervisor's committed post-exit state/event rather than
    # adding a task.failed fact that its actual path does not produce.
    async with session_scope() as session:
        await session.execute(update(Task).where(Task.id == task.id).values(
            status="interrupted", terminal_summary="Worker exited with an uncertain operation outcome.",
        ))
        await TaskRepository(session).append_event(task_id=task.id, execution_run_id=task.current_run_id,
            event_type="task.recovery.needed", payload_json={"reason": "uncertain_operation"},
            idempotency_key="supervisor:recovery-needed")

    async def retain(claim, tools):
        return await tools.call("retain", {"reason": "Recovery needs a permission-safe user choice.", "operation_key": "retain"})

    service = await ready(FixtureRuntime(retain))
    assert await service.run_one()
    pending = await task_presentation_service.list_pending()
    assert len(pending) == 1 and pending[0].task_id == task.id
    assert pending[0].candidate_type == "terminal_result"
    await service.intake()
    assert not await service.run_one()


def test_voice_prompt_assigns_only_background_automatic_feedback_ownership():
    from app.services.openai_live_adapter import OpenAILiveAdapter
    config = OpenAILiveAdapter._session_config({})
    assert "sole automatic worker-feedback owner" in config["instructions"]
    assert "exclusively handles automatic worker questions" in config["delegation"]["responses"]["instructions"]


@pytest.mark.asyncio
async def test_claim_refreshes_bounded_context_and_only_approved_standing_instructions():
    import json
    from uuid import uuid4
    from app.db.tables import Conversation, MemoryItem
    await question()
    service = await ready()
    async with session_scope() as session:
        conversation = Conversation(id=str(uuid4()), title="Preferences")
        session.add(conversation)
        await session.flush()
        session.add_all([MemoryItem(id=str(uuid4()), scope="preference", kind="preference", title="Environment",
            content="Use staging unless explicitly told otherwise", status=status, source_conversation_id=conversation.id)
            for status in ["approved", "proposed", "rejected", "deleted"]])
    other = (await task_service.dispatch(goal="New factual task", origin_surface="test", idempotency_key="fresh-context")).task
    claim = await service.claim()
    assert len(claim.context["standing_instructions"]) == 1
    assert claim.context["standing_instructions"][0]["content"].startswith("Use staging")
    assert other.id in {item["task_id"] for item in claim.context["situation"]["tasks"]}
    assert len(json.dumps(claim.context, ensure_ascii=False).encode()) <= 48000
    assert claim.context["observed"]["instructions_digest"]


@pytest.mark.asyncio
async def test_instruction_change_during_reasoning_fences_answer_and_queues_fresh_issue():
    from uuid import uuid4
    from app.db.tables import Conversation, MemoryItem
    await question()
    async with session_scope() as session:
        conversation = Conversation(id=str(uuid4()))
        session.add(conversation)
        await session.flush()
        memory = MemoryItem(id=str(uuid4()), scope="preference", kind="preference", title="Environment",
                            content="Use staging", status="approved", source_conversation_id=conversation.id)
        session.add(memory)

    async def decide(claim, tools):
        async with session_scope() as session:
            row = await session.get(MemoryItem, memory.id)
            row.content = "Ask before deploying"
        return await tools.call("answer", {"answer": "staging", "reason": "Old preference", "operation_key": "answer"})

    service = await ready(FixtureRuntime(decide))
    assert await service.run_one()
    decisions = await rows(OrchestrationDecision)
    assert {row.status for row in decisions} == {"obsolete", "pending"}
    fresh = await service.claim()
    assert fresh.context["standing_instructions"][0]["content"] == "Ask before deploying"
    assert (await rows(TaskQuestion))[0].state == "asked"


@pytest.mark.asyncio
async def test_original_evidence_chunks_are_lossless_bounded_and_task_scoped():
    import json
    from hashlib import sha256
    from uuid import uuid4
    from app.db.tables import TaskArtifact, TaskTurn
    task, _ = await question()
    content = "Original evidence: 🦉 staging " * 1500
    event = await task_service.record_event(task.id, event_type="task.evidence", payload={"content": content}, idempotency_key="large-event")
    other = (await task_service.dispatch(goal="Private unrelated task", origin_surface="test", idempotency_key="other")).task
    async with session_scope() as session:
        artifact = TaskArtifact(id=str(uuid4()), task_id=task.id, artifact_type="report", mime_type="text/plain", content_text=content)
        private = TaskArtifact(id=str(uuid4()), task_id=other.id, artifact_type="report", mime_type="text/plain", content_text="private")
        session.add_all([artifact, private, TaskTurn(id=str(uuid4()), task_id=task.id, sequence=50,
                                                 role="assistant", origin="worker", content=content)])
    tools = DecisionTools(await (await ready()).claim())
    for selector in [{"event_sequence": event.sequence}, {"turn_sequence": 50}, {"artifact_id": artifact.id}]:
        chunks, offset, expected_hash = [], 0, None
        while True:
            part = await tools.call("inspect", {**selector, "byte_offset": offset})
            assert len(part["evidence_json_chunk"].encode("ascii")) <= 8000
            expected_hash = expected_hash or part["sha256"]
            assert part["sha256"] == expected_hash
            chunks.append(part["evidence_json_chunk"])
            offset = part["next_byte_offset"]
            if offset == part["total_bytes"]:
                break
        encoded = "".join(chunks).encode("ascii")
        assert sha256(encoded).hexdigest() == expected_hash
        evidence = json.loads(encoded)
        assert (evidence.get("content") or evidence.get("content_text") or evidence["payload"]["content"]) == content
    with pytest.raises(ValueError, match="does not belong"):
        await tools.call("inspect", {"artifact_id": private.id})
    for invalid in [{"after_sequence": True}, {"event_sequence": event.sequence, "turn_sequence": 50},
                    {"byte_offset": 1}, {"turn_sequence": 50, "byte_offset": 999999}]:
        with pytest.raises(ValueError):
            await tools.call("inspect", invalid)


@pytest.mark.asyncio
async def test_guarded_presentation_preference_is_idempotent_and_retains_unanswered_question():
    task, _ = await question()
    service = await ready()
    tools = DecisionTools(await service.claim())
    args = {"policy": "follow_progress", "reason": "User wants progress updates", "operation_key": "presentation"}
    assert await tools.call("presentation", args) == await tools.call("presentation", args)
    with pytest.raises(ValueError, match="changed arguments"):
        await tools.call("presentation", {**args, "policy": "silent"})
    assert len(await rows(OrchestrationAction)) == 1
    assert len(await task_presentation_service.list_pending()) == 1
    detail = await task_service.get_task(task.id)
    assert detail.task.presentation_policy == "follow_progress"
    assert detail.questions[0].state == "asked"


@pytest.mark.asyncio
async def test_direct_user_update_preference_fences_background_mutation():
    task, _ = await question()
    tools = DecisionTools(await (await ready()).claim())
    await task_service.set_update_policy(task.id, "silent")
    with pytest.raises(StaleDecision):
        await tools.call("presentation", {"policy": "follow_progress", "reason": "Old context", "operation_key": "late"})


def test_context_budget_handles_unicode_and_advertises_retrievable_truncation():
    import json
    from app.services.orchestration_context import bound_context
    context = {"task": {"goal": "🦉" * 12000}, "question": "Which environment?",
               "events": [{"payload": "x" * 600} for _ in range(8)], "turns": [], "prior_actions": [], "artifacts": []}
    result = bound_context(context)
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 48000
    assert result["question"] == "Which environment?"
    assert result["context_budget"]["trimmed_sections"]


@pytest.mark.asyncio
async def test_approved_instruction_catalog_pages_and_retrieves_original_without_proposed_memory():
    import json
    from uuid import uuid4
    from app.db.tables import Conversation, MemoryItem
    task, _ = await question()
    async with session_scope() as session:
        conversation = Conversation(id=str(uuid4()))
        session.add(conversation)
        await session.flush()
        memories = [MemoryItem(id=str(uuid4()), scope="preference", kind="preference", title=f"Preference {i}",
                               content=f"Approved instruction {i}" * 100, status="approved", source_conversation_id=conversation.id)
                    for i in range(12)]
        proposed = MemoryItem(id=str(uuid4()), scope="preference", kind="preference", title="Unaccepted",
                              content="Do not trust this yet", status="proposed", source_conversation_id=conversation.id)
        session.add_all([*memories, proposed])
    service = await ready()
    claim = await service.claim()
    assert len(claim.context["standing_instructions"]) == 8
    assert claim.context["standing_instruction_count"] == 12
    tools = DecisionTools(claim)
    first = await tools.call("inspect", {"instructions_after": ""})
    second = await tools.call("inspect", {"instructions_after": first["next_cursor"]})
    assert len(first["instructions"]) == 8 and len(second["instructions"]) == 4
    assert not second["has_more"]
    assert {item["id"] for item in first["instructions"] + second["instructions"]} == {memory.id for memory in memories}
    original = await tools.call("inspect", {"instruction_id": memories[0].id})
    assert json.loads(original["evidence_json_chunk"])["content"] == memories[0].content
    goal = await tools.call("inspect", {"task_goal": True})
    assert json.loads(goal["evidence_json_chunk"])["content"] == task.goal
    with pytest.raises(ValueError, match="not an approved"):
        await tools.call("inspect", {"instruction_id": proposed.id})
    # A change outside the initial excerpts also fences mutation.
    included = {item["id"] for item in claim.context["standing_instructions"]}
    omitted = next(memory for memory in memories if memory.id not in included)
    async with session_scope() as session:
        row = await session.get(MemoryItem, omitted.id)
        row.content = "New authoritative instruction"
    with pytest.raises(StaleDecision):
        await tools.call("answer", {"answer": "staging", "reason": "Old context", "operation_key": "late"})
