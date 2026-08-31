"""Execution seam shared by conversation runs and first-class task attempts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol
from zoneinfo import ZoneInfo

from app.config import settings
from app.models.chat import Message
from app.services.agent_prompt_builder import (
    NATIVE_AGENT_ASK_USER_TOOL,
    NATIVE_AGENT_FINALIZE_TOOL,
    NATIVE_AGENT_WAIT_UNTIL_TOOL,
    _native_control_tool_definitions,
    _parse_native_tool_arguments,
)
from app.services.agent_tools import (
    ToolExecutionContext,
    execute_agent_tool,
    get_agent_tool_provider_definitions,
)
from app.services.conversation_store import conversation_store
from app.services.llm import chat_completion
from app.services.task_service import task_service


TASK_CONTROL_TOOLS = {"list_tasks", "get_task", "cancel_task"}
CancellationProbe = Callable[[], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class AgentExecutionContext:
    execution_id: str
    task_id: str
    attempt_id: str
    goal: str
    workspace_path: str
    origin_conversation_id: str | None
    targets: tuple[tuple[str, str, bool], ...]
    checkpoint: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AgentExecutionResult:
    status: str
    summary: str | None = None


class AgentExecutionSink(Protocol):
    async def event(self, event_type: str, payload: dict[str, Any]) -> None: ...
    async def progress(self, summary: str) -> None: ...
    async def checkpoint(self, value: dict[str, Any]) -> None: ...
    async def question(self, question: str) -> None: ...
    async def wait_until(self, resume_at: datetime, reason: str) -> None: ...
    async def complete(self, summary: str, final_text: str) -> None: ...
    async def fail(self, error: str) -> None: ...
    async def cancelled(self) -> None: ...


class TargetExpansionRequested(RuntimeError):
    def __init__(self, targets: tuple[tuple[str, str, bool], ...], checkpoint: dict[str, Any]) -> None:
        super().__init__("Agent execution discovered additional mutable targets")
        self.targets = targets
        self.checkpoint = checkpoint


class TaskExecutionSink:
    def __init__(self, context: AgentExecutionContext) -> None:
        self.context = context
        self.counter = int(context.checkpoint.get("event_counter", 0) or 0)

    def _key(self, event_type: str) -> str:
        self.counter += 1
        return f"runtime:{self.context.attempt_id}:{self.counter}:{event_type}"

    async def event(self, event_type: str, payload: dict[str, Any]) -> None:
        await task_service.record_event(
            self.context.task_id,
            attempt_id=self.context.attempt_id,
            event_type=event_type,
            payload=payload,
            idempotency_key=self._key(event_type),
        )

    async def progress(self, summary: str) -> None:
        await task_service.publish_progress(
            self.context.task_id,
            summary=summary,
            idempotency_key=self._key("task.progress"),
        )

    async def checkpoint(self, value: dict[str, Any]) -> None:
        value["event_counter"] = self.counter
        await task_service.save_checkpoint(self.context.task_id, checkpoint=value)

    async def question(self, question: str) -> None:
        await task_service.ask_question(
            self.context.task_id,
            question=question,
            idempotency_key=self._key("task.requires_input"),
        )

    async def wait_until(self, resume_at: datetime, reason: str) -> None:
        await task_service.wait_until(
            self.context.task_id,
            resume_at=resume_at,
            reason=reason,
            idempotency_key=self._key("task.waiting_until"),
        )

    async def complete(self, summary: str, final_text: str) -> None:
        artifact = await task_service.add_artifact(
            self.context.task_id,
            artifact_type="result",
            mime_type="text/markdown",
            title="Task result",
            content_text=final_text,
            idempotency_key=self._key("task.result"),
        )
        await task_service.complete(
            self.context.task_id,
            summary=summary or final_text,
            idempotency_key=self._key("task.completed"),
        )
        await task_service.enqueue_presentation(
            self.context.task_id,
            candidate_type="terminal_result",
            coalescing_key=f"task:{self.context.task_id}:terminal",
            source_task_event_id=artifact.source_task_event_id,
            importance="normal",
            idempotency_key=f"runtime-presentation:{self.context.attempt_id}:terminal",
        )

    async def fail(self, error: str) -> None:
        await task_service.complete(
            self.context.task_id,
            status="failed",
            summary=error,
            idempotency_key=self._key("task.failed"),
        )

    async def cancelled(self) -> None:
        await task_service.finish_cancellation(
            self.context.task_id,
            idempotency_key=self._key("task.cancelled"),
        )


class ConversationExecutionSink:
    """Adapter retained for the existing interactive conversation runtime."""

    def __init__(self, *, conversation_id: str, run_id: str, branch_key: str = "main") -> None:
        self.conversation_id = conversation_id
        self.run_id = run_id
        self.branch_key = branch_key

    async def event(self, event_type: str, payload: dict[str, Any]) -> None:
        await conversation_store.append_event(
            self.conversation_id,
            run_id=self.run_id,
            actor_kind="assistant",
            event_type=event_type,
            payload_json=payload,
            branch_key=self.branch_key,
        )

    async def progress(self, summary: str) -> None:
        await self.event("agent.progress", {"summary": summary})

    async def checkpoint(self, value: dict[str, Any]) -> None:
        await conversation_store.update_run_metadata(self.run_id, value)

    async def question(self, question: str) -> None:
        await self.event("agent.question.asked", {"question": question})

    async def wait_until(self, resume_at: datetime, reason: str) -> None:
        raise RuntimeError("Interactive conversation runs cannot schedule task waits")

    async def complete(self, summary: str, final_text: str) -> None:
        await self.event("agent.completed", {"summary": summary, "final_text": final_text})

    async def fail(self, error: str) -> None:
        await self.event("agent.failed", {"error": error})

    async def cancelled(self) -> None:
        await self.event("agent.cancelled", {"status": "cancelled"})


class TaskAgentRuntime:
    async def execute(
        self,
        context: AgentExecutionContext,
        sink: AgentExecutionSink,
        *,
        cancellation_requested: CancellationProbe,
    ) -> AgentExecutionResult:
        checkpoint = dict(context.checkpoint)
        history = list(checkpoint.get("history") or [])
        start_step = int(checkpoint.get("step", 0) or 0)
        model = str(checkpoint.get("model") or settings.task_agent_model)
        tool_context = build_task_tool_context(context)

        async def tool_progress(summary: str) -> None:
            await sink.progress(summary)

        tool_context.progress_callback = tool_progress
        provider_tools = [
            tool for tool in get_agent_tool_provider_definitions("openai")
            if str(tool.get("function", {}).get("name") or "") not in TASK_CONTROL_TOOLS
        ]
        tools = [
            *provider_tools,
            *_native_control_tool_definitions(include_delegate=False, include_wait=True),
        ]
        allowed = {str(tool.get("function", {}).get("name") or "") for tool in tools}
        system_prompt = (
            "You are the AI-Cockpit background task agent running in an isolated worker. "
            "Use tools to materially advance the goal. Never write outside the allowed write roots. "
            "Call task_ask_user when required information is missing. Use task_wait_until only to pause this task until "
            "a known future instant. Call task_finalize with the actual result when done. "
            "Do not delegate another task."
        )

        await sink.progress("Planning next step")
        for step in range(start_step + 1, 51):
            if await cancellation_requested():
                await sink.cancelled()
                return AgentExecutionResult(status="cancelled", summary="Task cancelled by user.")

            transcript = json.dumps(history[-24:], ensure_ascii=False, default=str)
            user_prompt = (
                f"Goal:\n{context.goal}\n\n"
                f"Attempt workspace: {tool_context.workspace_root.as_posix()}\n"
                f"Allowed read roots: {json.dumps([path.as_posix() for path in tool_context.read_roots])}\n"
                f"Allowed write roots: {json.dumps([path.as_posix() for path in tool_context.write_roots])}\n\n"
                f"Completed runtime transcript:\n{transcript}\n\nContinue from the latest state."
            )
            response = await chat_completion(
                [Message(role="system", content=system_prompt), Message(role="user", content=user_prompt)],
                model,
                temperature=0.2,
                max_tokens=4096,
                tools=tools,
                tool_choice="auto",
                session_id=f"global-task:{context.task_id}",
                priority="background",
            )
            if await cancellation_requested():
                await sink.cancelled()
                return AgentExecutionResult(status="cancelled", summary="Task cancelled by user.")
            if response.error:
                raise RuntimeError(response.error)
            if response.reasoning.strip():
                await sink.event("agent.thought", {"step": step, "summary": response.reasoning.strip()})
            if response.content.strip():
                await sink.event("agent.output", {"step": step, "content": response.content.strip()})

            calls = [call for call in response.tool_calls if call.function.name.strip()]
            if not calls:
                final_text = response.content.strip()
                if not final_text:
                    raise RuntimeError("Task agent returned no tool call or final text")
                await sink.complete(final_text.splitlines()[0][:240], final_text)
                return AgentExecutionResult(status="completed", summary=final_text)
            control_names = {
                NATIVE_AGENT_ASK_USER_TOOL,
                NATIVE_AGENT_FINALIZE_TOOL,
                NATIVE_AGENT_WAIT_UNTIL_TOOL,
            }
            if len(calls) > 1 and any(call.function.name in control_names for call in calls):
                raise RuntimeError("Task agent combined a control tool with another tool call")

            for call in calls:
                tool_name = call.function.name.strip()
                if tool_name not in allowed:
                    raise RuntimeError(f"Tool {tool_name} is not available for this task")
                arguments = _parse_native_tool_arguments(call.function.arguments, tool_name=tool_name)
                if tool_name == NATIVE_AGENT_ASK_USER_TOOL:
                    question = str(arguments.get("question") or "").strip()
                    if not question:
                        raise RuntimeError("Task question cannot be empty")
                    checkpoint.update({"step": step, "history": history, "model": model})
                    await sink.checkpoint(checkpoint)
                    await sink.question(question)
                    return AgentExecutionResult(status="waiting_for_input", summary=question)
                if tool_name == NATIVE_AGENT_FINALIZE_TOOL:
                    summary = str(arguments.get("summary") or "").strip()
                    final_text = response.content.strip() or str(arguments.get("final_text") or "").strip()
                    if not final_text:
                        raise RuntimeError("Task final result cannot be empty")
                    await sink.complete(summary or final_text.splitlines()[0][:240], final_text)
                    return AgentExecutionResult(status="completed", summary=summary or final_text)
                if tool_name == NATIVE_AGENT_WAIT_UNTIL_TOOL:
                    raw_resume_at = str(arguments.get("resume_at") or "").strip()
                    reason = str(arguments.get("reason") or "").strip()
                    try:
                        resume_at = datetime.fromisoformat(raw_resume_at.replace("Z", "+00:00"))
                    except ValueError as exc:
                        raise RuntimeError("task_wait_until requires an ISO-8601 resume_at") from exc
                    if resume_at.tzinfo is None:
                        resume_at = resume_at.replace(tzinfo=ZoneInfo("Europe/Amsterdam"))
                    resume_at = resume_at.astimezone(timezone.utc)
                    checkpoint.update({"step": step, "history": history, "model": model})
                    await sink.checkpoint(checkpoint)
                    await sink.wait_until(resume_at, reason)
                    return AgentExecutionResult(status="waiting_until", summary=reason)

                await sink.event("agent.tool.started", {"step": step, "tool": tool_name, "arguments": arguments})
                result = await execute_agent_tool(context=tool_context, tool=tool_name, arguments=arguments)
                history.append({
                    "kind": "tool",
                    "step": step,
                    "tool": tool_name,
                    "arguments": arguments,
                    "ok": True,
                    "output": result.output,
                    "metadata": result.metadata,
                })
                await sink.event("agent.tool.completed", {
                    "step": step,
                    "tool": tool_name,
                    "ok": True,
                    "output": result.output,
                    "metadata": result.metadata,
                })
                expansion = result.metadata.get("target_expansion") if isinstance(result.metadata, dict) else None
                if isinstance(expansion, dict):
                    resource_type = str(expansion.get("resource_type") or "").strip()
                    resource_key = str(expansion.get("resource_key") or "").strip()
                    if resource_type and resource_key:
                        checkpoint.update({"step": step, "history": history, "model": model})
                        raise TargetExpansionRequested(
                            ((resource_type, resource_key, bool(expansion.get("mutable", True))),),
                            checkpoint,
                        )
            checkpoint.update({"step": step, "history": history, "model": model})
            await sink.checkpoint(checkpoint)
        raise RuntimeError("Task agent reached the 50-step safety limit")


def build_task_tool_context(context: AgentExecutionContext) -> ToolExecutionContext:
    workspace = Path(context.workspace_path).resolve()
    repository_root = settings.backend_root.parent.resolve()
    read_roots = [path.resolve() for path in settings.allowed_dir_list] or [repository_root]
    write_roots = [workspace]
    for resource_type, resource_key, mutable in context.targets:
        if not mutable:
            continue
        resolved: Path | None = None
        if resource_type == "generated_app":
            resolved = (repository_root / "frontend" / "generated-apps" / resource_key).resolve()
        elif resource_type in {"directory", "path", "workspace"}:
            candidate = Path(resource_key)
            resolved = candidate.resolve() if candidate.is_absolute() else (repository_root / candidate).resolve()
        elif resource_type == "repository" and resource_key in {".", repository_root.name, str(repository_root)}:
            resolved = repository_root
        if resolved is not None and resolved not in write_roots:
            write_roots.append(resolved)
    return ToolExecutionContext(
        conversation_id=context.origin_conversation_id or f"task:{context.task_id}",
        run_id=context.attempt_id,
        workspace_path=str(workspace),
        workspace_root=workspace,
        read_roots=read_roots,
        write_roots=write_roots,
    )


task_agent_runtime = TaskAgentRuntime()
