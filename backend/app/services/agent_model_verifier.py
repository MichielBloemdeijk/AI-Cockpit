"""Live verifier for agent-grade models against the native AI-Cockpit harness."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Sequence
from uuid import uuid4

from app.config import settings
from app.models.chat import Message
from app.models.settings import ConversationSessionMetadata
from app.services.agent_tools import get_agent_tool_provider_definitions
from app.services.agent_runner import agent_runner
from app.services.agent_runner import _native_control_tool_definitions, _native_plan_tool_definition
from app.services.chat_orchestrator import chat_orchestrator
from app.services.chat_settings import chat_settings_service
from app.services.conversation_read_model import conversation_read_model
from app.services.conversation_store import conversation_store
from app.services.llm import supports_native_tool_calls

ScenarioName = Literal["ask_user", "app_initialize", "app_edit", "repo_summary", "doc_informed_app_edit"]
SCENARIO_TIMEOUT_SECONDS = 90.0


@dataclass(slots=True)
class VerificationCheck:
    name: str
    passed: bool
    detail: str


@dataclass(slots=True)
class ScenarioTrace:
    scenario: str
    prompt: str
    conversation_id: str
    run_id: str
    run_status: str
    run_error: str | None
    duration_seconds: float
    final_message: str
    raw_tool_calls: list[dict[str, Any]]
    external_tool_calls: list[dict[str, Any]]
    tool_completions: list[dict[str, Any]]
    messages: list[str]


@dataclass(slots=True)
class ScenarioResult:
    scenario: str
    prompt: str
    passed: bool
    duration_seconds: float
    conversation_id: str
    run_id: str
    run_status: str
    final_message: str
    raw_tool_call_names: list[str]
    external_tool_names: list[str]
    error: str | None = None
    checks: list[VerificationCheck] = field(default_factory=list)

    @property
    def passed_checks(self) -> int:
        return sum(1 for check in self.checks if check.passed)

    @property
    def total_checks(self) -> int:
        return len(self.checks)


@dataclass(slots=True)
class HarnessSnapshot:
    entrypoint: str
    planner_prompt_source: str
    decision_prompt_source: str
    plan_tool_names: list[str]
    decision_tool_names: list[str]
    notes: list[str]


@dataclass(slots=True)
class VerificationReport:
    model: str
    started_at: str
    finished_at: str
    overall_passed: bool
    harness: HarnessSnapshot
    scenario_results: list[ScenarioResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "overall_passed": self.overall_passed,
            "harness": asdict(self.harness),
            "scenario_results": [
                {
                    **asdict(result),
                    "passed_checks": result.passed_checks,
                    "total_checks": result.total_checks,
                }
                for result in self.scenario_results
            ],
        }


@dataclass(frozen=True, slots=True)
class _ScenarioSpec:
    name: ScenarioName
    prompt_builder: Callable[[str], str]
    evaluator: Callable[[ScenarioTrace], ScenarioResult]


def _tool_definition_names(definitions: Sequence[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for definition in definitions:
        function = definition.get("function") if isinstance(definition, dict) else None
        if isinstance(function, dict):
            name = str(function.get("name") or "").strip()
            if name:
                names.append(name)
    return names


def _build_harness_snapshot() -> HarnessSnapshot:
    plan_tools = [_native_plan_tool_definition()]
    decision_tools = [*get_agent_tool_provider_definitions("openai"), *_native_control_tool_definitions()]
    return HarnessSnapshot(
        entrypoint="chat_orchestrator.run_single_response",
        planner_prompt_source="agent_runner._request_native_turn(plan_required=True)",
        decision_prompt_source="agent_runner._request_native_turn(plan_required=False)",
        plan_tool_names=_tool_definition_names(plan_tools),
        decision_tool_names=_tool_definition_names(decision_tools),
        notes=[
            "Verifier runs the real chat orchestrator and native agent runner.",
            "Plan and action turns both come from the same phase-aware native loop used by the product flow.",
            "Provider tool definitions are taken from chat_tools plus the native control tools.",
            "Only the user task message is synthetic; system prompts, context formatting, and tool schemas come from production code.",
        ],
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact_messages(messages: Sequence[Any], *, run_id: str) -> list[str]:
    rendered: list[str] = []
    for message in messages:
        if getattr(message, "run_id", None) != run_id:
            continue
        role = str(getattr(message, "role", "unknown"))
        content = str(getattr(message, "content", "")).strip()
        if content:
            rendered.append(f"{role}: {content}")
    return rendered


def _tool_call_names(raw_tool_calls: Sequence[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for payload in raw_tool_calls:
        name = str(payload.get("tool") or "").strip()
        if name:
            names.append(name)
    return names


def _external_tool_names(external_tool_calls: Sequence[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for payload in external_tool_calls:
        name = str(payload.get("tool") or "").strip()
        if name:
            names.append(name)
    return names


def _failed_tool_completions(trace: ScenarioTrace) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for payload in trace.tool_completions:
        if payload.get("ok") is False:
            failures.append(payload)
    return failures


def _check(name: str, passed: bool, detail: str) -> VerificationCheck:
    return VerificationCheck(name=name, passed=passed, detail=detail)


def _task_brief(*, summary: str, requirements: Sequence[str], success: Sequence[str]) -> str:
    requirement_lines = "\n".join(f"- {item}" for item in requirements)
    success_lines = "\n".join(f"- {item}" for item in success)
    return (
        f"Task:\n{summary}\n\n"
        f"Requirements:\n{requirement_lines}\n\n"
        f"Success Criteria:\n{success_lines}"
    )


def _app_initialize_scenario_prompt(label: str) -> str:
    return _task_brief(
        summary=f'Create a generated app called "{label}".',
        requirements=[
            "Attach the scaffold and stop as soon as the app is ready.",
            "Do not make any extra file edits after initialization.",
        ],
        success=[
            "The generated app is attached to the run.",
            "The final response confirms completion and route location.",
        ],
    )


def _app_edit_scenario_prompt(label: str) -> str:
    return _task_brief(
        summary=f'Create a generated app called "{label}" and update its main page.',
        requirements=[
            f'The main page must render the exact text "Harness OK {label}".',
            "Finish after making the required edit.",
        ],
        success=[
            "The app scaffold exists.",
            "page.tsx was updated with the exact required marker text.",
        ],
    )


def _ask_user_scenario_prompt(_label: str) -> str:
    return _task_brief(
        summary="Create an app for me, but do not choose the app name yourself.",
        requirements=[
            "Ask exactly one clarifying question for the app name.",
            "Do not do any external tool work before asking that question.",
        ],
        success=[
            "The run pauses waiting for the user answer.",
            "The final assistant message is the clarifying question.",
        ],
    )


def _repo_summary_scenario_prompt(_label: str) -> str:
    return _task_brief(
        summary="Inspect README.md and docs/ARCHITECTURE.md and summarize two facts.",
        requirements=[
            "Only inspect README.md and docs/ARCHITECTURE.md.",
            "Determine the official product name from those docs.",
            "Determine the exact phrase used in docs/ARCHITECTURE.md for the required generated-app direction.",
            "Do not write or modify any files.",
            "Finish with exactly two short bullet points and then stop.",
        ],
        success=[
            "The final response includes the exact product name `AI Cockpit`.",
            "The final response includes the exact phrase `chat-resident agent loop`.",
        ],
    )


def _doc_informed_app_edit_scenario_prompt(label: str) -> str:
    return _task_brief(
        summary=f'Create a generated app called "{label}" and make its page reflect README.md plus docs/ARCHITECTURE.md.',
        requirements=[
            "Inspect only README.md and docs/ARCHITECTURE.md before editing the app.",
            "The page must render the exact product name on one line and the exact generated-app direction phrase on another line.",
            "Keep the page minimal and stop after the edit is complete.",
        ],
        success=[
            "The page contains `AI Cockpit`.",
            "The page contains `chat-resident agent loop`.",
        ],
    )


def _evaluate_ask_user(trace: ScenarioTrace) -> ScenarioResult:
    raw_tool_names = _tool_call_names(trace.raw_tool_calls)
    external_tool_names = _external_tool_names(trace.external_tool_calls)
    checks = [
        _check("run paused", trace.run_status == "paused", f"run_status={trace.run_status}"),
        _check("task_plan used", "task_plan" in raw_tool_names, f"raw_tool_calls={raw_tool_names}"),
        _check("task_ask_user used", "task_ask_user" in raw_tool_names, f"raw_tool_calls={raw_tool_names}"),
        _check("no external tool work", not external_tool_names, f"external_tools={external_tool_names}"),
        _check("question surfaced", bool(trace.final_message.strip()), f"final_message={trace.final_message!r}"),
    ]
    return ScenarioResult(
        scenario=trace.scenario,
        prompt=trace.prompt,
        passed=all(check.passed for check in checks),
        duration_seconds=trace.duration_seconds,
        conversation_id=trace.conversation_id,
        run_id=trace.run_id,
        run_status=trace.run_status,
        final_message=trace.final_message,
        raw_tool_call_names=raw_tool_names,
        external_tool_names=external_tool_names,
        checks=checks,
    )


def _evaluate_app_initialize(trace: ScenarioTrace) -> ScenarioResult:
    raw_tool_names = _tool_call_names(trace.raw_tool_calls)
    external_tool_names = _external_tool_names(trace.external_tool_calls)
    failures = _failed_tool_completions(trace)
    completed_initialize = [
        payload for payload in trace.tool_completions if str(payload.get("tool") or "") == "app_initialize" and payload.get("ok") is True
    ]
    app_metadata = completed_initialize[-1].get("metadata") if completed_initialize else {}
    checks = [
        _check("run completed", trace.run_status == "completed", f"run_status={trace.run_status}"),
        _check("task_plan used", "task_plan" in raw_tool_names, f"raw_tool_calls={raw_tool_names}"),
        _check("app_initialize used", "app_initialize" in raw_tool_names, f"raw_tool_calls={raw_tool_names}"),
        _check("task_finalize used", "task_finalize" in raw_tool_names, f"raw_tool_calls={raw_tool_names}"),
        _check("no failed tools", not failures, f"failed_tools={failures}"),
        _check("app attached", bool((app_metadata or {}).get("app", {}).get("slug")), f"app_metadata={app_metadata}"),
    ]
    return ScenarioResult(
        scenario=trace.scenario,
        prompt=trace.prompt,
        passed=all(check.passed for check in checks),
        duration_seconds=trace.duration_seconds,
        conversation_id=trace.conversation_id,
        run_id=trace.run_id,
        run_status=trace.run_status,
        final_message=trace.final_message,
        raw_tool_call_names=raw_tool_names,
        external_tool_names=external_tool_names,
        checks=checks,
    )


def _evaluate_app_edit(trace: ScenarioTrace) -> ScenarioResult:
    raw_tool_names = _tool_call_names(trace.raw_tool_calls)
    external_tool_names = _external_tool_names(trace.external_tool_calls)
    failures = _failed_tool_completions(trace)
    file_write_calls = [payload for payload in trace.external_tool_calls if str(payload.get("tool") or "") == "file_write"]
    wrote_harness_marker = any(
        "Harness OK" in str(((payload.get("arguments") or {}).get("content") or "")) for payload in file_write_calls
    )
    checks = [
        _check("run completed", trace.run_status == "completed", f"run_status={trace.run_status}"),
        _check("task_plan used", "task_plan" in raw_tool_names, f"raw_tool_calls={raw_tool_names}"),
        _check("app_initialize used", "app_initialize" in external_tool_names, f"external_tools={external_tool_names}"),
        _check("file_write used", "file_write" in external_tool_names, f"external_tools={external_tool_names}"),
        _check("task_finalize used", "task_finalize" in raw_tool_names, f"raw_tool_calls={raw_tool_names}"),
        _check("no failed tools", not failures, f"failed_tools={failures}"),
        _check("wrote expected marker", wrote_harness_marker, f"file_write_calls={file_write_calls}"),
    ]
    return ScenarioResult(
        scenario=trace.scenario,
        prompt=trace.prompt,
        passed=all(check.passed for check in checks),
        duration_seconds=trace.duration_seconds,
        conversation_id=trace.conversation_id,
        run_id=trace.run_id,
        run_status=trace.run_status,
        final_message=trace.final_message,
        raw_tool_call_names=raw_tool_names,
        external_tool_names=external_tool_names,
        checks=checks,
    )


def _evaluate_repo_summary(trace: ScenarioTrace) -> ScenarioResult:
    raw_tool_names = _tool_call_names(trace.raw_tool_calls)
    external_tool_names = _external_tool_names(trace.external_tool_calls)
    failures = _failed_tool_completions(trace)
    final_text = trace.final_message
    checks = [
        _check("run completed", trace.run_status == "completed", f"run_status={trace.run_status}"),
        _check("task_plan used", "task_plan" in raw_tool_names, f"raw_tool_calls={raw_tool_names}"),
        _check(
            "doc inspection used",
            "workspace_search" in external_tool_names or "file_read" in external_tool_names,
            f"external_tools={external_tool_names}",
        ),
        _check("no write tools used", "file_write" not in external_tool_names, f"external_tools={external_tool_names}"),
        _check("task_finalize used", "task_finalize" in raw_tool_names, f"raw_tool_calls={raw_tool_names}"),
        _check("no failed tools", not failures, f"failed_tools={failures}"),
        _check("product name recovered", "AI Cockpit" in final_text, f"final_message={final_text!r}"),
        _check("direction phrase recovered", "chat-resident agent loop" in final_text, f"final_message={final_text!r}"),
    ]
    return ScenarioResult(
        scenario=trace.scenario,
        prompt=trace.prompt,
        passed=all(check.passed for check in checks),
        duration_seconds=trace.duration_seconds,
        conversation_id=trace.conversation_id,
        run_id=trace.run_id,
        run_status=trace.run_status,
        final_message=trace.final_message,
        raw_tool_call_names=raw_tool_names,
        external_tool_names=external_tool_names,
        checks=checks,
    )


def _evaluate_doc_informed_app_edit(trace: ScenarioTrace) -> ScenarioResult:
    raw_tool_names = _tool_call_names(trace.raw_tool_calls)
    external_tool_names = _external_tool_names(trace.external_tool_calls)
    failures = _failed_tool_completions(trace)
    file_write_calls = [payload for payload in trace.external_tool_calls if str(payload.get("tool") or "") == "file_write"]
    wrote_expected_markers = any(
        "AI Cockpit" in str(((payload.get("arguments") or {}).get("content") or ""))
        and "chat-resident agent loop" in str(((payload.get("arguments") or {}).get("content") or ""))
        for payload in file_write_calls
    )
    checks = [
        _check("run completed", trace.run_status == "completed", f"run_status={trace.run_status}"),
        _check("task_plan used", "task_plan" in raw_tool_names, f"raw_tool_calls={raw_tool_names}"),
        _check("app_initialize used", "app_initialize" in external_tool_names, f"external_tools={external_tool_names}"),
        _check(
            "doc inspection used",
            "workspace_search" in external_tool_names or "file_read" in external_tool_names,
            f"external_tools={external_tool_names}",
        ),
        _check("file_write used", "file_write" in external_tool_names, f"external_tools={external_tool_names}"),
        _check("task_finalize used", "task_finalize" in raw_tool_names, f"raw_tool_calls={raw_tool_names}"),
        _check("no failed tools", not failures, f"failed_tools={failures}"),
        _check("wrote doc markers", wrote_expected_markers, f"file_write_calls={file_write_calls}"),
    ]
    return ScenarioResult(
        scenario=trace.scenario,
        prompt=trace.prompt,
        passed=all(check.passed for check in checks),
        duration_seconds=trace.duration_seconds,
        conversation_id=trace.conversation_id,
        run_id=trace.run_id,
        run_status=trace.run_status,
        final_message=trace.final_message,
        raw_tool_call_names=raw_tool_names,
        external_tool_names=external_tool_names,
        checks=checks,
    )


def _scenario_failure_result(spec: _ScenarioSpec, prompt: str, duration_seconds: float, error: BaseException) -> ScenarioResult:
    return ScenarioResult(
        scenario=spec.name,
        prompt=prompt,
        passed=False,
        duration_seconds=duration_seconds,
        conversation_id="",
        run_id="",
        run_status="failed_to_execute",
        final_message="",
        raw_tool_call_names=[],
        external_tool_names=[],
        error=f"{type(error).__name__}: {error}",
        checks=[_check("scenario execution", False, f"{type(error).__name__}: {error}")],
    )


def _scenario_specs() -> dict[ScenarioName, _ScenarioSpec]:
    return {
        "ask_user": _ScenarioSpec(
            name="ask_user",
            prompt_builder=_ask_user_scenario_prompt,
            evaluator=_evaluate_ask_user,
        ),
        "app_initialize": _ScenarioSpec(
            name="app_initialize",
            prompt_builder=_app_initialize_scenario_prompt,
            evaluator=_evaluate_app_initialize,
        ),
        "app_edit": _ScenarioSpec(
            name="app_edit",
            prompt_builder=_app_edit_scenario_prompt,
            evaluator=_evaluate_app_edit,
        ),
        "repo_summary": _ScenarioSpec(
            name="repo_summary",
            prompt_builder=_repo_summary_scenario_prompt,
            evaluator=_evaluate_repo_summary,
        ),
        "doc_informed_app_edit": _ScenarioSpec(
            name="doc_informed_app_edit",
            prompt_builder=_doc_informed_app_edit_scenario_prompt,
            evaluator=_evaluate_doc_informed_app_edit,
        ),
    }


def render_report(report: VerificationReport) -> str:
    lines = [
        f"Model: {report.model}",
        f"Started: {report.started_at}",
        f"Finished: {report.finished_at}",
        f"Overall passed: {'yes' if report.overall_passed else 'no'}",
        f"Harness entrypoint: {report.harness.entrypoint}",
        f"Plan prompt source: {report.harness.planner_prompt_source}",
        f"Decision prompt source: {report.harness.decision_prompt_source}",
        f"Plan tools: {', '.join(report.harness.plan_tool_names) or 'none'}",
        f"Decision tools: {', '.join(report.harness.decision_tool_names) or 'none'}",
        "",
    ]
    for result in report.scenario_results:
        lines.append(f"Scenario: {result.scenario}")
        lines.append(f"Passed: {'yes' if result.passed else 'no'} ({result.passed_checks}/{result.total_checks})")
        lines.append(f"Run status: {result.run_status}")
        lines.append(f"Duration: {result.duration_seconds:.2f}s")
        lines.append(f"Raw tool calls: {', '.join(result.raw_tool_call_names) or 'none'}")
        lines.append(f"External tools: {', '.join(result.external_tool_names) or 'none'}")
        if result.error:
            lines.append(f"Error: {result.error}")
        lines.append(f"Final message: {result.final_message!r}")
        for check in result.checks:
            status = "PASS" if check.passed else "FAIL"
            lines.append(f"  [{status}] {check.name}: {check.detail}")
        lines.append("")
    return "\n".join(lines).rstrip()


async def _run_single_scenario(
    spec: _ScenarioSpec,
    *,
    defaults: ConversationSessionMetadata,
    model: str,
) -> ScenarioResult:
    scenario_label = f"Verifier {spec.name.replace('_', ' ').title()} {uuid4().hex[:8]}"
    prompt = spec.prompt_builder(scenario_label)
    started = time.perf_counter()
    async def _execute() -> ScenarioResult:
        effective_defaults = defaults.model_copy(update={"single_model": model})
        conversation = await conversation_store.create_conversation(
            mode_hint=effective_defaults.mode,
            session_metadata_json=effective_defaults.model_dump(),
        )
        agent_metadata = await chat_orchestrator._build_agent_metadata(
            conversation_id=conversation.id,
            goal=prompt,
            session_metadata=effective_defaults,
        )
        agent_metadata["model"] = model
        prepared = await chat_orchestrator.prepare_turn(
            messages=[Message(role="user", content=prompt)],
            conversation_id=conversation.id,
            run_kind="assistant",
            session_metadata=effective_defaults,
            metadata_json=agent_metadata,
        )
        try:
            await agent_runner.continue_run(prepared.run.id)
        except Exception as exc:
            error_text = str(exc) or "Agent run failed."
            await conversation_store.complete_message(
                prepared.conversation_id,
                run_id=prepared.run.id,
                role="assistant",
                content=error_text,
                actor_kind="assistant",
                event_type="conversation.assistant.message.completed",
                author_label="agent",
                payload_json={"model": model, "content": error_text},
            )

        duration_seconds = time.perf_counter() - started
        run = await conversation_store.get_run(prepared.run.id)
        if run is None:
            raise ValueError(f"Run not found for scenario {spec.name}: {prepared.run.id}")

        branch_messages = await conversation_read_model.list_messages_for_branch(
            prepared.conversation_id,
            branch_key="main",
            final_only=True,
        )
        assistant_message = next(
            (
                message
                for message in reversed(branch_messages)
                if message.run_id == prepared.run.id and message.role == "assistant"
            ),
            None,
        )
        run_metadata = dict(run.metadata_json or {}) if run is not None else {}
        pending_question = run_metadata.get("pending_question") if isinstance(run_metadata.get("pending_question"), dict) else None
        if assistant_message is None and pending_question:
            question_text = str(pending_question.get("question") or "Please clarify how to proceed.").strip()
            _, assistant_message = await conversation_store.complete_message(
                prepared.conversation_id,
                run_id=prepared.run.id,
                role="assistant",
                content=question_text,
                actor_kind="assistant",
                event_type="conversation.assistant.message.completed",
                author_label="agent",
                payload_json={"model": model, "content": question_text},
            )
        if assistant_message is None:
            fallback = "Agent run completed with no final message."
            if str(run.status or "").lower() == "failed":
                fallback = str(run.error or "Agent run failed.")
            _, assistant_message = await conversation_store.complete_message(
                prepared.conversation_id,
                run_id=prepared.run.id,
                role="assistant",
                content=fallback,
                actor_kind="assistant",
                event_type="conversation.assistant.message.completed",
                author_label="agent",
                payload_json={"model": model, "content": fallback},
            )

        events = await conversation_store.list_events_for_run(prepared.conversation_id, prepared.run.id)
        messages = await conversation_store.list_messages(prepared.conversation_id, final_only=True)
        trace = ScenarioTrace(
            scenario=spec.name,
            prompt=prompt,
            conversation_id=prepared.conversation_id,
            run_id=prepared.run.id,
            run_status=str(run.status or "unknown"),
            run_error=None if run.error in (None, "") else str(run.error),
            duration_seconds=duration_seconds,
            final_message=assistant_message.content,
            raw_tool_calls=[event.payload_json or {} for event in events if event.event_type == "agent.response.tool_call"],
            external_tool_calls=[event.payload_json or {} for event in events if event.event_type == "agent.tool.called"],
            tool_completions=[event.payload_json or {} for event in events if event.event_type == "agent.tool.completed"],
            messages=_compact_messages(messages, run_id=prepared.run.id),
        )
        return spec.evaluator(trace)

    try:
        return await asyncio.wait_for(_execute(), timeout=SCENARIO_TIMEOUT_SECONDS)
    except Exception as exc:
        duration_seconds = time.perf_counter() - started
        return _scenario_failure_result(spec, prompt, duration_seconds, exc)


async def verify_agent_model(
    *,
    model: str,
    scenarios: Sequence[ScenarioName] | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> VerificationReport:
    normalized_model = str(model or "").strip()
    if not normalized_model:
        raise ValueError("Model is required")
    if not settings.openrouter_api_key:
        raise ValueError("OPENROUTER_API_KEY is required for live model verification")
    if not supports_native_tool_calls(normalized_model):
        raise ValueError(f"Model is not currently allowed for the native agent harness: {normalized_model}")

    scenario_names = list(scenarios or ["ask_user", "app_initialize", "app_edit", "repo_summary", "doc_informed_app_edit"])
    known_scenarios = _scenario_specs()
    missing = [name for name in scenario_names if name not in known_scenarios]
    if missing:
        raise ValueError(f"Unknown scenarios: {', '.join(missing)}")

    defaults = await chat_settings_service.get_defaults()
    started_at = _utc_now_iso()
    results: list[ScenarioResult] = []
    harness = _build_harness_snapshot()
    for scenario_name in scenario_names:
        if progress_callback is not None:
            progress_callback(f"Starting scenario: {scenario_name}")
        result = await _run_single_scenario(known_scenarios[scenario_name], defaults=defaults, model=normalized_model)
        results.append(result)
        if progress_callback is not None:
            progress_callback(f"Finished scenario: {scenario_name} -> {'pass' if result.passed else 'fail'}")

    finished_at = _utc_now_iso()
    return VerificationReport(
        model=normalized_model,
        started_at=started_at,
        finished_at=finished_at,
        overall_passed=all(result.passed for result in results),
        harness=harness,
        scenario_results=results,
    )


async def verify_claude_sonnet_baseline() -> VerificationReport:
    return await verify_agent_model(model="anthropic/claude-sonnet-4-6")
