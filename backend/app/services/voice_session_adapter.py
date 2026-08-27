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
    ) -> None: ...

    async def send_user_text(
        self,
        *,
        session_id: str,
        generation: int,
        turn_id: str,
        text: str,
    ) -> None: ...

    async def close(self, *, session_id: str, generation: int, reason: str) -> None: ...


class FakeVoiceSessionAdapter:
    """In-memory provider used by tests and the developer conversation lab.

    Events still cross the same normalized callback boundary as a production
    adapter. Callers can inject exact provider timelines without a microphone,
    network connection, or model API key.
    """

    name = "fake"

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, int], ProviderEventHandler] = {}

    async def connect(
        self,
        *,
        session_id: str,
        generation: int,
        context: dict[str, Any],
        on_event: ProviderEventHandler,
    ) -> None:
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
                },
            ),
        )

    async def close(self, *, session_id: str, generation: int, reason: str) -> None:
        key = (session_id, generation)
        if key not in self._handlers:
            return
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

    def detach(self, *, session_id: str, generation: int) -> None:
        self._handlers.pop((session_id, generation), None)


fake_voice_session_adapter = FakeVoiceSessionAdapter()


def get_voice_session_adapter(name: str) -> VoiceSessionAdapter:
    if name == fake_voice_session_adapter.name:
        return fake_voice_session_adapter
    raise ValueError(f"Unsupported voice session adapter: {name}")
