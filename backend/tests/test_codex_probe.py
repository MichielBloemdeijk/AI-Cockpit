from __future__ import annotations
from app.config import settings

from app.jarvis_tasks.codex_probe import (
    AssertionResult,
    _base_config_overrides,
    _overall_status,
    classify_provider_error,
)
from app.jarvis_tasks.mcp_fixture import _handle_request


def _result(status: str, *, gating: bool = True) -> AssertionResult:
    return AssertionResult(
        id=f"test.{status}",
        status=status,  # type: ignore[arg-type]
        summary="test",
        evidence={},
        elapsed_ms=0,
        gating=gating,
    )


def test_provider_errors_distinguish_billing_rate_limit_and_auth() -> None:
    assert classify_provider_error("insufficient_quota", "") == "billing_exhausted"
    assert classify_provider_error(None, "credit balance exhausted") == "billing_exhausted"
    assert classify_provider_error("429", "too many requests") == "rate_limited"
    assert classify_provider_error("401", "unauthorized") == "authentication"
    assert classify_provider_error(None, "upstream unavailable") == "provider_error"


def test_probe_requires_live_evidence_for_a_pass() -> None:
    assert _overall_status([_result("pass")], live=False) == "incomplete"
    assert _overall_status([_result("pass"), _result("blocked")], live=True) == "incomplete"
    assert _overall_status([_result("pass")], live=True) == "passed"
    assert _overall_status([_result("pass"), _result("fail")], live=True) == "failed"
    assert (
        _overall_status(
            [_result("pass"), _result("blocked", gating=False)], live=True
        )
        == "passed"
    )


def test_windows_probe_selects_a_native_sandbox() -> None:
    overrides = _base_config_overrides()
    if overrides:
        assert overrides == (f'windows.sandbox="{settings.task_windows_sandbox}"',)


def test_mcp_fixture_commits_before_returning_success(tmp_path) -> None:
    journal = tmp_path / "operations.jsonl"
    response = _handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "journal_commit",
                "arguments": {"operation_id": "op-1", "value": "done"},
            },
        },
        journal=journal,
        allowed_operation_id="op-1",
    )

    assert response is not None
    assert response["result"]["isError"] is False
    assert '"state":"succeeded"' in journal.read_text(encoding="utf-8")


def test_mcp_fixture_denies_undeclared_or_unauthorized_calls(tmp_path) -> None:
    journal = tmp_path / "operations.jsonl"
    for name, operation_id in (("not_declared", "op-1"), ("journal_commit", "wrong")):
        response = _handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": {"operation_id": operation_id, "value": "done"},
                },
            },
            journal=journal,
            allowed_operation_id="op-1",
        )
        assert response is not None
        assert response["result"]["isError"] is True
    assert not journal.exists()
