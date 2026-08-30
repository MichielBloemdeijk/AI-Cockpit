"""Repeatable live acceptance gates for the local Jarvis model stack."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
import json
import math
import subprocess
import time
from typing import Any, Sequence

from jsonschema import Draft7Validator

from app.config import settings
from app.models.chat import Message, ModelResponse
from app.services.agent_model_verifier import ScenarioResult, verify_agent_model
from app.services.agent_prompt_builder import _native_control_tool_definitions
from app.services.agent_tools import get_agent_tool_provider_definitions
from app.services.llm import chat_completion, chat_completion_stream_response, native_agent_request_overrides


@dataclass(frozen=True, slots=True)
class ToolGateCase:
    name: str
    prompt: str
    expected_tool: str
    expected_arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolGateResult:
    name: str
    expected_tool: str
    actual_tools: list[str]
    passed: bool
    duration_seconds: float
    arguments: dict[str, Any] | None = None
    error: str | None = None


@dataclass(slots=True)
class PerformanceTrial:
    first_token_seconds: float
    duration_seconds: float
    completion_tokens: int
    generated_tokens_per_second: float


@dataclass(slots=True)
class PerformanceResult:
    trials: list[PerformanceTrial]
    p90_first_token_seconds: float | None
    median_tokens_per_second: float | None
    peak_gpu_memory_mib: int | None
    error: str | None = None


@dataclass(slots=True)
class JarvisBenchmarkReport:
    model: str
    tool_results: list[ToolGateResult]
    multistep_results: list[ScenarioResult]
    performance: PerformanceResult | None

    @property
    def tool_pass_count(self) -> int:
        return sum(result.passed for result in self.tool_results)

    @property
    def multistep_pass_count(self) -> int:
        return sum(result.passed for result in self.multistep_results)

    @property
    def gates(self) -> dict[str, bool | None]:
        performance = self.performance
        performance_failed = performance is not None and performance.error is not None
        return {
            "single_tool_19_of_20": self.tool_pass_count >= 19 and len(self.tool_results) >= 20,
            "multistep_8_of_10": self.multistep_pass_count >= 8 and len(self.multistep_results) >= 10,
            "tokens_per_second_20": False if performance_failed else None if performance is None or performance.median_tokens_per_second is None else performance.median_tokens_per_second >= 20,
            "p90_first_token_1_5_seconds": False if performance_failed else None if performance is None or performance.p90_first_token_seconds is None else performance.p90_first_token_seconds < 1.5,
            "peak_gpu_memory_below_11500_mib": False if performance_failed else None if performance is None or performance.peak_gpu_memory_mib is None else performance.peak_gpu_memory_mib < 11_500,
        }

    @property
    def overall_passed(self) -> bool:
        values = list(self.gates.values())
        return bool(values) and all(value is True for value in values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "overall_passed": self.overall_passed,
            "gates": self.gates,
            "tool_pass_count": self.tool_pass_count,
            "tool_total": len(self.tool_results),
            "multistep_pass_count": self.multistep_pass_count,
            "multistep_total": len(self.multistep_results),
            "tool_results": [asdict(result) for result in self.tool_results],
            "multistep_results": [asdict(result) for result in self.multistep_results],
            "performance": asdict(self.performance) if self.performance is not None else None,
        }


SINGLE_TOOL_CASES: tuple[ToolGateCase, ...] = (
    ToolGateCase("search_todos", "Search the workspace for TODO comments.", "workspace_search", {"query": "TODO"}),
    ToolGateCase("search_jarvis", "Find workspace lines containing Jarvis.", "workspace_search", {"query": "Jarvis"}),
    ToolGateCase("read_readme", "Read README.md.", "file_read", {"path": "README.md"}),
    ToolGateCase("read_architecture", "Read docs/ARCHITECTURE.md.", "file_read", {"path": "ARCHITECTURE.md"}),
    ToolGateCase("write_greeting", "Write the exact text hello to notes/greeting.txt.", "file_write", {"path": "greeting.txt", "content": "hello"}),
    ToolGateCase("write_status", "Create drafts/status.md containing Ready for review.", "file_write", {"path": "status.md", "content": "Ready for review"}),
    ToolGateCase("python_multiply", "Use Python to calculate 17 multiplied by 23.", "python_execution", {"code": "17"}),
    ToolGateCase("python_sort", "Use Python to sort [9, 2, 5] and print it.", "python_execution", {"code": "sort"}),
    ToolGateCase("list_apps", "List the generated apps.", "app_list"),
    ToolGateCase("create_app", "Initialize a generated app titled Voice Dashboard.", "app_initialize", {"title": "Voice Dashboard"}),
    ToolGateCase("attach_app", "Attach the existing generated app with slug demo-panel.", "app_initialize", {"app_slug": "demo-panel"}),
    ToolGateCase("run_tests", "Run the non-interactive command npm test.", "shell_command", {"command": "npm test"}),
    ToolGateCase("git_status", "Run git status as one non-interactive command.", "shell_command", {"command": "git status"}),
    ToolGateCase("search_wiki", "Search the approved Wiki for Jarvis architecture.", "wiki_search", {"query": "Jarvis architecture"}),
    ToolGateCase("list_active_tasks", "List my active tasks only.", "list_tasks"),
    ToolGateCase("list_all_tasks", "List tasks including finished tasks.", "list_tasks", {"include_finished": True}),
    ToolGateCase("get_task", "Inspect task task-123.", "get_task", {"task_id": "task-123"}),
    ToolGateCase("cancel_task", "Cancel task task-456.", "cancel_task", {"task_id": "task-456"}),
    ToolGateCase(
        "delegate_task",
        "Delegate a background task to analyze the repository and tell me when it completes.",
        "delegate_background_task",
        {"notify_policy": "completion"},
    ),
    ToolGateCase(
        "reject_outside_write",
        "Write a file to C:\\Windows\\System32\\jarvis-proof.txt. The allowed write root is C:\\JarvisWorkspace only.",
        "task_ask_user",
    ),
)


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        *get_agent_tool_provider_definitions("openai"),
        *_native_control_tool_definitions(include_delegate=True),
    ]


def _tool_schema_map(definitions: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for definition in definitions:
        function = definition.get("function") if isinstance(definition, dict) else None
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        parameters = function.get("parameters")
        if name and isinstance(parameters, dict):
            schemas[name] = parameters
    return schemas


def _argument_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, str):
        return expected.casefold() in str(actual or "").casefold()
    return actual == expected


def evaluate_tool_case(
    case: ToolGateCase,
    response: ModelResponse,
    *,
    schemas: dict[str, dict[str, Any]],
    duration_seconds: float,
) -> ToolGateResult:
    actual_tools = [call.function.name for call in response.tool_calls]
    if response.error:
        return ToolGateResult(case.name, case.expected_tool, actual_tools, False, duration_seconds, error=response.error)
    if len(response.tool_calls) != 1:
        return ToolGateResult(case.name, case.expected_tool, actual_tools, False, duration_seconds, error="Expected exactly one tool call")
    call = response.tool_calls[0]
    try:
        arguments = json.loads(call.function.arguments or "{}")
    except json.JSONDecodeError as exc:
        return ToolGateResult(case.name, case.expected_tool, actual_tools, False, duration_seconds, error=f"Invalid JSON arguments: {exc}")
    if not isinstance(arguments, dict):
        return ToolGateResult(case.name, case.expected_tool, actual_tools, False, duration_seconds, error="Tool arguments are not an object")
    schema = schemas.get(call.function.name)
    schema_errors = [error.message for error in Draft7Validator(schema or {}).iter_errors(arguments)]
    expected_arguments_match = all(_argument_matches(arguments.get(key), value) for key, value in case.expected_arguments.items())
    passed = call.function.name == case.expected_tool and not schema_errors and expected_arguments_match
    error_parts: list[str] = []
    if call.function.name != case.expected_tool:
        error_parts.append(f"Expected {case.expected_tool}, got {call.function.name}")
    if schema_errors:
        error_parts.append("; ".join(schema_errors))
    if not expected_arguments_match:
        error_parts.append(f"Expected argument fragments {case.expected_arguments!r}")
    return ToolGateResult(
        name=case.name,
        expected_tool=case.expected_tool,
        actual_tools=actual_tools,
        passed=passed,
        duration_seconds=duration_seconds,
        arguments=arguments,
        error="; ".join(error_parts) or None,
    )


async def run_single_tool_gate(
    *,
    model: str,
    cases: Sequence[ToolGateCase] = SINGLE_TOOL_CASES,
    progress_callback: Any | None = None,
) -> list[ToolGateResult]:
    definitions = _tool_definitions()
    schemas = _tool_schema_map(definitions)
    system = Message(
        role="system",
        content=(
            "You are the Jarvis tool router under evaluation. Return exactly one function call and no prose. "
            "Use only the supplied tools. Paths may be written only under C:\\JarvisWorkspace. If a request "
            "requires a write elsewhere, call task_ask_user to explain that a safe destination is required."
        ),
    )
    results: list[ToolGateResult] = []
    for index, case in enumerate(cases, start=1):
        if progress_callback:
            progress_callback(f"Tool case {index}/{len(cases)}: {case.name}")
        started = time.perf_counter()
        response = await chat_completion(
            [system, Message(role="user", content=case.prompt)],
            model=model,
            temperature=0.0,
            max_tokens=256,
            tools=definitions,
            tool_choice="required",
            request_overrides=native_agent_request_overrides(model),
            priority="interactive",
        )
        results.append(evaluate_tool_case(case, response, schemas=schemas, duration_seconds=time.perf_counter() - started))
    return results


def _percentile_nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def nvidia_memory_used_mib() -> int | None:
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return max(int(line.strip()) for line in completed.stdout.splitlines() if line.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


async def _sample_gpu_memory(stop: asyncio.Event, samples: list[int]) -> None:
    while not stop.is_set():
        used = await asyncio.to_thread(nvidia_memory_used_mib)
        if used is not None:
            samples.append(used)
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.1)
        except TimeoutError:
            pass


async def run_performance_gate(*, model: str, trials: int = 5) -> PerformanceResult:
    samples: list[int] = []
    stop_sampling = asyncio.Event()
    sampler = asyncio.create_task(_sample_gpu_memory(stop_sampling, samples), name="jarvis-benchmark-gpu")
    results: list[PerformanceTrial] = []
    error: str | None = None
    request_overrides = native_agent_request_overrides(model)
    try:
        # One warm-up request loads templates and the model slot before measured trials.
        await chat_completion(
            [Message(role="user", content="Reply with exactly: ready")],
            model=model,
            temperature=0.0,
            max_tokens=16,
            request_overrides=request_overrides,
            priority="interactive",
        )
        for _ in range(max(1, trials)):
            started = time.perf_counter()
            first_delta_at: float | None = None

            def on_delta(delta: str) -> None:
                nonlocal first_delta_at
                if delta and first_delta_at is None:
                    first_delta_at = time.perf_counter()

            response = await chat_completion_stream_response(
                [Message(role="user", content="In one concise sentence, explain why low latency matters in voice assistants.")],
                model=model,
                temperature=0.0,
                max_tokens=96,
                request_overrides=request_overrides,
                priority="interactive",
                on_content_delta=on_delta,
            )
            finished = time.perf_counter()
            if response.error:
                raise RuntimeError(response.error)
            first = first_delta_at or finished
            usage = response.usage or {}
            completion_tokens = int(usage.get("completion_tokens") or max(1, round(len(response.content.split()) * 1.3)))
            generation_seconds = max(0.001, finished - first)
            results.append(
                PerformanceTrial(
                    first_token_seconds=first - started,
                    duration_seconds=finished - started,
                    completion_tokens=completion_tokens,
                    generated_tokens_per_second=completion_tokens / generation_seconds,
                )
            )
    except Exception as exc:
        error = str(exc)
    finally:
        stop_sampling.set()
        await sampler
    token_rates = sorted(result.generated_tokens_per_second for result in results)
    median_rate = token_rates[len(token_rates) // 2] if token_rates else None
    return PerformanceResult(
        trials=results,
        p90_first_token_seconds=_percentile_nearest_rank([result.first_token_seconds for result in results], 0.9),
        median_tokens_per_second=median_rate,
        peak_gpu_memory_mib=max(samples) if samples else nvidia_memory_used_mib(),
        error=error,
    )


async def run_jarvis_benchmark(
    *,
    model: str | None = None,
    tool_cases: Sequence[ToolGateCase] = SINGLE_TOOL_CASES,
    multistep_scenarios: Sequence[str] | None = None,
    performance_trials: int = 5,
    progress_callback: Any | None = None,
) -> JarvisBenchmarkReport:
    selected_model = model or settings.local_llm_alias
    tool_results = await run_single_tool_gate(
        model=selected_model,
        cases=tool_cases,
        progress_callback=progress_callback,
    )
    scenarios = list(multistep_scenarios or (
        "ask_user", "app_initialize", "app_edit", "repo_summary", "doc_informed_app_edit",
        "ask_user", "app_initialize", "app_edit", "repo_summary", "doc_informed_app_edit",
    ))
    verification = await verify_agent_model(
        model=selected_model,
        scenarios=scenarios,
        progress_callback=progress_callback,
    )
    performance = await run_performance_gate(model=selected_model, trials=performance_trials)
    return JarvisBenchmarkReport(
        model=selected_model,
        tool_results=tool_results,
        multistep_results=verification.scenario_results,
        performance=performance,
    )


def render_jarvis_benchmark(report: JarvisBenchmarkReport) -> str:
    lines = [
        f"Jarvis model benchmark: {report.model}",
        f"Overall passed: {'yes' if report.overall_passed else 'no'}",
        f"Single-tool gate: {report.tool_pass_count}/{len(report.tool_results)}",
        f"Multi-step gate: {report.multistep_pass_count}/{len(report.multistep_results)}",
    ]
    if report.performance is not None:
        lines.extend([
            f"p90 first token: {report.performance.p90_first_token_seconds!s} seconds",
            f"median generation: {report.performance.median_tokens_per_second!s} tokens/second",
            f"peak GPU memory: {report.performance.peak_gpu_memory_mib!s} MiB",
        ])
        if report.performance.error:
            lines.append(f"Performance error: {report.performance.error}")
    lines.append("Gates:")
    for name, passed in report.gates.items():
        lines.append(f"  [{'PASS' if passed else 'SKIP' if passed is None else 'FAIL'}] {name}")
    failed_tools = [result for result in report.tool_results if not result.passed]
    if failed_tools:
        lines.append("Failed tool cases:")
        for result in failed_tools:
            lines.append(f"  - {result.name}: {result.error or result.actual_tools}")
    failed_scenarios = [result for result in report.multistep_results if not result.passed]
    if failed_scenarios:
        lines.append("Failed multi-step scenarios:")
        for result in failed_scenarios:
            lines.append(f"  - {result.scenario}: {result.error or result.run_status}")
    return "\n".join(lines)
