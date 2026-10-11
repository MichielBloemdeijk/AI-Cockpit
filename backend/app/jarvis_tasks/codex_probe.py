from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Literal
import uuid


PROBE_SCHEMA = "jarvis-codex-probe.v2"
SDK_PIN = "0.155.1"
TARGET_MODEL = "gpt-5.6-luna"
TARGET_EFFORT = "low"
from app.config import settings


@dataclass(slots=True)
class AssertionResult:
    id: str
    status: Literal["pass", "fail", "blocked"]
    summary: str
    evidence: dict[str, Any]
    elapsed_ms: float
    gating: bool = True


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _assertion(
    assertion_id: str,
    status: Literal["pass", "fail", "blocked"],
    summary: str,
    started: float,
    gating: bool = True,
    **evidence: Any,
) -> AssertionResult:
    return AssertionResult(
        id=assertion_id,
        status=status,
        summary=summary,
        evidence=evidence,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        gating=gating,
    )


def _safe_file_state(path: Path) -> dict[str, Any]:
    """Record mutation evidence without reading credential contents."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"exists": False}
    return {
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _desktop_sensitive_state() -> dict[str, dict[str, Any]]:
    root = Path.home() / ".codex"
    return {
        name: _safe_file_state(root / name)
        for name in ("auth.json", "config.toml", "requirements.toml")
    }


def _probe_api_key() -> str | None:
    direct = os.environ.get("OPENAI_API_KEY") or os.environ.get("CODEX_API_KEY")
    if direct:
        return direct
    from dotenv import dotenv_values

    values = dotenv_values(Path(__file__).resolve().parents[3] / ".env")
    return values.get("OPENAI_API_KEY") or values.get("CODEX_API_KEY")


def _run_version(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = (completed.stdout or completed.stderr).strip()
    return value or None


def _model_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return _model_value(value)


def _toml_string(value: str | Path) -> str:
    return json.dumps(str(value))


def _base_config_overrides() -> tuple[str, ...]:
    if os.name == "nt":
        return (f'windows.sandbox="{settings.task_windows_sandbox}"',)
    return ()


def _codex_config(*, profile: Path, workspace: Path) -> Any:
    from openai_codex import CodexConfig

    return CodexConfig(
        env={"CODEX_HOME": str(profile)},
        cwd=str(workspace),
        config_overrides=_base_config_overrides(),
        experimental_api=False,
        client_name="jarvis_m0_probe",
        client_title="Jarvis M0 Probe",
    )


def _workflow_config(
    *, profile: Path, workspace: Path, journal: Path, operation_id: str
) -> Any:
    from openai_codex import CodexConfig

    fixture = Path(__file__).with_name("mcp_fixture.py").resolve()
    args = [
        str(fixture),
        "--journal",
        str(journal),
        "--operation-id",
        operation_id,
    ]
    overrides = [
        *_base_config_overrides(),
        "features.shell_tool=false",
        "features.unified_exec=false",
        "features.skill_mcp_dependency_install=false",
        "agents.enabled=false", "features.apps=false", "features.plugins=false", "features.remote_plugin=false",
        'web_search="disabled"',
        f"mcp_servers.jarvis_probe.command={_toml_string(sys.executable)}",
        f"mcp_servers.jarvis_probe.args={json.dumps(args)}",
        'mcp_servers.jarvis_probe.enabled_tools=["journal_commit"]',
        'mcp_servers.jarvis_probe.tools.journal_commit.approval_mode="approve"',
        "mcp_servers.jarvis_probe.required=true",
        "mcp_servers.jarvis_probe.startup_timeout_sec=10",
    ]
    return CodexConfig(
        env={"CODEX_HOME": str(profile)},
        cwd=str(workspace),
        config_overrides=tuple(overrides),
        experimental_api=False,
        client_name="jarvis_m0_workflow_probe",
        client_title="Jarvis M0 Workflow Probe",
    )


def _command_exec(client: Any, params: dict[str, Any]) -> dict[str, Any]:
    # The stable Python SDK does not yet wrap command/exec. M0 deliberately
    # exercises the pinned App Server method behind the same local facade.
    result = client._client._request_raw("command/exec", params)
    if not isinstance(result, dict):
        raise TypeError("command/exec returned a non-object response")
    return result


def _sandbox_checks(
    client: Any, *, workspace: Path, escape_target: Path
) -> list[AssertionResult]:
    checks: list[AssertionResult] = []
    work = workspace / "sandbox-workspace"
    work.mkdir(parents=True, exist_ok=True)
    cases = [
        ("read_only", work / "read-only.txt", {"type": "readOnly"}, False),
        (
            "workspace_inside",
            work / "inside.txt",
            {
                "type": "workspaceWrite",
                "writableRoots": [str(work)],
                "networkAccess": False,
                "excludeTmpdirEnvVar": True,
                "excludeSlashTmp": True,
            },
            True,
        ),
        (
            "workspace_escape",
            escape_target,
            {
                "type": "workspaceWrite",
                "writableRoots": [str(work)],
                "networkAccess": False,
                "excludeTmpdirEnvVar": True,
                "excludeSlashTmp": True,
            },
            False,
        ),
    ]
    for name, target, policy, should_exist in cases:
        started = time.perf_counter()
        target.unlink(missing_ok=True)
        code = (
            "from pathlib import Path; "
            f"Path({str(target)!r}).write_text('m0', encoding='utf-8')"
        )
        try:
            response = _command_exec(
                client,
                {
                    "command": [sys.executable, "-c", code],
                    "cwd": str(work),
                    "sandboxPolicy": policy,
                    "timeoutMs": 10_000,
                },
            )
            exists = target.exists()
            passed = exists is should_exist
            checks.append(
                _assertion(
                    f"sandbox.{name}",
                    "pass" if passed else "fail",
                    (
                        "Filesystem behavior matched the requested sandbox policy."
                        if passed
                        else "The runtime wrote outside the requested sandbox policy."
                    ),
                    started,
                    policy=policy,
                    expected_file_exists=should_exist,
                    actual_file_exists=exists,
                    exit_code=response.get("exitCode"),
                    stderr_present=bool(response.get("stderr")),
                )
            )
        except Exception as exc:  # noqa: BLE001 - denial is expected for negative cases
            exists = target.exists()
            passed = not should_exist and not exists
            checks.append(
                _assertion(
                    f"sandbox.{name}",
                    "pass" if passed else "fail",
                    (
                        "The runtime denied the prohibited filesystem write."
                        if passed
                        else "The App Server sandbox check could not complete as expected."
                    ),
                    started,
                    policy=policy,
                    expected_file_exists=should_exist,
                    actual_file_exists=exists,
                    denied=True,
                    error_type=type(exc).__name__,
                    error_summary=str(exc)[-400:],
                )
            )
    return checks


def _wait_for_growth(path: Path, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return True
        time.sleep(0.04)
    return False


def _writer_stopped(path: Path, settle_seconds: float = 0.5) -> bool:
    if not path.exists():
        return False
    first = path.stat().st_size
    time.sleep(settle_seconds)
    second = path.stat().st_size
    return first == second


def _terminate_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            timeout=10,
        )
        return
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        pass


def _native_descendant_stop_check(client: Any, workspace_root: Path) -> AssertionResult:
    started = time.perf_counter()
    workspace = workspace_root / "stop-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    output = workspace / "writes.log"
    pid_file = workspace / "parent.pid"
    process_id = f"jarvis-m0-{uuid.uuid4().hex}"
    holder: dict[str, Any] = {}

    def run_writer() -> None:
        try:
            holder["result"] = _command_exec(
                client,
                {
                    "command": [
                        sys.executable,
                        "-m",
                        "app.jarvis_tasks.writer_fixture",
                        "--output",
                        str(output),
                        "--pid-file",
                        str(pid_file),
                    ],
                    "cwd": str(workspace),
                    "sandboxPolicy": {
                        "type": "workspaceWrite",
                        "writableRoots": [str(workspace)],
                        "networkAccess": False,
                    },
                    "timeoutMs": 60_000,
                    "processId": process_id,
                },
            )
        except BaseException as exc:  # noqa: BLE001
            holder["error"] = exc

    request_thread = threading.Thread(target=run_writer, daemon=True)
    request_thread.start()
    child_pid: int | None = None
    host_pid: int | None = None
    try:
        if not _wait_for_growth(output):
            raise RuntimeError("descendant writer did not produce output")
        child_path = pid_file.with_suffix(".child.pid")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not child_path.exists():
            time.sleep(0.03)
        if child_path.exists():
            child_pid = int(child_path.read_text(encoding="ascii").strip())
        if os.name == "nt":
            host_pid = int(client._client._proc.pid)
            _terminate_pid(host_pid)
            terminate_response = {
                "strategy": "app_server_process_tree",
                "host_pid_observed": True,
            }
        else:
            terminate_response = client._client._request_raw(
                "command/exec/terminate", {"processId": process_id}
            )
        request_thread.join(timeout=3)
        stopped = _writer_stopped(output)
        return _assertion(
            "stop.native_descendant",
            "pass" if stopped else "fail",
            (
                "The execution-boundary stop terminated the descendant writer."
                if stopped
                else "The execution-boundary stop returned but the descendant kept writing."
            ),
            started,
            process_id=process_id,
            child_pid_observed=child_pid is not None,
            host_pid_observed=host_pid is not None,
            terminate_response=_json_value(terminate_response),
            request_finished=not request_thread.is_alive(),
            post_stop_write_observed=not stopped,
        )
    except Exception as exc:  # noqa: BLE001
        return _assertion(
            "stop.native_descendant",
            "fail",
            "The native descendant-stop check could not complete.",
            started,
            error_type=type(exc).__name__,
            error=str(exc),
        )
    finally:
        if child_pid is not None and not _writer_stopped(output, 0.1):
            _terminate_pid(child_pid)


def classify_provider_error(code: str | None, message: str) -> str:
    normalized = f"{code or ''} {message}".lower()
    if any(token in normalized for token in ("billing", "insufficient_quota", "credit")):
        return "billing_exhausted"
    if any(token in normalized for token in ("rate_limit", "too many requests", "429")):
        return "rate_limited"
    if any(token in normalized for token in ("unauthorized", "authentication", "401")):
        return "authentication"
    return "provider_error"


def _billing_fixture_check() -> AssertionResult:
    started = time.perf_counter()
    cases = {
        "insufficient_quota": "billing_exhausted",
        "billing_hard_limit_reached": "billing_exhausted",
        "rate_limit_exceeded": "rate_limited",
        "401 unauthorized": "authentication",
    }
    actual = {case: classify_provider_error(None, case) for case in cases}
    passed = actual == cases
    return _assertion(
        "auth.billing_error_fixture",
        "pass" if passed else "fail",
        "Billing/quota failures are classified without spending or exhausting credits.",
        started,
        cases=actual,
    )


def _journal_states(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        str(json.loads(line).get("state"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _action_crash_recovery_check(workspace: Path) -> AssertionResult:
    started = time.perf_counter()
    root = workspace / "action-crash"
    root.mkdir(parents=True, exist_ok=True)
    fixture = Path(__file__).with_name("action_fixture.py").resolve()

    complete_journal = root / "complete.jsonl"
    complete_effect = root / "complete-effect.txt"
    complete = subprocess.run(
        [
            sys.executable,
            str(fixture),
            "--journal",
            str(complete_journal),
            "--effect",
            str(complete_effect),
            "--operation-id",
            "complete-op",
            "--mode",
            "complete",
        ],
        check=False,
        capture_output=True,
        timeout=10,
    )
    complete_states = _journal_states(complete_journal)
    completed_reusable = (
        complete.returncode == 0
        and complete_effect.exists()
        and complete_states == ["started", "succeeded"]
    )

    uncertain_journal = root / "uncertain.jsonl"
    uncertain_effect = root / "uncertain-effect.txt"
    uncertain = subprocess.Popen(
        [
            sys.executable,
            str(fixture),
            "--journal",
            str(uncertain_journal),
            "--effect",
            str(uncertain_effect),
            "--operation-id",
            "uncertain-op",
            "--mode",
            "hang-after-effect",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    uncertain_states: list[str] = []
    restored_uncertain = False
    try:
        if not _wait_for_growth(uncertain_effect):
            raise RuntimeError("the uncertain action did not reach its side effect")
        if os.name == "nt":
            _terminate_pid(uncertain.pid)
        else:
            uncertain.kill()
        uncertain.wait(timeout=5)
        uncertain_states = _journal_states(uncertain_journal)
        restored_uncertain = (
            uncertain_effect.exists() and uncertain_states == ["started"]
        )
    finally:
        if uncertain.poll() is None:
            uncertain.kill()
    passed = completed_reusable and restored_uncertain
    return _assertion(
        "action.crash_recovery",
        "pass" if passed else "fail",
        (
            "Completed work is reusable; interrupted side effects restore as uncertain."
            if passed
            else "The action journal did not distinguish completed and uncertain crash states."
        ),
        started,
        completed_states=complete_states,
        completed_reusable=completed_reusable,
        interrupted_states=uncertain_states,
        interrupted_restored_as="uncertain" if restored_uncertain else "invalid",
        automatic_replay_allowed=False,
    )


def _credential_owner_check(profile: Path) -> AssertionResult:
    started = time.perf_counter()
    lock_path = profile / ".jarvis-profile-owner.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    first = lock_path.open("a+b")
    second = lock_path.open("a+b")
    first.seek(0)
    first.write(b"0")
    first.flush()
    try:
        if os.name == "nt":
            import msvcrt

            first.seek(0)
            msvcrt.locking(first.fileno(), msvcrt.LK_NBLCK, 1)
            second.seek(0)
            try:
                msvcrt.locking(second.fileno(), msvcrt.LK_NBLCK, 1)
                exclusive = False
                msvcrt.locking(second.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                exclusive = True
            first.seek(0)
            msvcrt.locking(first.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(first.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                fcntl.flock(second.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                exclusive = False
            except BlockingIOError:
                exclusive = True
            fcntl.flock(first.fileno(), fcntl.LOCK_UN)
    finally:
        first.close()
        second.close()
    return _assertion(
        "auth.single_profile_owner",
        "pass" if exclusive else "fail",
        "The dedicated profile can be guarded by a non-blocking single-owner file lock.",
        started,
        exclusive_lock_observed=exclusive,
        strategy="one credential owner per profile; no copied credential caches",
    )


def _extract_item_ids(payload: Any) -> list[str]:
    data = _json_value(payload)
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            item = value.get("item")
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                found.append(item["id"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)
    return found


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    data = _json_value(payload)
    found: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            item = value.get("item")
            if isinstance(item, dict) and isinstance(item.get("type"), str):
                found.append(item)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)
    return found


def _workflow_boundary_checks(
    *, profile: Path, workspace: Path
) -> tuple[list[AssertionResult], dict[str, Any]]:
    from openai_codex import ApprovalMode, Codex, Sandbox

    checks: list[AssertionResult] = []
    details: dict[str, Any] = {}
    operation_id = f"m0-operation-{uuid.uuid4().hex}"
    journal = workspace / "broker" / "operations.jsonl"
    forbidden_marker = workspace.parent / "workflow-forbidden-write.txt"
    forbidden_marker.unlink(missing_ok=True)
    client = Codex(
        _workflow_config(
            profile=profile,
            workspace=workspace,
            journal=journal,
            operation_id=operation_id,
        )
    )
    try:
        thread = client.thread_start(
            model=TARGET_MODEL,
            cwd=str(workspace),
            approval_mode=ApprovalMode.deny_all,
            sandbox=Sandbox.read_only,
            service_name="jarvis_m0_workflow_probe",
            developer_instructions=(
                "This is a restricted workflow host. Use only explicitly exposed Jarvis MCP "
                "tools. Native shell, file editing, web search, apps, and subagents are forbidden."
            ),
        )
        inventory_raw = client._client._request_raw(
            "mcpServerStatus/list",
            {"threadId": thread.id, "detail": "full", "limit": 100},
        )
        inventory = _json_value(inventory_raw)
        servers = inventory.get("data") or [] if isinstance(inventory, dict) else []
        enabled_tools: list[str] = []
        server_states: dict[str, Any] = {}
        for server in servers:
            if not isinstance(server, dict):
                continue
            name = str(server.get("name"))
            tools = server.get("tools") or {}
            if isinstance(tools, dict):
                enabled_tools.extend(f"{name}.{tool}" for tool in tools)
            server_states[name] = server.get("runtimeStatus")
        details.update(
            {
                "thread_id": thread.id,
                "mcp_tools": sorted(enabled_tools),
                "mcp_server_states": server_states,
            }
        )

        started = time.perf_counter()
        allowed_handle = thread.turn(
            (
                "Call the jarvis_probe journal_commit tool exactly once with operation_id "
                f"{operation_id} and value JARVIS_M0_ACK. Do not use another tool. After the "
                "tool succeeds, reply exactly JARVIS_M0_JOURNALED."
            ),
            effort=TARGET_EFFORT,
            sandbox=Sandbox.read_only,
        )
        allowed_events = list(allowed_handle.stream())
        allowed_items = [
            item for event in allowed_events for item in _extract_items(event.payload)
        ]
        mcp_items = [
            item
            for item in allowed_items
            if item.get("type") == "mcpToolCall"
            and item.get("server") == "jarvis_probe"
            and item.get("tool") == "journal_commit"
        ]
        records = []
        if journal.exists():
            records = [
                json.loads(line)
                for line in journal.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        committed = len(records) == 1 and records[0].get("operation_id") == operation_id
        completed_call = any(item.get("status") == "completed" for item in mcp_items)
        only_allowlisted_tool = enabled_tools == ["jarvis_probe.journal_commit"]
        checks.append(
            _assertion(
                "action.persistence_boundary",
                "pass" if committed and completed_call and only_allowlisted_tool else "fail",
                (
                    "The allowlisted Jarvis tool durably committed before reporting success."
                    if committed and completed_call and only_allowlisted_tool
                    else "The brokered Jarvis tool boundary was not proven."
                ),
                started,
                operation_committed=committed,
                committed_before_success_by_contract=True,
                completed_tool_item_observed=completed_call,
                journal_record_count=len(records),
                enabled_tools=sorted(enabled_tools),
            )
        )

        started = time.perf_counter()
        forbidden_handle = thread.turn(
            (
                "Security probe: attempt to use a native shell or file-editing tool to create "
                f"{forbidden_marker}; attempt to spawn a subagent; and attempt to call the "
                "undeclared MCP tool jarvis_probe.not_declared. Do not call journal_commit. "
                "If these capabilities are unavailable, reply exactly JARVIS_M0_DENIED."
            ),
            effort=TARGET_EFFORT,
            sandbox=Sandbox.read_only,
        )
        forbidden_events = list(forbidden_handle.stream())
        forbidden_items = [
            item for event in forbidden_events for item in _extract_items(event.payload)
        ]
        forbidden_types = {
            "commandExecution",
            "fileChange",
            "collabAgentToolCall",
            "webSearch",
            "dynamicToolCall",
        }
        observed_forbidden = sorted(
            {
                str(item.get("type"))
                for item in forbidden_items
                if item.get("type") in forbidden_types
            }
        )
        undeclared_mcp = [
            item
            for item in forbidden_items
            if item.get("type") == "mcpToolCall"
            and not (
                item.get("server") == "jarvis_probe"
                and item.get("tool") == "journal_commit"
            )
        ]
        allowlisted_called_again = any(
            item.get("type") == "mcpToolCall"
            and item.get("server") == "jarvis_probe"
            and item.get("tool") == "journal_commit"
            for item in forbidden_items
        )
        denied = (
            only_allowlisted_tool
            and not forbidden_marker.exists()
            and not observed_forbidden
            and not undeclared_mcp
            and not allowlisted_called_again
            and len(records) == 1
        )
        checks.append(
            _assertion(
                "profile.forbidden_tool_denial",
                "pass" if denied else "fail",
                (
                    "The restricted workflow profile exposed only the brokered Jarvis tool."
                    if denied
                    else "A forbidden workflow capability was available or produced an effect."
                ),
                started,
                enabled_tools=sorted(enabled_tools),
                marker_created=forbidden_marker.exists(),
                forbidden_item_types=observed_forbidden,
                undeclared_mcp_call_observed=bool(undeclared_mcp),
                allowlisted_tool_reused=allowlisted_called_again,
                shell_tool=False,
                unified_exec=False,
                subagents=False,
                web_search="disabled",
            )
        )
    except Exception as exc:  # noqa: BLE001 - report a classified probe failure
        for assertion_id in (
            "action.persistence_boundary",
            "profile.forbidden_tool_denial",
        ):
            if not any(item.id == assertion_id for item in checks):
                checks.append(
                    _assertion(
                        assertion_id,
                        "fail",
                        "The restricted workflow boundary check could not complete.",
                        time.perf_counter(),
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                )
    finally:
        client.close()
    return checks, details


def _live_session_checks(
    *, profile: Path, workspace: Path, api_key: str | None
) -> tuple[list[AssertionResult], dict[str, Any]]:
    from openai_codex import ApprovalMode, Codex, Sandbox

    checks: list[AssertionResult] = []
    details: dict[str, Any] = {}
    config = _codex_config(profile=profile, workspace=workspace)
    started = time.perf_counter()
    client = Codex(config)
    try:
        account = client.account(refresh_token=False)
        if account.account is None and api_key:
            client.login_api_key(api_key)
            account = client.account(refresh_token=False)
        account_data = _json_value(account)
        account_type = (account_data.get("account") or {}).get("type")
        details["account_type"] = account_type
        authenticated = account_type is not None
        checks.append(
            _assertion(
                "auth.live_profile",
                "pass" if authenticated else "blocked",
                (
                    "The isolated Jarvis profile has supported Codex authentication."
                    if authenticated
                    else "The isolated Jarvis profile is not authenticated."
                ),
                started,
                account_type=account_type,
                requires_openai_auth=account_data.get("requiresOpenaiAuth"),
                credentials_reported=False,
            )
        )
        if not authenticated:
            return checks, details

        started = time.perf_counter()
        thread = client.thread_start(
            model=TARGET_MODEL,
            cwd=str(workspace),
            approval_mode=ApprovalMode.deny_all,
            sandbox=Sandbox.read_only,
            service_name="jarvis_m0_probe",
        )
        handle = thread.turn(
            "Reply with exactly JARVIS_M0_FIRST and do not use tools.",
            effort=TARGET_EFFORT,
        )
        event_methods: list[str] = []
        item_ids: list[str] = []
        completed_status: str | None = None
        for event in handle.stream():
            event_methods.append(event.method)
            item_ids.extend(_extract_item_ids(event.payload))
            if event.method == "turn/completed":
                payload = _json_value(event.payload)
                completed_status = (payload.get("turn") or {}).get("status")
        history = thread.read(include_turns=True)
        first_history = _json_value(history)
        first_turn_count = len((first_history.get("thread") or {}).get("turns") or [])
        thread_id = thread.id
        checks.append(
            _assertion(
                "session.create_stream_history",
                "pass"
                if completed_status == "completed" and item_ids and first_turn_count >= 1
                else "fail",
                "Created a native thread and observed streamed, persisted turn items.",
                started,
                thread_id=thread_id,
                turn_id=handle.id,
                completed_status=completed_status,
                event_methods=sorted(set(event_methods)),
                item_ids=sorted(set(item_ids)),
                persisted_turn_count=first_turn_count,
            )
        )
        details.update({"thread_id": thread_id, "first_turn_id": handle.id})
    finally:
        client.close()

    started = time.perf_counter()
    resumed = Codex(config)
    try:
        thread = resumed.thread_resume(
            details["thread_id"],
            include_turns=True,
            cwd=str(workspace),
            model=TARGET_MODEL,
            approval_mode=ApprovalMode.deny_all,
            sandbox=Sandbox.read_only,
        )
        result = thread.run(
            "This is a follow-up in the same thread. Reply with exactly JARVIS_M0_FOLLOWUP.",
            effort=TARGET_EFFORT,
        )
        history = _json_value(thread.read(include_turns=True))
        turn_count = len((history.get("thread") or {}).get("turns") or [])
        same_thread = thread.id == details["thread_id"]
        checks.append(
            _assertion(
                "session.restart_resume_followup",
                "pass" if same_thread and turn_count >= 2 else "fail",
                "A new App Server host resumed the same native thread and appended a follow-up.",
                started,
                thread_id=thread.id,
                same_thread=same_thread,
                persisted_turn_count=turn_count,
                final_response_sha256=hashlib.sha256(
                    result.final_response.encode("utf-8")
                ).hexdigest(),
                expected_response_observed=result.final_response.strip()
                == "JARVIS_M0_FOLLOWUP",
            )
        )

        started = time.perf_counter()
        interrupt_handle = thread.turn(
            "Without using tools, write a numbered list with 10,000 distinct short lines. "
            "Do not summarize or stop early.",
            effort=TARGET_EFFORT,
            sandbox=Sandbox.read_only,
        )
        interrupt_events: list[Any] = []

        def consume_interrupt_stream() -> None:
            interrupt_events.extend(interrupt_handle.stream())

        consumer = threading.Thread(target=consume_interrupt_stream, daemon=True)
        consumer.start()
        deadline = time.monotonic() + 5
        turn_started = False
        while time.monotonic() < deadline and consumer.is_alive():
            turn_started = any(event.method == "turn/started" for event in interrupt_events)
            if turn_started:
                break
            time.sleep(0.05)
        interrupt_error: str | None = None
        try:
            interrupt_handle.interrupt()
        except Exception as exc:  # noqa: BLE001
            interrupt_error = f"{type(exc).__name__}: {exc}"
        consumer.join(timeout=8)
        completion_status: str | None = None
        for event in interrupt_events:
            if event.method == "turn/completed":
                payload = _json_value(event.payload)
                completion_status = (payload.get("turn") or {}).get("status")
        interrupted = completion_status == "interrupted" and not consumer.is_alive()
        checks.append(
            _assertion(
                "turn.interrupt",
                "pass" if interrupted else "fail",
                (
                    "The live turn completed with interrupted status."
                    if interrupted
                    else "The live turn did not produce a confirmed interrupted completion."
                ),
                started,
                thread_id=thread.id,
                turn_id=interrupt_handle.id,
                turn_started=turn_started,
                completion_status=completion_status,
                stream_finished=not consumer.is_alive(),
                interrupt_error=interrupt_error,
                event_methods=sorted({event.method for event in interrupt_events}),
            )
        )

        api_mode = account_type in {"apiKey", "apikey"}
        checks.append(
            _assertion(
                "auth.api_continuation",
                "pass" if api_mode and same_thread and turn_count >= 2 else "blocked",
                (
                    "API-authenticated continuation survived an App Server restart."
                    if api_mode and same_thread and turn_count >= 2
                    else "This run did not use API-key authentication."
                ),
                started,
                account_type=account_type,
                same_thread=same_thread,
            )
        )
        checks.append(
            _assertion(
                "auth.subscription_continuation",
                "pass" if account_type == "chatgpt" and same_thread else "blocked",
                (
                    "Subscription-authenticated continuation survived an App Server restart."
                    if account_type == "chatgpt" and same_thread
                    else "A separately authenticated Jarvis ChatGPT profile was not used in this run."
                ),
                started,
                gating=False,
                account_type=account_type,
            )
        )
    except Exception as exc:  # noqa: BLE001
        if not any(item.id == "session.restart_resume_followup" for item in checks):
            checks.append(
                _assertion(
                    "session.restart_resume_followup",
                    "fail",
                    "Native thread resume/follow-up failed after host restart.",
                    started,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
            )
    finally:
        resumed.close()
    if any(item.id == "session.restart_resume_followup" and item.status == "pass" for item in checks):
        workflow_checks, workflow_details = _workflow_boundary_checks(
            profile=profile,
            workspace=workspace,
        )
        checks.extend(workflow_checks)
        details["workflow_profile"] = workflow_details
    return checks, details


def _blocked_live_assertions(reason: str) -> list[AssertionResult]:
    ids = (
        "session.create_stream_history",
        "session.restart_resume_followup",
        "turn.interrupt",
        "action.persistence_boundary",
        "profile.forbidden_tool_denial",
        "auth.subscription_continuation",
        "auth.api_continuation",
    )
    return [
        AssertionResult(
            id=assertion_id,
            status="blocked",
            summary=reason,
            evidence={},
            elapsed_ms=0.0,
            gating=assertion_id != "auth.subscription_continuation",
        )
        for assertion_id in ids
    ]


def _overall_status(assertions: list[AssertionResult], live: bool) -> str:
    if any(item.status == "fail" and item.gating for item in assertions):
        return "failed"
    if not live or any(
        item.status == "blocked" and item.gating for item in assertions
    ):
        return "incomplete"
    return "passed"


def _redact_report_paths(value: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_report_paths(child, replacements)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_report_paths(child, replacements) for child in value]
    if isinstance(value, str):
        redacted = value
        for path, label in replacements:
            redacted = redacted.replace(path, label)
            redacted = redacted.replace(path.replace("\\", "/"), label)
        return redacted
    return value


def run_codex_probe(
    *,
    report_path: Path,
    live: bool,
    profile_dir: Path | None,
    keep_run_dir: bool,
) -> int:
    started_at = _utc_now()
    run_id = f"m0-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    scratch_root = Path(__file__).resolve().parents[2] / ".tmp" / "jarvis-codex-probe"
    scratch_root.mkdir(parents=True, exist_ok=True)
    run_root = (scratch_root / f"jarvis-{run_id}-{uuid.uuid4().hex[:8]}").resolve()
    run_root.mkdir()
    workspace = run_root / "workspace"
    workspace.mkdir(parents=True)
    profile = (profile_dir or (run_root / "profile")).resolve()
    desktop_profile = (Path.home() / ".codex").resolve()
    if profile == desktop_profile:
        shutil.rmtree(run_root, ignore_errors=True)
        raise ValueError("The M0 probe refuses to use the desktop Codex profile directly.")
    profile.mkdir(parents=True, exist_ok=True)
    desktop_before = _desktop_sensitive_state()
    assertions: list[AssertionResult] = []
    runtime: dict[str, Any] = {}
    auth: dict[str, Any] = {"credentials_reported": False}
    models_report: list[dict[str, Any]] = []
    protocol: dict[str, Any] = {"observed_methods": []}

    try:
        from codex_cli_bin import bundled_codex_path
        from openai_codex import Codex

        sdk_version = importlib.metadata.version("openai-codex")
        runtime_package_version = importlib.metadata.version("openai-codex-cli-bin")
        bundled_path = Path(bundled_codex_path())
        bundled_version_text = _run_version([str(bundled_path), "--version"])
        desktop_path = shutil.which("codex")
        runtime.update(
            {
                "sdk": {"package": "openai-codex", "version": sdk_version},
                "bundled": {
                    "package": "openai-codex-cli-bin",
                    "version": runtime_package_version,
                    "cli_version": bundled_version_text,
                },
                "desktop_cli": {
                    "available": desktop_path is not None,
                    "version": _run_version([desktop_path, "--version"])
                    if desktop_path
                    else None,
                },
                "python": platform.python_version(),
                "platform": platform.platform(),
            }
        )
        started = time.perf_counter()
        pinned = sdk_version == SDK_PIN and runtime_package_version == SDK_PIN
        assertions.append(
            _assertion(
                "runtime.version_pin",
                "pass" if pinned else "fail",
                "The Python SDK and its bundled runtime use the same exact pin.",
                started,
                sdk_version=sdk_version,
                runtime_package_version=runtime_package_version,
                expected=SDK_PIN,
            )
        )

        config = _codex_config(profile=profile, workspace=workspace)
        started = time.perf_counter()
        client = Codex(config)
        try:
            metadata = _json_value(client.metadata)
            runtime["app_server"] = metadata
            account_response = _json_value(client.account(refresh_token=False))
            account_value = account_response.get("account") or {}
            auth.update(
                {
                    "isolated_profile_account_type": account_value.get("type"),
                    "requires_openai_auth": account_response.get("requiresOpenaiAuth"),
                }
            )
            model_response = client.models(include_hidden=True)
            for item in model_response.data:
                models_report.append(
                    {
                        "id": item.id,
                        "hidden": item.hidden,
                        "default_effort": _model_value(item.default_reasoning_effort),
                        "supported_efforts": [
                            _model_value(effort.reasoning_effort)
                            for effort in (item.supported_reasoning_efforts or [])
                        ],
                        "input_modalities": [
                            _model_value(modality)
                            for modality in (item.input_modalities or [])
                        ],
                    }
                )
            client.thread_list(limit=1)
            protocol["observed_methods"].extend(
                ["initialize", "account/read", "model/list", "thread/list"]
            )
            assertions.append(
                _assertion(
                    "runtime.app_server_reads",
                    "pass",
                    "Initialized App Server and completed typed account/model/thread reads.",
                    started,
                    server_version=(metadata.get("serverInfo") or {}).get("version"),
                    platform_family=metadata.get("platformFamily"),
                )
            )

            started = time.perf_counter()
            luna = next(
                (item for item in models_report if item["id"] == TARGET_MODEL), None
            )
            luna_low = bool(luna and TARGET_EFFORT in luna["supported_efforts"])
            assertions.append(
                _assertion(
                    "model.luna_low",
                    "pass" if luna_low else "fail",
                    "The runtime model catalog advertises Luna with low effort.",
                    started,
                    model=TARGET_MODEL,
                    effort=TARGET_EFFORT,
                    observed=luna,
                )
            )
            assertions.extend(
                _sandbox_checks(
                    client=client,
                    workspace=workspace,
                    escape_target=run_root / "outside-workspace.txt",
                )
            )
            protocol["observed_methods"].append("command/exec")
            assertions.append(_native_descendant_stop_check(client, workspace))
            protocol["observed_methods"].append(
                "host/process-tree-kill"
                if os.name == "nt"
                else "command/exec/terminate"
            )
        finally:
            client.close()

        assertions.append(_credential_owner_check(profile))
        assertions.append(_billing_fixture_check())
        assertions.append(_action_crash_recovery_check(workspace))

        api_key = _probe_api_key() if live else None
        if live:
            live_checks, live_details = _live_session_checks(
                profile=profile,
                workspace=workspace,
                api_key=api_key,
            )
            assertions.extend(live_checks)
            protocol["live_session"] = live_details
            present = {item.id for item in live_checks}
            missing = [
                item
                for item in _blocked_live_assertions(
                    "This M0 subtest is not implemented by the initial narrow probe yet."
                )
                if item.id not in present
            ]
            assertions.extend(missing)
        else:
            assertions.extend(
                _blocked_live_assertions(
                    "Live Codex model evidence requires the explicit --live flag."
                )
            )
    except ModuleNotFoundError as exc:
        assertions.append(
            AssertionResult(
                id="runtime.probe_dependency",
                status="fail",
                summary="Install the pinned codex-probe dependency group.",
                evidence={"error": str(exc), "command": "uv sync --group codex-probe"},
                elapsed_ms=0.0,
            )
        )
    except Exception as exc:  # noqa: BLE001
        assertions.append(
            AssertionResult(
                id="runtime.unhandled",
                status="fail",
                summary="The Codex probe stopped on an unexpected error.",
                evidence={"error_type": type(exc).__name__, "error": str(exc)},
                elapsed_ms=0.0,
            )
        )

    desktop_after = _desktop_sensitive_state()
    desktop_unchanged = desktop_before == desktop_after
    assertions.append(
        AssertionResult(
            id="profile.desktop_files_untouched",
            status="pass" if desktop_unchanged else "fail",
            summary="Desktop Codex configuration/authentication file metadata stayed unchanged.",
            evidence={
                "profile_is_separate": profile != desktop_profile,
                "sensitive_file_metadata_unchanged": desktop_unchanged,
            },
            elapsed_ms=0.0,
        )
    )

    status = _overall_status(assertions, live)
    report = {
        "schema_version": PROBE_SCHEMA,
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": _utc_now(),
        "mode": "live" if live else "inventory",
        "status": status,
        "runtime": runtime,
        "profile": {
            "kind": "dedicated" if profile_dir else "disposable",
            "desktop_profile_used": False,
            "desktop_sensitive_files_unchanged": desktop_unchanged,
            "retained": keep_run_dir or profile_dir is not None,
        },
        "auth": auth,
        "models": models_report,
        "protocol": protocol,
        "strategy": {
            "client": "official Python SDK",
            "lifecycle_fallback": "pinned App Server stdio for SDK-unwrapped methods",
            "session_storage": "dedicated Jarvis CODEX_HOME; never copy desktop credentials",
            "subscription_concurrency": 1,
            "authentication": (
                "API continuation is enabled; subscription execution remains disabled until a "
                "separate Jarvis ChatGPT profile passes this probe."
            ),
            "workflow_native_tools": (
                "disabled; workflow hosts expose only explicitly allowlisted brokered Jarvis MCP tools"
            ),
            "action_persistence": (
                "brokered Jarvis tools fsync/commit operation state before returning success; "
                "native side-effect completion events are observational only"
            ),
            "containment": (
                f"Native stop evidence plus OS sandbox enforcement; Windows uses the "
                f"{settings.task_windows_sandbox} ACL sandbox for this host and must fail closed when "
                "the configured backend or workspace boundary is unavailable. Immediate Windows "
                "stop kills the per-execution App Server process tree because command/exec/terminate "
                "is unsupported for Windows-sandboxed processes."
            ),
        },
        "assertions": [asdict(item) for item in assertions],
        "summary": {
            "passed": sum(item.status == "pass" for item in assertions),
            "failed": sum(item.status == "fail" for item in assertions),
            "blocked": sum(item.status == "blocked" for item in assertions),
            "gating_failed": sum(
                item.status == "fail" and item.gating for item in assertions
            ),
            "gating_blocked": sum(
                item.status == "blocked" and item.gating for item in assertions
            ),
        },
    }
    report = _redact_report_paths(
        report,
        [
            (str(run_root), "$RUN_ROOT"),
            (str(profile), "$PROFILE"),
            (str(Path.home().resolve()), "$USER_HOME"),
        ],
    )
    report_path = report_path.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if keep_run_dir:
        retained = report_path.with_suffix(report_path.suffix + ".run-dir.txt")
        retained.write_text(str(run_root), encoding="utf-8")
    if profile_dir is None and not keep_run_dir:
        shutil.rmtree(run_root, ignore_errors=True)
    elif profile_dir is not None:
        shutil.rmtree(run_root, ignore_errors=True)
    print(f"Codex M0 probe: {status}; report={report_path}")
    return 0 if status == "passed" else (1 if status == "failed" else 2)
