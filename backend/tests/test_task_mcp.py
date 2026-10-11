from __future__ import annotations


def test_stdio_uses_utf8_even_with_windows_legacy_codepage(tmp_path):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    bridge = Path(__file__).resolve().parents[1] / "app/jarvis_tasks/task_mcp.py"
    identity = "quantity-1¼-×-é"
    completed = subprocess.run([sys.executable, str(bridge), "--database-url", "sqlite+aiosqlite:///:memory:",
        "--task-id", "test", "--run-id", "test", "--attempt-id", "test", "--claim-token", "test",
        "--workspace", str(tmp_path)], input=(json.dumps({"jsonrpc": "2.0", "id": identity,
        "method": "initialize"}, ensure_ascii=False) + "\n").encode("utf-8"), capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"}, timeout=20)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert json.loads(completed.stdout)["id"] == identity

from pathlib import Path
from types import SimpleNamespace
import json
from hashlib import sha256

import pytest
from sqlalchemy import select

from app.jarvis_tasks.task_mcp import _call
from app.db.session import session_scope
from app.db.tables import TaskContextBundle
from app.services.task_context import current_run_delta, portable_context
from app.services.task_operation_recovery import confirmed_file_write
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator


@pytest.mark.asyncio
async def test_answer_after_pinned_context_is_available_to_resumed_run():
    created = await task_service.dispatch(goal="Explain the menu dish", origin_surface="test",
        idempotency_key="answer-after-context")
    worker = await worker_coordinator.activate(process_identity="answer-context-worker")
    claim = (await worker_coordinator.claim_next(worker.worker_id)).task
    handoff = await portable_context(created.task.id, run_id=claim.execution_run_id,
        attempt_id=claim.attempt_id, claim_token=claim.claim_token)
    question = await task_service.ask_question(created.task.id, question="Which dish?",
        idempotency_key="menu-question", attempt_id=claim.attempt_id,
        execution_run_id=claim.execution_run_id, claim_token=claim.claim_token)
    await worker_coordinator.release_claim(worker.worker_id, claim.attempt_id)
    await task_service.answer_question(created.task.id, question.id, answer="Tomato pasta, vegetarian",
        idempotency_key="menu-answer")
    replacement = (await worker_coordinator.claim_next(worker.worker_id)).task
    assert replacement.execution_run_id == claim.execution_run_id
    assert await portable_context(created.task.id, run_id=replacement.execution_run_id,
        attempt_id=replacement.attempt_id, claim_token=replacement.claim_token) == handoff
    delta = json.loads(await current_run_delta(created.task.id, run_id=replacement.execution_run_id,
        attempt_id=replacement.attempt_id, claim_token=replacement.claim_token,
        after_sequence=json.loads(handoff)["latest_event_sequence"]))
    assert [row["content"] for row in delta["user_turns"]] == ["Tomato pasta, vegetarian"]
    assert delta["user_turns"][0]["question_id"] == question.id


def test_post_exit_file_proof_rejects_mismatch_and_escape(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "proof.txt").write_text("different", encoding="utf-8")
    digest = sha256(b"expected").hexdigest()
    assert confirmed_file_write(str(root), {"path": "proof.txt", "sha256": digest}) is None
    assert confirmed_file_write(str(root), {"path": "../proof.txt", "sha256": digest}) is None
    assert confirmed_file_write(str(root), {"path": str(tmp_path / "proof.txt"), "sha256": digest}) is None


@pytest.mark.asyncio
async def test_retired_worker_tools_are_not_callable(tmp_path):
    args = SimpleNamespace(workspace=tmp_path)
    for name in ("file_read", "file_write", "project_command", "research_sources"):
        with pytest.raises(ValueError, match="not allowlisted"):
            await _call(name, {}, args)


@pytest.mark.asyncio
async def test_broker_history_is_cursor_based_and_fenced_to_current_claim() -> None:
    created = await task_service.dispatch(
        goal="Remember original evidence", origin_surface="test",
        idempotency_key="m1-broker-history",
    )
    worker = await worker_coordinator.activate(process_identity="m1-broker-history-worker")
    assert worker is not None
    claim = await worker_coordinator.claim_next(worker.worker_id)
    assert claim.task is not None
    args = SimpleNamespace(
        task_id=created.task.id,
        run_id=claim.task.execution_run_id,
        attempt_id=claim.task.attempt_id,
        claim_token=claim.task.claim_token,
        workspace=Path(claim.task.workspace_path),
    )
    event = await task_service.record_event(
        created.task.id, event_type="agent.evidence", payload={"fact": "durable"},
        idempotency_key="m1-broker-history-event",
        attempt_id=args.attempt_id, execution_run_id=args.run_id, claim_token=args.claim_token,
    )
    history = await _call("task_history", {"after_sequence": event.sequence - 1}, args)
    assert history["events"][0]["payload"] == {"fact": "durable"}
    assert history["next_cursor"] == event.sequence
    assert (await _call("task_history", {"after_sequence": event.sequence}, args))["events"] == []
    chunk = await _call("task_history", {"event_sequence": event.sequence}, args)
    assert chunk["payload_json_chunk"] == '{"fact":"durable"}'
    assert chunk["next_byte_offset"] == chunk["total_bytes"]
    large = await task_service.record_event(
        created.task.id, event_type="agent.large_evidence", payload={"text": "x" * 150_000},
        idempotency_key="m1-broker-large-history-event",
        attempt_id=args.attempt_id, execution_run_id=args.run_id, claim_token=args.claim_token,
    )
    listing = await _call("task_history", {"after_sequence": event.sequence}, args)
    assert listing["events"][0]["payload_oversize"] is True
    pieces = []
    offset = 0
    while True:
        part = await _call(
            "task_history", {"event_sequence": large.sequence, "byte_offset": offset}, args
        )
        pieces.append(part["payload_json_chunk"])
        offset = part["next_byte_offset"]
        if offset == part["total_bytes"]:
            break
    assert '"text":"' + "x" * 150_000 + '"' in "".join(pieces)
    with pytest.raises(ValueError, match="nonnegative"):
        await _call("task_history", {"after_sequence": -1}, args)
    args.claim_token = "stale"
    with pytest.raises((ValueError, RuntimeError)):
        await _call("task_history", {}, args)


@pytest.mark.asyncio
async def test_portable_context_indexes_prior_run_without_replaying_uncertain_actions() -> None:
    created = await task_service.dispatch(
        goal="Initial request", origin_surface="test", idempotency_key="m1-portable-context"
    )
    await task_service.complete(
        created.task.id, summary="First result", final_text="Original durable result",
        idempotency_key="m1-portable-first-done"
    )
    await task_service.continue_task(
        created.task.id, content="Follow up", origin="test",
        idempotency_key="m1-portable-followup",
    )
    worker = await worker_coordinator.activate(process_identity="m1-portable-worker")
    assert worker is not None
    claim = await worker_coordinator.claim_next(worker.worker_id)
    assert claim.task is not None
    handoff = await portable_context(
        created.task.id, run_id=claim.task.execution_run_id,
        attempt_id=claim.task.attempt_id, claim_token=claim.task.claim_token,
    )
    assert "Initial request" in handoff
    assert "First result" in handoff
    assert json.loads(handoff)["current_request"] == "Follow up"
    assert json.loads(handoff)["recent_artifacts"][0]["type"] == "result"
    assert '"current_run_ordinal":2' in handoff
    assert "task_history" in handoff
    pinned = await task_service.get_task(created.task.id)
    assert pinned is not None
    assert pinned.runs[-1].context_reference is not None
    await task_service.record_event(
        created.task.id, event_type="agent.later_evidence", payload={"later": True},
        idempotency_key="m1-portable-later-event",
        attempt_id=claim.task.attempt_id, execution_run_id=claim.task.execution_run_id,
        claim_token=claim.task.claim_token,
    )
    assert await portable_context(
        created.task.id, run_id=claim.task.execution_run_id,
        attempt_id=claim.task.attempt_id, claim_token=claim.task.claim_token,
    ) == handoff
    async with session_scope() as session:
        bundles = list((await session.execute(
            select(TaskContextBundle).where(TaskContextBundle.execution_run_id == claim.task.execution_run_id)
        )).scalars().all())
        assert len(bundles) == 1
        assert bundles[0].content_json == json.loads(handoff)
        bundles[0].content_json = {"schema": "task-context.v1", "tampered": True}
    with pytest.raises(ValueError, match="digest"):
        await portable_context(
            created.task.id, run_id=claim.task.execution_run_id,
            attempt_id=claim.task.attempt_id, claim_token=claim.task.claim_token,
        )


@pytest.mark.asyncio
async def test_started_broker_action_becomes_uncertain_and_is_not_replayed_after_crash() -> None:
    created = await task_service.dispatch(
        goal="Do not duplicate an uncertain action",
        origin_surface="test",
        idempotency_key="m1-broker-uncertain",
    )
    first_identity = "m1-broker-crash-worker"
    worker = await worker_coordinator.activate(process_identity=first_identity)
    assert worker is not None
    claim = await worker_coordinator.claim_next(worker.worker_id)
    assert claim.task is not None
    await task_service.start_operation(
        created.task.id,
        operation_key=f"{claim.task.execution_run_id}:uncertain-write",
        operation_type="broker.file_write",
        input_digest="known-digest",
        attempt_id=claim.task.attempt_id,
        execution_run_id=claim.task.execution_run_id,
        claim_token=claim.task.claim_token,
    )
    await worker_coordinator.worker_exited(
        worker.worker_id,
        process_identity=first_identity,
        exit_code=9,
    )
    interrupted = await task_service.get_task(created.task.id)
    assert interrupted is not None and interrupted.operations[0].state == "uncertain"
    await task_service.control(
        created.task.id,
        action="retry",
        idempotency_key="m1-broker-manual-retry",
    )
    replacement = await worker_coordinator.activate(process_identity="m1-broker-replacement")
    assert replacement is not None
    next_claim = await worker_coordinator.claim_next(replacement.worker_id)
    assert next_claim.task is not None
    with pytest.raises(ValueError, match="uncertain or failed"):
        await task_service.start_operation(
            created.task.id,
            operation_key=f"{next_claim.task.execution_run_id}:uncertain-write",
            operation_type="broker.file_write",
            input_digest="known-digest",
            attempt_id=next_claim.task.attempt_id,
            execution_run_id=next_claim.task.execution_run_id,
            claim_token=next_claim.task.claim_token,
        )
    with pytest.raises(ValueError, match="unresolved uncertain"):
        await task_service.start_operation(
            created.task.id,
            operation_key=f"{next_claim.task.execution_run_id}:different-key",
            operation_type="broker.file_write",
            input_digest="different-digest",
            attempt_id=next_claim.task.attempt_id,
            execution_run_id=next_claim.task.execution_run_id,
            claim_token=next_claim.task.claim_token,
        )


@pytest.mark.asyncio
async def test_file_write_after_replace_is_confirmed_only_after_writer_exit() -> None:
    created = await task_service.dispatch(
        goal="Recover committed file write", origin_surface="test",
        idempotency_key="m1-broker-post-exit-proof",
    )
    worker = await worker_coordinator.activate(process_identity="m1-file-write-exit-worker")
    assert worker is not None
    claim = await worker_coordinator.claim_next(worker.worker_id)
    assert claim.task is not None
    handoff = await portable_context(
        created.task.id, run_id=claim.task.execution_run_id,
        attempt_id=claim.task.attempt_id, claim_token=claim.task.claim_token,
    )
    content = "COMMITTED"
    operation = await task_service.start_operation(
        created.task.id, operation_key=f"{claim.task.execution_run_id}:proof",
        operation_type="broker.file_write", input_digest="proof-digest",
        intent={"path": "proof.txt", "sha256": sha256(content.encode()).hexdigest()},
        attempt_id=claim.task.attempt_id, execution_run_id=claim.task.execution_run_id,
        claim_token=claim.task.claim_token,
    )
    path = Path(claim.task.workspace_path) / "proof.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    before_exit = await task_service.get_task(created.task.id)
    assert before_exit is not None and before_exit.operations[0].state == "started"
    await worker_coordinator.worker_exited(
        worker.worker_id, process_identity="m1-file-write-exit-worker", exit_code=9,
    )
    after_exit = await task_service.get_task(created.task.id)
    assert after_exit is not None
    assert after_exit.operations[0].id == operation.id
    assert after_exit.operations[0].state == "succeeded"
    assert after_exit.operations[0].result_json == {
        "path": "proof.txt", "sha256": sha256(content.encode()).hexdigest(),
    }
    assert any(event.event_type == "task.operation.succeeded" for event in after_exit.events)
    await task_service.control(
        created.task.id, action="retry", idempotency_key="m1-file-write-exit-retry",
    )
    replacement = await worker_coordinator.activate(process_identity="m1-file-write-exit-replacement")
    assert replacement is not None
    next_claim = await worker_coordinator.claim_next(replacement.worker_id)
    assert next_claim.task is not None
    delta = json.loads(await current_run_delta(
        created.task.id, run_id=next_claim.task.execution_run_id,
        attempt_id=next_claim.task.attempt_id, claim_token=next_claim.task.claim_token,
        after_sequence=json.loads(handoff)["latest_event_sequence"],
    ))
    assert delta["operation_count"] == 1
    assert delta["uncertain_operation_count"] == 0
    assert delta["recent_operations"][0]["state"] == "succeeded"
    assert delta["event_count"] >= 1
