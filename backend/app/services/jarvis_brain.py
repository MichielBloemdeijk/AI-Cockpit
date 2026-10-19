"""Restricted capability gateway exposed to the Live Jarvis voice session."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from time import perf_counter as monotonic
from typing import Any
from sqlalchemy import select

from app.db.session import read_session_scope
from app.db.tables import ConversationMessage, LiveSession, ResultPage, Task, TaskContextRequest, TaskExecutionRun, TaskTurn
from app.config import settings
from app.services.history_retrieval import history_retrieval_service
from app.services.jarvis_preferences import jarvis_preferences_service
from app.services.natural_task_resolver import natural_task_resolver, task_title
from app.services.reminder_scheduler import reminder_scheduler
from app.services.task_error_messages import public_failure_summary
from app.services.task_service import task_service
from app.services.task_capture import task_capture_service
from app.services.task_workflows import resolve_workflow_config
from app.services.task_result_contract import result_links, admission_result_url
from app.services.admission_timing import observe_admission, timestamp, timed


ALLOWED_SHELL_TOOLS = frozenset(
    {
        "dispatch_task",
        "start_workflow",
        "list_tasks",
        "get_task",
        "answer_task_question",
        "cancel_task",
        "continue_task",
        "control_task",
        "set_task_update_policy",
        "create_reminder",
        "list_reminders",
        "get_attention_checklist",
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

    async def _preference_receipt(self, result):
        task_id = result.output.get("task_id")
        if task_id:
            from app.services.task_notification_preferences import current
            async with read_session_scope() as session:
                task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
                preference = await current(session,task)
            result.output.update(notification=preference,notification_intent=preference["intent"],notification_revision=preference["revision"])
        return result

    @observe_admission
    async def execute(
        self, *, turn_scope: str | None = None, **kwargs,
    ) -> BrainResult:
        name = kwargs.get("tool_name")
        if name not in {"dispatch_task", "start_workflow", "continue_task"} or kwargs.get("session_id") is None:
            result = await self._execute(**kwargs)
            return await self._preference_receipt(result) if name in {"dispatch_task","start_workflow","set_task_update_policy"} else result
        from app.services.live_admission_intents import reserve_intent, finish_intent, pin_target
        receipt = await reserve_intent(session_id=kwargs["session_id"], generation=kwargs["generation"],
            turn_scope=turn_scope or kwargs.get("origin_turn_id"),
            intent_id=kwargs["arguments"].get("intent_id"), tool_name=name, arguments=kwargs["arguments"])
        if receipt.output_json is not None:
            return await self._preference_receipt(BrainResult(output={**receipt.output_json, "replayed": True}))
        if receipt.error is not None:
            raise ValueError(receipt.error)
        # The receipt key is shared across routes; the receipt pins the route
        # and arguments before any capture wait or admission transaction.
        kwargs["call_id"] = f"intent:{receipt.id}"
        if name == "continue_task":
            if receipt.target_task_id is None:
                target = await self._resolve_task(kwargs["arguments"], mutation=True, allow_terminal=True)
                kwargs["admission_task_id"] = await pin_target(receipt.id, target.id)
            else:
                kwargs["admission_task_id"] = receipt.target_task_id
        # Retried deliveries share the original bounded pre-admission wait.
        from app.services.task_capture import PREFLIGHT_WAIT_SECONDS
        created = receipt.created_at.replace(tzinfo=timezone.utc) if receipt.created_at.tzinfo is None else receipt.created_at
        kwargs["capture_wait_seconds"] = max(0., PREFLIGHT_WAIT_SECONDS - (datetime.now(timezone.utc) - created).total_seconds())
        try:
            result = await self._execute(**kwargs)
            if name in {"dispatch_task","start_workflow"}: await self._preference_receipt(result)
            result.output.setdefault("admission_timing", {"clock": "server_monotonic",
                "admission_commit_ns": __import__("time").perf_counter_ns(),
                "intent_id": receipt.intent_id if hasattr(receipt, "intent_id") else receipt.id})
            if committed := timestamp("admission_commit_ns"):
                result.output["admission_timing"]["admission_commit_ns"] = committed
        except ValueError as exc:
            await finish_intent(receipt.id, error=str(exc))
            raise
        await finish_intent(receipt.id, output=result.output)
        return result

    async def _execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        call_id: str,
        conversation_id: str,
        origin_turn_id: str | None,
        active_time_zone: str = "Europe/Amsterdam",
        now: datetime | None = None,
        session_id: str | None = None,
        generation: int | None = None,
        provider_event_id: str | None = None,
        provider_call_id: str | None = None,
        capture_wait_seconds: float | None = None,
        admission_task_id: str | None = None,
        capture_request_identity: str | None = None,
        notification_review: dict | None = None,
    ) -> BrainResult:
        if tool_name not in ALLOWED_SHELL_TOOLS:
            raise ValueError(f"Jarvis shell tool is not allowed: {tool_name}")

        preference_args = {"notification_intent":arguments.get("notification_intent"),
            "notification_reason":arguments.get("notification_reason"),
            "notification_authority":({"session_id":session_id,"generation":generation,"call_id":call_id,
                **(notification_review or {})} if session_id else None)}

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
                execution_config=arguments.get("execution_preferences"),
                result_request=arguments.get("result"),
                origin_conversation_id=conversation_id,
                origin_turn_id=origin_turn_id,
                importance=str(arguments.get("importance") or "normal"),
                presentation_policy=arguments.get("presentation_policy"),
                **preference_args,
                targets=self._targets(arguments.get("targets")),
                idempotency_key=f"jarvis-shell:{call_id}:dispatch",
            )
            await natural_task_resolver.touch(snapshot.task.id)
            async with read_session_scope() as session:
                admitted_turn = (await session.execute(select(TaskTurn).where(
                    TaskTurn.task_id == snapshot.task.id,
                    TaskTurn.request_identity == f"jarvis-shell:{call_id}:dispatch"))).scalar_one()
                result_url = await admission_result_url(session, snapshot.task.id, f"jarvis-shell:{call_id}:dispatch", turn=admitted_turn)
            return BrainResult(output={
                "accepted": True,
                "title": task_title(snapshot.task),
                "status": snapshot.task.status,
                "status_reason": snapshot.task.status_reason,
                "queue_position": snapshot.queue_position,
                "internal_task_id": snapshot.task.id,
                "task_id": snapshot.task.id,
                "turn_id": admitted_turn.id,
                "execution_run_id": admitted_turn.execution_run_id,
                "result_url": result_url,
                "internal_fields_are_not_user_facing": True,
            })

        if tool_name == "start_workflow":
            admission_started = monotonic()
            goal = " ".join(str(arguments.get("goal") or "").split()).strip()
            definition = str(arguments.get("definition") or "")
            capture_mode = str(arguments.get("screen_context_mode") or "activation")
            capture_id = str(arguments.get("capture_id") or "") or None
            if not goal or len(goal) > 4000 or capture_mode not in {"activation", "fresh"}:
                raise ValueError("Screen workflow request or capture mode is invalid")
            workflow_request = {"definition": definition, **(arguments.get("execution_preferences") or {})}
            config = resolve_workflow_config(workflow_request)
            key = f"jarvis-shell:{call_id}:start-workflow"
            if "task.context.images@2" not in config["workflow"]["capabilities"]:
                if capture_id is not None:
                    raise ValueError("This workflow does not accept a screen capture")
                created = await task_service.dispatch(goal=goal, origin_surface="jarvis_voice",
                    execution_kind="workflow", execution_config=workflow_request, idempotency_key=key,
                    presentation_policy=arguments.get("presentation_policy"), **preference_args)
                async with read_session_scope() as session:
                    turn = (await session.execute(select(TaskTurn).where(TaskTurn.task_id == created.task.id,
                        TaskTurn.request_identity == key))).scalar_one()
                return BrainResult(output={"accepted": True, "internal_task_id": created.task.id,
                    "task_id": created.task.id, "turn_id": turn.id, "execution_run_id": turn.execution_run_id,
                    "status": created.task.status, "queue_position": created.queue_position,
                    "internal_fields_are_not_user_facing": True})
            if session_id is None or generation is None:
                raise ValueError("A screen workflow requires an active Live session")
            async with read_session_scope() as session:
                live = await session.get(LiveSession, session_id)
                expected_catalog = hashlib.sha256(settings.task_workflow_allowlist.encode("utf-8")).hexdigest()
                if (live is None or live.generation != generation or live.controller_id is None
                        or live.activation_id is None or live.lifecycle_state not in {"connecting", "ready"}
                        or live.workflow_catalog_revision != expected_catalog):
                    raise ValueError("Live workflow authority or catalog is stale")
                device_id, activation_id = live.controller_id, live.activation_id
                existing = (await session.execute(select(Task).where(Task.idempotency_key == key))).scalar_one_or_none()
                if existing is not None:
                    turn = (await session.execute(select(TaskTurn).where(TaskTurn.task_id == existing.id,
                        TaskTurn.request_identity == key))).scalar_one_or_none()
                    run = await session.get(TaskExecutionRun, turn.execution_run_id) if turn else None
                    request = (await session.execute(select(TaskContextRequest).where(
                        TaskContextRequest.task_id == existing.id,
                        TaskContextRequest.execution_run_id == run.id if run else False,
                    ).order_by(TaskContextRequest.capture_mode).limit(1))).scalar_one_or_none()
                    page = (await session.execute(select(ResultPage).where(
                        ResultPage.task_id == existing.id,
                        ResultPage.initial_execution_run_id == run.id if run else False,
                    ))).scalar_one_or_none()
                    if (turn is None or turn.content != goal or run is None or run.execution_kind != "workflow"
                            or (run.configuration_json or {}).get("workflow", {}).get("digest") != config["workflow"]["digest"]
                            or request is None or page is None or request.activation_id != activation_id
                            or request.device_id != device_id or request.capture_mode != capture_mode
                            or (capture_id is not None and request.capture_id != capture_id)):
                        raise ValueError("Workflow call identity was already used for different input")
                    return BrainResult(output={
                        "accepted": True, "status": existing.status, "internal_task_id": existing.id,
                        "task_id": existing.id, "turn_id": turn.id, "execution_run_id": run.id,
                        "context_request_id": request.id, "replayed": True,
                        "result_url": f"/results/{page.public_id}",
                        "internal_fields_are_not_user_facing": True,
                    })
            binding = await task_capture_service.preflight_collection(
                request_identity=capture_request_identity or call_id,
                device_id=device_id, session_id=session_id, generation=generation,
                activation_id=activation_id, capture_mode=capture_mode,
                capture_id=capture_id,
                **({"wait_seconds": capture_wait_seconds} if capture_wait_seconds is not None else {}),
            )
            preflight_finished = monotonic()
            snapshot = await task_service.dispatch(
                goal=goal, idempotency_key=key, origin_surface="jarvis_voice",
                origin_conversation_id=conversation_id, origin_turn_id=origin_turn_id,
                execution_kind="workflow", execution_config=workflow_request,
                capture_binding=binding,
                presentation_policy=arguments.get("presentation_policy"), **preference_args,
            )
            admission_finished = monotonic()
            await natural_task_resolver.touch(snapshot.task.id)
            async with read_session_scope() as session:
                request = (await session.execute(select(TaskContextRequest).where(
                    TaskContextRequest.task_id == snapshot.task.id,
                    TaskContextRequest.execution_run_id == snapshot.task.current_run_id,
                ).order_by(TaskContextRequest.capture_mode).limit(1))).scalar_one()
                page = (await session.execute(select(ResultPage).where(
                    ResultPage.task_id == snapshot.task.id,
                    ResultPage.initial_execution_run_id == snapshot.task.current_run_id,
                ))).scalar_one()
                turn = (await session.execute(select(TaskTurn).where(TaskTurn.task_id == snapshot.task.id,
                    TaskTurn.request_identity == key))).scalar_one()
            from app.services.result_telemetry import observe
            await observe(page.id, kind="screen_admission", key=f"screen-admission:{page.id}",
                run_id=page.active_execution_run_id, revision_number=page.revision_number,
                duration_ms=(admission_finished - admission_started) * 1000,
                values={"preflight_ms": (preflight_finished - admission_started) * 1000,
                    "durable_admission_ms": (admission_finished - preflight_finished) * 1000,
                    "includes_capture_preflight": True, "clock": "server_monotonic"})
            return BrainResult(output={
                "accepted": True, "status": snapshot.task.status,
                "internal_task_id": snapshot.task.id,
                "task_id": snapshot.task.id, "turn_id": turn.id, "execution_run_id": turn.execution_run_id,
                "context_request_id": request.id,
                "result_url": f"/results/{page.public_id}",
                "admission_timing": {"clock": "server_monotonic",
                    "capture_ready_ns": int(preflight_finished * 1_000_000_000),
                    "admission_commit_ns": int(admission_finished * 1_000_000_000)},
                "screen_context": binding.manifest(),
                "timing_ms": {
                    "preflight": round((preflight_finished - admission_started) * 1000, 1),
                    "durable_admission": round((admission_finished - preflight_finished) * 1000, 1),
                    "ready_call_to_admission": round((admission_finished - admission_started) * 1000, 1),
                },
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
                "runs": [
                    {"ordinal": run.ordinal, "status": run.status, "summary": run.terminal_summary}
                    for run in detail.runs
                ],
                "recent_events": [
                    {"type": event.event_type, "payload": event.payload_json}
                    for event in detail.events[-12:]
                ],
                "internal_fields_are_not_user_facing": True,
            }
            if include_diagnostics and detail.task.terminal_summary:
                output["diagnostic_error"] = detail.task.terminal_summary
            async with read_session_scope() as session:
                output["results"] = await result_links(session, task.id)
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

        if tool_name == "continue_task":
            task = await self._resolve_task({"task_id": admission_task_id} if admission_task_id else arguments,
                mutation=True, allow_terminal=True)
            if admission_task_id:
                # Recover a committed admission after a crash before receipt
                # acknowledgement. Its capture may already have expired.
                async with read_session_scope() as session:
                    prior_turn = (await session.execute(select(TaskTurn).where(TaskTurn.task_id == task.id,
                        TaskTurn.request_identity == f"jarvis-shell:{call_id}:continue"))).scalar_one_or_none()
                    if prior_turn is not None:
                        prior_run = await session.get(TaskExecutionRun, prior_turn.execution_run_id)
                        url = await admission_result_url(session, task.id, f"jarvis-shell:{call_id}:continue", turn=prior_turn)
                        return BrainResult(output={"accepted": True, "title": task_title(task),
                            "task_id": task.id, "internal_task_id": task.id, "turn_id": prior_turn.id,
                            "execution_run_id": prior_run.id, "run_ordinal": prior_run.ordinal,
                            "status": task.status, "result_url": url, "replayed": True})
            binding = None
            if arguments.get("screen_context_mode") is not None:
                mode = arguments["screen_context_mode"]
                if mode not in {"activation", "fresh"} or session_id is None:
                    raise ValueError("Continuation screen context selection is invalid")
                async with read_session_scope() as session:
                    live = await session.get(LiveSession, session_id)
                    if live is None or live.generation != generation or not live.activation_id:
                        raise ValueError("Continuation screen session is unavailable")
                    device_id, activation_id = live.controller_id, live.activation_id
                capture_id = arguments.get("capture_id")
                binding = await task_capture_service.preflight_collection(
                    request_identity=capture_request_identity or call_id, device_id=device_id, session_id=session_id,
                    generation=generation, activation_id=activation_id, capture_mode=mode, capture_id=capture_id,
                    **({"wait_seconds": capture_wait_seconds} if capture_wait_seconds is not None else {}))
            capture_ready_ns = __import__("time").perf_counter_ns() if binding else None
            admission = await task_service.continue_task(
                task.id,
                content=str(arguments.get("content") or ""),
                origin="jarvis",
                result_request=arguments.get("result"),
                execution_config=arguments.get("execution_preferences"),
                capture_binding=binding,
                idempotency_key=f"jarvis-shell:{call_id}:continue",
            )
            committed_ns = __import__("time").perf_counter_ns()
            await natural_task_resolver.touch(task.id)
            async with read_session_scope() as session:
                result_url = await admission_result_url(session, task.id, f"jarvis-shell:{call_id}:continue", turn=admission.turn)
            return BrainResult(output={
                "accepted": True,
                "title": task_title(task),
                "task_id": task.id,
                "internal_task_id": task.id,
                "turn_id": admission.turn.id,
                "execution_run_id": admission.run.id,
                "status": admission.task.status,
                "result_url": result_url,
                "run_ordinal": admission.run.ordinal,
                "queue_position": admission.queue_position,
                "admission_timing": {"clock": "server_monotonic", "admission_commit_ns": committed_ns,
                    **({"capture_ready_ns": capture_ready_ns} if capture_ready_ns else {})},
                **({"screen_context": binding.manifest()} if binding else {}),
            })

        if tool_name == "control_task":
            task = await self._resolve_task(arguments, mutation=True, allow_terminal=True)
            reason = str(arguments.get("reason") or "").strip()
            if not reason:
                raise ValueError("Jarvis task controls require an audited reason")
            action = str(arguments.get("action") or "")
            parameters = {
                key: arguments[key]
                for key in ("importance", "immediate") if key in arguments
            }
            controlled = await task_service.control(
                task.id,
                action=action,
                parameters=parameters,
                idempotency_key=f"jarvis-shell:{call_id}:control",
                requester="jarvis",
                reason=reason,
                provider_call_id=provider_call_id or call_id,
            )
            await natural_task_resolver.touch(task.id)
            return BrainResult(output={
                "title": task_title(task),
                "status": controlled.task.status,
                "command_state": controlled.command.state,
                "queue_position": controlled.queue_position,
            })

        if tool_name == "set_task_update_policy":
            # Notification preferences remain editable for completed outcomes;
            # this changes delivery metadata, never reopens task execution.
            task = await self._resolve_task(arguments, mutation=True, allow_terminal=True)
            snapshot = await task_service.set_update_policy(
                task.id,
                arguments.get("presentation_policy"),
                idempotency_key=f"jarvis-shell:{call_id}:update-policy", **preference_args,
            )
            await natural_task_resolver.touch(task.id)
            return BrainResult(output={
                "title": task_title(task),
                "task_id":task.id,
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

        if tool_name == "get_attention_checklist":
            if not session_id or generation is None:
                raise ValueError("Attention checklist requires the active voice session")
            from app.services.task_presentations import task_presentation_service

            checklist = await task_presentation_service.get_attention_checklist(
                session_id=session_id,
                generation=generation,
                cursor=str(arguments.get("cursor") or "") or None,
                limit=int(arguments.get("limit") or 20),
            )
            return BrainResult(output={
                "items": list(checklist.items),
                "total_pending": checklist.total_pending,
                "next_cursor": checklist.next_cursor,
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
            return BrainResult(output={"voice": preferences.voice, "applies_to": "next_live_session"})

        if tool_name == "start_new_jarvis_log":
            return BrainResult(
                output={"accepted": True, "message": "Starting a fresh Jarvis log."},
                control="start_new_jarvis_log",
            )

        return BrainResult(
            output={"accepted": True, "message": "Ending the voice session."},
            control="end_voice_session",
        )

    @timed("task_resolution")
    async def _resolve_task(
        self,
        arguments: dict[str, Any],
        *,
        mutation: bool,
        allow_terminal: bool = False,
    ):
        # Trusted callers and replayed historical shell events may already carry
        # an internal ID. Keep accepting it without teaching the model or user
        # to work with identifiers.
        internal_id = str(arguments.get("task_id") or "").strip()
        if internal_id:
            async with read_session_scope() as session:
                task = (await session.execute(select(Task).where(Task.id == internal_id))).scalar_one_or_none()
            if task is not None:
                if mutation and not allow_terminal and task.status not in {
                    "queued", "waiting_for_worker", "running", "waiting_for_input", "waiting_until"
                }:
                    raise ValueError("That task is no longer active.")
                return task
        reference = str(
            arguments.get("task_reference") or arguments.get("reference") or ""
        ).strip()
        result = await natural_task_resolver.resolve(
            reference, active_only=mutation and not allow_terminal
        )
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
            async with read_session_scope() as session:
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
