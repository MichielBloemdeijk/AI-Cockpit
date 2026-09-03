"""Restricted capability gateway exposed to the realtime Jarvis shell."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any
from sqlalchemy import select

from app.db.session import session_scope
from app.db.tables import ConversationMessage
from app.services.history_retrieval import history_retrieval_service
from app.services.jarvis_preferences import jarvis_preferences_service
from app.services.natural_task_resolver import natural_task_resolver, task_title
from app.services.reminder_scheduler import reminder_scheduler
from app.services.task_error_messages import public_failure_summary
from app.services.task_service import task_service


ALLOWED_SHELL_TOOLS = frozenset(
    {
        "dispatch_task",
        "list_tasks",
        "get_task",
        "answer_task_question",
        "cancel_task",
        "set_task_update_policy",
        "create_reminder",
        "list_reminders",
        "snooze_reminder",
        "history_grep",
        "set_jarvis_voice",
        "start_new_jarvis_log",
        "end_voice_session",
    }
)


@dataclass(frozen=True, slots=True)
class BrainResult:
    output: dict[str, Any]
    control: str | None = None


class RestrictedBrainGateway:
    """Maps a small allowlist to durable application services."""

    async def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        call_id: str,
        conversation_id: str,
        origin_turn_id: str | None,
        active_time_zone: str = "Europe/Amsterdam",
        now: datetime | None = None,
    ) -> BrainResult:
        if tool_name not in ALLOWED_SHELL_TOOLS:
            raise ValueError(f"Jarvis shell tool is not allowed: {tool_name}")

        if tool_name == "dispatch_task":
            goal = " ".join(str(arguments.get("goal") or "").split()).strip()
            if await self._is_timed_reminder_request(
                goal=goal,
                conversation_id=conversation_id,
                origin_turn_id=origin_turn_id,
            ):
                raise ValueError(
                    "This is a timed reminder. Call create_reminder with structured timing; no task was created."
                )
            snapshot = await task_service.dispatch(
                goal=goal,
                origin_surface="jarvis",
                origin_conversation_id=conversation_id,
                origin_turn_id=origin_turn_id,
                importance=str(arguments.get("importance") or "normal"),
                presentation_policy=str(arguments.get("presentation_policy") or "critical_only"),
                targets=self._targets(arguments.get("targets")),
                idempotency_key=f"jarvis-shell:{call_id}:dispatch",
            )
            await natural_task_resolver.touch(snapshot.task.id)
            return BrainResult(output={
                "accepted": True,
                "title": task_title(snapshot.task),
                "status": snapshot.task.status,
                "status_reason": snapshot.task.status_reason,
                "queue_position": snapshot.queue_position,
                "internal_task_id": snapshot.task.id,
                "internal_fields_are_not_user_facing": True,
            })

        if tool_name in {"list_tasks", "list_reminders"}:
            tasks = await task_service.list_tasks()
            reminders_only = tool_name == "list_reminders"
            values = [
                {
                    "title": task_title(item.task),
                    "status": item.task.status,
                    "status_reason": item.task.status_reason,
                    "summary": (
                        public_failure_summary(item.task.terminal_summary)
                        if item.task.status in {"failed", "interrupted"}
                        else item.task.terminal_summary
                    ),
                    "diagnostics_available": bool(
                        item.task.status in {"failed", "interrupted"}
                        and item.task.terminal_summary
                    ),
                    "due_at": item.task.resume_at.isoformat() if item.task.resume_at else None,
                    "internal_task_id": item.task.id,
                }
                for item in reversed(tasks)
                if not reminders_only or (item.task.scheduled_delivery_json or {}).get("kind") == "reminder"
            ]
            return BrainResult(output={
                "tasks": values[:50],
                "internal_fields_are_not_user_facing": True,
            })

        if tool_name == "get_task":
            task = await self._resolve_task(arguments, mutation=False)
            detail = await task_service.get_task(task.id)
            if detail is None:
                raise ValueError("Task not found")
            await natural_task_resolver.touch(task.id)
            include_diagnostics = bool(arguments.get("include_diagnostics"))
            output = {
                "internal_task_id": detail.task.id,
                "title": task_title(detail.task),
                "goal": detail.task.goal,
                "status": detail.task.status,
                "status_reason": detail.task.status_reason,
                "summary": (
                    public_failure_summary(detail.task.terminal_summary)
                    if detail.task.status in {"failed", "interrupted"}
                    else detail.task.terminal_summary
                ),
                "diagnostics_available": bool(
                    detail.task.status in {"failed", "interrupted"}
                    and detail.task.terminal_summary
                ),
                "questions": [
                    {
                        "internal_question_id": item.id,
                        "question": item.question,
                        "state": item.state,
                    }
                    for item in detail.questions
                ],
                "internal_fields_are_not_user_facing": True,
            }
            if include_diagnostics and detail.task.terminal_summary:
                output["diagnostic_error"] = detail.task.terminal_summary
            return BrainResult(output=output)

        if tool_name == "answer_task_question":
            task = await self._resolve_task(arguments, mutation=True)
            detail = await task_service.get_task(task.id)
            if detail is None:
                raise ValueError("Task not found")
            pending = [item for item in detail.questions if item.state == "asked"]
            question_ref = str(arguments.get("question") or "").strip().lower()
            matching = [item for item in pending if question_ref in item.question.lower()] if question_ref else pending
            if len(matching) != 1:
                raise ValueError("Which pending task question do you want to answer?")
            snapshot = await task_service.answer_question(
                task.id,
                matching[0].id,
                answer=str(arguments.get("answer") or ""),
                idempotency_key=f"jarvis-shell:{call_id}:answer",
            )
            await natural_task_resolver.touch(task.id)
            return BrainResult(output={"title": task_title(task), "status": snapshot.task.status})

        if tool_name == "cancel_task":
            task = await self._resolve_task(arguments, mutation=True)
            snapshot = await task_service.cancel(
                task.id,
                idempotency_key=f"jarvis-shell:{call_id}:cancel",
            )
            await natural_task_resolver.touch(task.id)
            return BrainResult(output={"title": task_title(task), "status": snapshot.task.status})

        if tool_name == "set_task_update_policy":
            task = await self._resolve_task(arguments, mutation=True)
            snapshot = await task_service.set_update_policy(
                task.id,
                str(arguments.get("presentation_policy") or "critical_only"),
            )
            await natural_task_resolver.touch(task.id)
            return BrainResult(output={
                "title": task_title(task),
                "presentation_policy": snapshot.task.presentation_policy,
            })

        if tool_name == "create_reminder":
            snapshot = await reminder_scheduler.create(
                text=str(arguments.get("text") or ""),
                timing=arguments.get("timing"),
                due_at=arguments.get("due_at"),
                active_time_zone=active_time_zone,
                now=now,
                idempotency_key=f"jarvis-shell:{call_id}:reminder",
                origin_conversation_id=conversation_id,
                allow_duplicate=bool(arguments.get("allow_duplicate", False)),
            )
            await natural_task_resolver.touch(snapshot.task.id)
            return BrainResult(output={
                "accepted": True,
                "title": task_title(snapshot.task),
                "due_at": snapshot.task.resume_at.isoformat() if snapshot.task.resume_at else None,
            })

        if tool_name == "snooze_reminder":
            task = await self._resolve_task(arguments, mutation=False)
            snapshot = await reminder_scheduler.snooze(
                task.id,
                timing=arguments.get("timing"),
                due_at=arguments.get("due_at"),
                active_time_zone=active_time_zone,
                now=now,
                idempotency_key=f"jarvis-shell:{call_id}:snooze",
            )
            await natural_task_resolver.touch(snapshot.task.id)
            return BrainResult(output={
                "accepted": True,
                "title": task_title(snapshot.task),
                "due_at": snapshot.task.resume_at.isoformat() if snapshot.task.resume_at else None,
            })

        if tool_name == "history_grep":
            matches, truncated, elapsed_ms = await history_retrieval_service.search(
                str(arguments.get("argv") or "")
            )
            return BrainResult(output={
                "matches": [
                    {
                        "title": item.title,
                        "timestamp": item.timestamp.isoformat(),
                        "virtual_path": item.virtual_path,
                        "matching_text": item.matching_text,
                        "internal_source_id": item.internal_source_id,
                    }
                    for item in matches[:20]
                ],
                "truncated": truncated,
                "elapsed_ms": round(elapsed_ms, 2),
                "internal_fields_are_not_user_facing": True,
            })

        if tool_name == "set_jarvis_voice":
            voice = str(arguments.get("voice") or "").lower()
            if voice not in {"marin", "cedar"}:
                raise ValueError("Jarvis voice must be Marin or Cedar")
            preferences, _changed = await jarvis_preferences_service.patch({"voice": voice})
            return BrainResult(output={"voice": preferences.voice, "rotation_requested": True})

        if tool_name == "start_new_jarvis_log":
            return BrainResult(
                output={"accepted": True, "message": "Starting a fresh Jarvis log."},
                control="start_new_jarvis_log",
            )

        return BrainResult(
            output={"accepted": True, "message": "Ending the voice session."},
            control="end_voice_session",
        )

    async def _resolve_task(self, arguments: dict[str, Any], *, mutation: bool):
        # Trusted callers and replayed historical shell events may already carry
        # an internal ID. Keep accepting it without teaching the model or user
        # to work with identifiers.
        internal_id = str(arguments.get("task_id") or "").strip()
        if internal_id:
            detail = await task_service.get_task(internal_id)
            if detail is not None:
                if mutation and detail.task.status not in {
                    "queued", "waiting_for_worker", "running", "waiting_for_input", "waiting_until"
                }:
                    raise ValueError("That task is no longer active.")
                return detail.task
        reference = str(
            arguments.get("task_reference") or arguments.get("reference") or ""
        ).strip()
        result = await natural_task_resolver.resolve(reference, active_only=mutation)
        if result.ambiguous:
            raise ValueError(result.clarification or "Which task do you mean?")
        if result.task is None:
            raise ValueError("I couldn't find a task matching that description.")
        return result.task

    @staticmethod
    async def _is_timed_reminder_request(
        *,
        goal: str,
        conversation_id: str,
        origin_turn_id: str | None,
    ) -> bool:
        transcript = ""
        if origin_turn_id:
            async with session_scope() as session:
                transcript = str((await session.execute(
                    select(ConversationMessage.content)
                    .where(ConversationMessage.id == origin_turn_id)
                    .where(ConversationMessage.conversation_id == conversation_id)
                )).scalar_one_or_none() or "")
        value = f"{transcript} {goal}".casefold()
        has_reminder_intent = re.search(r"\b(remind(?:er)?|alarm|herinner(?:ing|en)?)\b", value) is not None
        has_timing = re.search(
            r"\b(in|at|on|today|tomorrow|tonight|minute|minutes|hour|hours|"
            r"over|om|vandaag|morgen|vanavond|minuut|minuten|uur)\b|\b\d{1,2}[:.]\d{2}\b",
            value,
        ) is not None
        return has_reminder_intent and has_timing

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
            targets.append((
                str(item.get("resource_type") or ""),
                str(item.get("resource_key") or ""),
                bool(item.get("mutable", True)),
            ))
        return tuple(targets)


restricted_brain_gateway = RestrictedBrainGateway()
