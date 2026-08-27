"""Restricted capability gateway exposed to the realtime Jarvis shell."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.task_service import task_service


ALLOWED_SHELL_TOOLS = frozenset(
    {
        "dispatch_task",
        "get_task",
        "answer_task_question",
        "cancel_task",
        "start_new_jarvis_log",
        "end_voice_session",
    }
)


@dataclass(frozen=True, slots=True)
class BrainResult:
    output: dict[str, Any]
    control: str | None = None


class RestrictedBrainGateway:
    """Maps the shell's tiny allowlist to durable application services.

    This class deliberately has no reference to the agent tool registry, file
    system, browser, code execution, or knowledge mutation surfaces.
    """

    async def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        call_id: str,
        conversation_id: str,
        origin_turn_id: str | None,
    ) -> BrainResult:
        if tool_name not in ALLOWED_SHELL_TOOLS:
            raise ValueError(f"Jarvis shell tool is not allowed: {tool_name}")

        if tool_name == "dispatch_task":
            goal = " ".join(str(arguments.get("goal") or "").split()).strip()
            targets = self._targets(arguments.get("targets"))
            snapshot = await task_service.dispatch(
                goal=goal,
                origin_surface="jarvis",
                origin_conversation_id=conversation_id,
                origin_turn_id=origin_turn_id,
                importance=str(arguments.get("importance") or "normal"),
                presentation_policy=str(arguments.get("presentation_policy") or "natural"),
                targets=targets,
                idempotency_key=f"jarvis-shell:{call_id}:dispatch",
            )
            return BrainResult(
                output={
                    "accepted": True,
                    "task_id": snapshot.task.id,
                    "status": snapshot.task.status,
                    "queue_position": snapshot.queue_position,
                }
            )

        if tool_name == "get_task":
            task_id = str(arguments.get("task_id") or "").strip()
            detail = await task_service.get_task(task_id)
            if detail is None:
                raise ValueError(f"Task not found: {task_id}")
            return BrainResult(
                output={
                    "task_id": detail.task.id,
                    "goal": detail.task.goal,
                    "status": detail.task.status,
                    "summary": detail.task.terminal_summary,
                    "questions": [
                        {"question_id": item.id, "question": item.question, "state": item.state}
                        for item in detail.questions
                    ],
                }
            )

        if tool_name == "answer_task_question":
            task_id = str(arguments.get("task_id") or "").strip()
            question_id = str(arguments.get("question_id") or "").strip()
            answer = str(arguments.get("answer") or "")
            snapshot = await task_service.answer_question(
                task_id,
                question_id,
                answer=answer,
                idempotency_key=f"jarvis-shell:{call_id}:answer",
            )
            return BrainResult(output={"task_id": task_id, "status": snapshot.task.status})

        if tool_name == "cancel_task":
            task_id = str(arguments.get("task_id") or "").strip()
            snapshot = await task_service.cancel(
                task_id,
                idempotency_key=f"jarvis-shell:{call_id}:cancel",
            )
            return BrainResult(output={"task_id": task_id, "status": snapshot.task.status})

        if tool_name == "start_new_jarvis_log":
            return BrainResult(
                output={"accepted": True, "message": "Starting a fresh Jarvis log."},
                control="start_new_jarvis_log",
            )

        return BrainResult(
            output={"accepted": True, "message": "Ending the voice session."},
            control="end_voice_session",
        )

    @staticmethod
    def _targets(value: Any) -> tuple[tuple[str, str, bool], ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise ValueError("Task targets must be a list")
        targets: list[tuple[str, str, bool]] = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("Each task target must be an object")
            targets.append(
                (
                    str(item.get("resource_type") or ""),
                    str(item.get("resource_key") or ""),
                    bool(item.get("mutable", True)),
                )
            )
        return tuple(targets)


restricted_brain_gateway = RestrictedBrainGateway()
