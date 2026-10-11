from types import SimpleNamespace
import pytest

from app.services.native_worker import approved_skill_config, start_turn, verify_native_workload
from app.services.task_service import task_service
from test_worker_tool_policy import claimed


def test_only_approved_skills_and_no_implicit_dependency_installation(tmp_path):
    good, bad = tmp_path / "good" / "SKILL.md", tmp_path / "bad" / "SKILL.md"
    calls = []
    class Client:
        def _request_raw(self, method, params):
            calls.append((method, params))
            if method == "skills/list":
                return {"data": [{"skills": [{"path": str(good)}, {"path": str(bad)}]}]}
            return {}
    config = approved_skill_config(SimpleNamespace(_client=Client()), tmp_path,
        {"tools": ["native.skills"], "approved_skills": [str(good)]})
    assert any(row["path"] == str(good) and row["enabled"] for row in config["skills"]["config"])
    assert any(row["path"] == str(bad) and not row["enabled"] for row in config["skills"]["config"])
    disabled = approved_skill_config(SimpleNamespace(_client=Client()), tmp_path,
        {"tools": [], "approved_skills": [str(good)]})
    assert not any(row["enabled"] for row in disabled["skills"]["config"])


def test_turn_preserves_exact_writable_roots_and_never_approval(tmp_path):
    captured = {}
    class Client:
        def _start_turn(self, thread_id, inputs, params, for_handle):
            captured.update(params)
            assert for_handle
            return SimpleNamespace(turn=SimpleNamespace(id="turn")), object()
    policy = {"type": "workspaceWrite", "writableRoots": [str(tmp_path)],
        "networkAccess": False, "excludeTmpdirEnvVar": True, "excludeSlashTmp": True}
    handle = start_turn(SimpleNamespace(id="thread", _client=Client()), "test", effort="low", policy=policy)
    assert handle.id == "turn" and captured["sandboxPolicy"] == policy
    assert captured["approvalPolicy"] == "never"


def test_workload_failure_does_not_start_unrestricted_command(tmp_path, monkeypatch):
    import app.jarvis_tasks.codex_probe as probe
    policies = []
    def fail(client, params):
        policies.append(params["sandboxPolicy"])
        raise RuntimeError("permission denied")
    monkeypatch.setattr(probe, "_command_exec", fail)
    with pytest.raises(RuntimeError, match="No unrestricted retry"):
        verify_native_workload(None, tmp_path)
    assert len(policies) == 1 and policies[0]["type"] == "workspaceWrite"


def test_native_observations_do_not_embed_history_or_image_receipts():
    from app.services.native_worker import observation_payload
    raw = {"item": {"type": "mcpToolCall", "tool": "task_context_images", "result": {
        "content": [{"type": "image", "data": "private-base64-image"}]}}}
    clean = observation_payload(raw)
    assert "private-base64-image" not in str(clean)
    assert clean["item"]["result"]["sha256"]
    assert raw["item"]["result"]["content"]


def test_tool_receipt_evidence_is_not_mistaken_for_a_new_native_action():
    from app.services.codex_task_runtime import _walk_items
    search = {"type": "webSearch", "id": "earlier-search"}
    receipt = {"type": "mcpToolCall", "id": "history", "result": {"native_research": [search]}}
    assert _walk_items({"item": receipt}) == [receipt]
    assert _walk_items({"turn": {"items": [receipt, search]}}) == [receipt, search]


@pytest.mark.asyncio
async def test_subagent_history_recurses_and_only_interrupts_at_parent_stop():
    from app.services.native_worker import subagent_observations
    interrupted = []
    class Client:
        def thread_read(self, child, *, include_turns):
            if child == "child":
                return {"thread": {"turns": [{"id": "live", "status": "inProgress", "items": [
                    {"id": "spawn", "type": "collabAgentToolCall", "receiverThreadIds": ["grandchild"]}]}]}}
            return {"thread": {"turns": [{"id": "done", "status": "completed", "items": [
                {"id": "search", "type": "webSearch"}]}]}}
        def turn_interrupt(self, child, turn):
            interrupted.append((child, turn))
    client = SimpleNamespace(_client=Client())
    found = await subagent_observations(client, "parent", {"child"})
    assert len(found) == 1 and found[0]["thread_id"] == "grandchild" and not interrupted
    await subagent_observations(client, "parent", {"child"}, stop_active=True)
    assert interrupted == [("child", "live")]


@pytest.mark.asyncio
async def test_trailing_native_observation_survives_publication_without_writer_authority():
    context, _ = await claimed("native-observation")
    fence = dict(attempt_id=context.attempt_id, execution_run_id=context.execution_run_id, claim_token=context.claim_token)
    await task_service.complete(context.task_id, summary="done", idempotency_key="done", **fence)
    await task_service.record_native_observation(context.task_id, event_type="agent.native.subagent.item",
        payload={"thread_id": "child", "item": {"id": "child-result", "type": "agentMessage", "text": "done"}},
        idempotency_key="child-result", **fence)
    with pytest.raises(ValueError):
        await task_service.record_event(context.task_id, event_type="mutation", payload={}, idempotency_key="late", **fence)
    with pytest.raises(ValueError, match="Only native"):
        await task_service.record_native_observation(context.task_id, event_type="task.completed", payload={}, idempotency_key="wrong", **fence)
    with pytest.raises(ValueError, match="authority"):
        await task_service.record_native_observation(context.task_id, event_type="agent.native.item", payload={}, idempotency_key="forged", **(fence | {"claim_token": "forged"}))
