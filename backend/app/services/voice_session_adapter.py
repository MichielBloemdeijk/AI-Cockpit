"""Provider-neutral live-voice boundary and deterministic fake adapter."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


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

    async def judge_presentation_timing(
        self,
        *,
        session_id: str,
        generation: int,
        facts: tuple[dict[str, Any], ...],
    ) -> str: ...

    async def present_task_updates(
        self,
        *,
        session_id: str,
        generation: int,
        presentation_ids: tuple[str, ...],
        facts: tuple[dict[str, Any], ...],
    ) -> None: ...

    async def fold_task_updates(
        self,
        *,
        session_id: str,
        generation: int,
        presentation_ids: tuple[str, ...],
        facts: tuple[dict[str, Any], ...],
    ) -> None: ...

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
        self._timing_decisions: list[str] = []
        self._folded_updates: dict[
            tuple[str, int], tuple[tuple[str, ...], tuple[dict[str, Any], ...]]
        ] = {}

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
        response_id = f"fake-response:{turn_id}"
        await self.inject(
            session_id=session_id,
            generation=generation,
            event=VoiceProviderEvent(
                event_id=f"{response_id}:generating",
                kind="assistant_generating",
                payload={"response_id": response_id},
            ),
        )
        normalized = text.strip()
        if normalized.lower().startswith("delegate:"):
            goal = normalized.split(":", 1)[1].strip()
            await self.inject(
                session_id=session_id,
                generation=generation,
                event=VoiceProviderEvent(
                    event_id=f"{response_id}:dispatch",
                    kind="shell_call",
                    payload={
                        "call_id": f"{response_id}:dispatch",
                        "name": "dispatch_task",
                        "arguments": {"goal": goal},
                    },
                ),
            )
            transcript = "I've started that in the background."
        else:
            transcript = f"I heard: {text}"
        folded = self._folded_updates.pop((session_id, generation), None)
        presentation_ids = folded[0] if folded else ()
        folded_facts = folded[1] if folded else ()
        if folded_facts:
            transcript = f"{transcript} {self._summarize_facts(folded_facts)}".strip()
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
                },
            ),
        )

    async def send_tool_result(
        self,
        *,
        session_id: str,
        generation: int,
        call_id: str,
        output: dict[str, Any],
    ) -> None:
        # The fake adapter emits its deterministic acknowledgement before the
        # shell call. Keeping this a no-op still exercises the shared method.
        if (session_id, generation) not in self._handlers:
            raise ValueError("Fake provider session is not connected")

    async def judge_presentation_timing(
        self,
        *,
        session_id: str,
        generation: int,
        facts: tuple[dict[str, Any], ...],
    ) -> str:
        if (session_id, generation) not in self._handlers:
            raise ValueError("Fake provider session is not connected")
        return self._timing_decisions.pop(0) if self._timing_decisions else "PRESENT"

    async def present_task_updates(
        self,
        *,
        session_id: str,
        generation: int,
        presentation_ids: tuple[str, ...],
        facts: tuple[dict[str, Any], ...],
    ) -> None:
        response_id = f"fake-presentation:{presentation_ids[0]}"
        await self.inject(
            session_id=session_id,
            generation=generation,
            event=VoiceProviderEvent(
                event_id=f"{response_id}:generating",
                kind="assistant_generating",
                payload={"response_id": response_id},
            ),
        )
        await self.inject(
            session_id=session_id,
            generation=generation,
            event=VoiceProviderEvent(
                event_id=f"{response_id}:completed",
                kind="assistant_response",
                payload={
                    "response_id": response_id,
                    "item_id": f"{response_id}:item",
                    "transcript": self._summarize_facts(facts),
                    "presentation_ids": list(presentation_ids),
                },
            ),
        )

    async def fold_task_updates(
        self,
        *,
        session_id: str,
        generation: int,
        presentation_ids: tuple[str, ...],
        facts: tuple[dict[str, Any], ...],
    ) -> None:
        if (session_id, generation) not in self._handlers:
            raise ValueError("Fake provider session is not connected")
        existing_ids, existing_facts = self._folded_updates.get(
            (session_id, generation),
            ((), ()),
        )
        self._folded_updates[(session_id, generation)] = (
            existing_ids + presentation_ids,
            existing_facts + facts,
        )

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
        self._folded_updates.pop(key, None)

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
        self._timing_decisions.clear()
        self._folded_updates.clear()

    def detach(self, *, session_id: str, generation: int) -> None:
        self._handlers.pop((session_id, generation), None)
        self._folded_updates.pop((session_id, generation), None)

    def queue_timing_decision(self, decision: str) -> None:
        """Queue one deterministic policy response for a test or developer probe."""
        self._timing_decisions.append(decision)

    @staticmethod
    def _summarize_facts(facts: tuple[dict[str, Any], ...]) -> str:
        values = [
            str(fact.get("summary") or fact.get("question") or fact.get("status") or "Task update")
            for fact in facts
        ]
        return " ".join(value.strip() for value in values if value.strip())


fake_voice_session_adapter = FakeVoiceSessionAdapter()


def get_voice_session_adapter(name: str) -> VoiceSessionAdapter:
    if name == fake_voice_session_adapter.name:
        return fake_voice_session_adapter
    if name == "openai":
        from app.services.openai_realtime_adapter import openai_realtime_adapter

        return openai_realtime_adapter
    raise ValueError(f"Unsupported voice session adapter: {name}")
