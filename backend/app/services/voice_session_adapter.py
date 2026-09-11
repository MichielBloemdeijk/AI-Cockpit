"""Provider-neutral live-voice boundary and deterministic fake adapter."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class VoiceProviderEvent:
    """Normalized event emitted by a realtime provider adapter."""

    event_id: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


ProviderEventHandler = Callable[[str, int, VoiceProviderEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class VoiceSessionConnection:
    """Safe connection material returned to the coordinator/browser boundary."""

    sdp_answer: str | None = None


class VoiceSessionAdapter(Protocol):
    """The coordinator-facing surface implemented by any realtime provider."""

    name: str

    async def connect(
        self,
        *,
        session_id: str,
        generation: int,
        context: dict[str, Any],
        on_event: ProviderEventHandler,
        sdp_offer: str | None = None,
    ) -> VoiceSessionConnection: ...

    async def send_user_text(
        self,
        *,
        session_id: str,
        generation: int,
        turn_id: str,
        text: str,
    ) -> None: ...

    async def send_tool_result(
        self,
        *,
        session_id: str,
        generation: int,
        call_id: str,
        output: dict[str, Any],
    ) -> None: ...

    async def create_response(
        self,
        *,
        session_id: str,
        generation: int,
        request_id: str,
        activation_id: str | None = None,
        input_item_id: str | None = None,
        response_kind: str,
        presentation_ids: tuple[str, ...],
        facts: tuple[dict[str, Any], ...],
        context: dict[str, Any],
    ) -> None: ...

    async def cancel_response(
        self,
        *,
        session_id: str,
        generation: int,
        response_id: str | None = None,
    ) -> None: ...

    async def discard_candidate_items(
        self,
        *,
        session_id: str,
        generation: int,
        item_ids: tuple[str, ...],
    ) -> None: ...

    def is_connected(self, *, session_id: str, generation: int) -> bool: ...

    async def close(
        self,
        *,
        session_id: str,
        generation: int,
        reason: str,
        emit_event: bool = True,
    ) -> None: ...


class FakeVoiceSessionAdapter:
    """In-memory provider used by tests and the developer conversation lab.

    Events still cross the same normalized callback boundary as a production
    adapter. Callers can inject exact provider timelines without a microphone,
    network connection, or model API key.
    """

    name = "fake"

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, int], ProviderEventHandler] = {}
        self._pending_text: dict[tuple[str, int], tuple[str, str]] = {}
        self._pending_tool_results: dict[tuple[str, int], dict[str, Any]] = {}
        self.discarded_candidate_items: list[tuple[str, int, tuple[str, ...]]] = []

    async def connect(
        self,
        *,
        session_id: str,
        generation: int,
        context: dict[str, Any],
        on_event: ProviderEventHandler,
        sdp_offer: str | None = None,
    ) -> VoiceSessionConnection:
        self._handlers[(session_id, generation)] = on_event
        await self.inject(
            session_id=session_id,
            generation=generation,
            event=VoiceProviderEvent(
                event_id=f"fake-ready:{session_id}:{generation}",
                kind="ready",
                payload={"context_cursors": context.get("cursors", {})},
            ),
        )
        return VoiceSessionConnection()

    async def send_user_text(
        self,
        *,
        session_id: str,
        generation: int,
        turn_id: str,
        text: str,
    ) -> None:
        key = (session_id, generation)
        if key not in self._handlers:
            raise ValueError("Fake provider session is not connected")
        self._pending_text[key] = (turn_id, text)

    async def create_response(
        self,
        *,
        session_id: str,
        generation: int,
        request_id: str,
        activation_id: str | None = None,
        input_item_id: str | None = None,
        response_kind: str,
        presentation_ids: tuple[str, ...],
        facts: tuple[dict[str, Any], ...],
        context: dict[str, Any],
    ) -> None:
        key = (session_id, generation)
        if key not in self._handlers:
            raise ValueError("Fake provider session is not connected")
        pending_text = self._pending_text.pop(key, None)
        pending_tool = self._pending_tool_results.pop(key, None)
        response_id = f"fake-response:{uuid4()}"
        await self.inject(
            session_id=session_id,
            generation=generation,
            event=VoiceProviderEvent(
                event_id=f"{response_id}:generating",
                kind="assistant_generating",
                payload={
                    "response_id": response_id,
                    "response_kind": response_kind,
                    "request_id": request_id,
                    "activation_id": activation_id,
                    "input_item_id": input_item_id,
                },
            ),
        )
        normalized = "" if pending_text is None else pending_text[1].strip()
        if normalized.lower().startswith("delegate:"):
            goal = normalized.split(":", 1)[1].strip()
            await self.inject(
                session_id=session_id,
                generation=generation,
                event=VoiceProviderEvent(
                    event_id=f"{response_id}:dispatch",
                    kind="shell_call",
                    payload={
                        "response_id": response_id,
                        "call_id": f"{response_id}:dispatch",
                        "name": "dispatch_task",
                        "arguments": {"goal": goal},
                        "presentation_ids": list(presentation_ids),
                        "response_kind": response_kind,
                    },
                ),
            )
            return
        if response_kind == "task_update":
            transcript = ""
        elif pending_tool is not None:
            transcript = self._tool_confirmation(pending_tool)
        elif normalized:
            transcript = "I understand."
        else:
            transcript = ""
        if facts:
            total_updates = max(len(facts), int(context.get("pending_update_count") or 0))
            count_prefix = f"There are {total_updates} updates. " if total_updates > 3 else ""
            transcript = f"{transcript} {count_prefix}{self._summarize_facts(facts)}".strip()
        transcript = transcript or "That's done."
        await self.inject(
            session_id=session_id,
            generation=generation,
            event=VoiceProviderEvent(
                event_id=f"{response_id}:completed",
                kind="assistant_response",
                payload={
                    "response_id": response_id,
                    "item_id": f"{response_id}:item",
                    "transcript": transcript,
                    "presentation_ids": list(presentation_ids),
                    "response_kind": response_kind,
                    "request_id": request_id,
                    "activation_id": activation_id,
                    "input_item_id": input_item_id,
                },
            ),
        )

    async def cancel_response(
        self,
        *,
        session_id: str,
        generation: int,
        response_id: str | None = None,
    ) -> None:
        key = (session_id, generation)
        if key not in self._handlers:
            return
        await self.inject(
            session_id=session_id,
            generation=generation,
            event=VoiceProviderEvent(
                event_id=f"fake-cancelled:{response_id or uuid4()}",
                kind="assistant_interrupted",
                payload={
                    "response_id": response_id or "",
                    "transcript": "",
                    "heard_transcript": "",
                    "interrupted": True,
                    "response_status": "cancelled",
                },
            ),
        )

    async def discard_candidate_items(
        self,
        *,
        session_id: str,
        generation: int,
        item_ids: tuple[str, ...],
    ) -> None:
        if item_ids:
            self.discarded_candidate_items.append((session_id, generation, item_ids))

    async def send_tool_result(
        self,
        *,
        session_id: str,
        generation: int,
        call_id: str,
        output: dict[str, Any],
    ) -> None:
        key = (session_id, generation)
        if key not in self._handlers:
            raise ValueError("Fake provider session is not connected")
        self._pending_tool_results[key] = output

    def is_connected(self, *, session_id: str, generation: int) -> bool:
        return (session_id, generation) in self._handlers

    async def close(
        self,
        *,
        session_id: str,
        generation: int,
        reason: str,
        emit_event: bool = True,
    ) -> None:
        key = (session_id, generation)
        if key not in self._handlers:
            return
        if emit_event:
            await self.inject(
                session_id=session_id,
                generation=generation,
                event=VoiceProviderEvent(
                    event_id=f"fake-disconnected:{session_id}:{generation}:{reason}",
                    kind="disconnected",
                    payload={"reason": reason},
                ),
            )
        self._handlers.pop(key, None)
        self._pending_text.pop(key, None)
        self._pending_tool_results.pop(key, None)

    async def inject(
        self,
        *,
        session_id: str,
        generation: int,
        event: VoiceProviderEvent,
    ) -> None:
        handler = self._handlers.get((session_id, generation))
        if handler is None:
            raise ValueError("Fake provider session is not connected")
        await handler(session_id, generation, event)

    def reset(self) -> None:
        """Forget process-local test connections; durable rows remain intact."""
        self._handlers.clear()
        self._pending_text.clear()
        self._pending_tool_results.clear()
        self.discarded_candidate_items.clear()

    def detach(self, *, session_id: str, generation: int) -> None:
        self._handlers.pop((session_id, generation), None)
        self._pending_text.pop((session_id, generation), None)
        self._pending_tool_results.pop((session_id, generation), None)

    @staticmethod
    def _summarize_facts(facts: tuple[dict[str, Any], ...]) -> str:
        values = [
            str(
                fact.get("required_spoken_phrase")
                or fact.get("summary")
                or fact.get("question")
                or fact.get("status")
                or "Task update"
            )
            for fact in facts
        ]
        return " ".join(value.strip() for value in values if value.strip())

    @staticmethod
    def _tool_confirmation(output: dict[str, Any]) -> str:
        if not bool(output.get("ok")):
            return "That didn't work."
        result = output.get("result") if isinstance(output.get("result"), dict) else {}
        status = str(result.get("status") or "")
        if status == "waiting_for_worker":
            return "It hasn't started; the worker is offline."
        if status == "queued":
            return "It's queued."
        if status == "cancelled":
            return "It's cancelled."
        if result.get("due_at"):
            return "Reminder set."
        if result.get("presentation_policy"):
            return "Update preference changed."
        return "That's done."


fake_voice_session_adapter = FakeVoiceSessionAdapter()


def get_voice_session_adapter(name: str) -> VoiceSessionAdapter:
    if name == fake_voice_session_adapter.name:
        return fake_voice_session_adapter
    if name == "openai":
        from app.services.openai_realtime_adapter import openai_realtime_adapter

        return openai_realtime_adapter
    raise ValueError(f"Unsupported voice session adapter: {name}")
