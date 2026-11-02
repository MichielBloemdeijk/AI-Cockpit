"""Optional observer of Live provider traffic and response-gate decisions.

The self-test recorder sets one on the adapter and the coordinator; production
leaves both unset. Observers must not raise or block: they run inline.
"""
from __future__ import annotations

from typing import Any, Protocol


class LiveTrafficObserver(Protocol):
    def connect_requested(self, *, session_id: str, generation: int) -> None: ...

    def connect_failed(self, *, session_id: str, generation: int, error: Exception) -> None: ...

    def session_configured(self, configuration: dict[str, Any]) -> None: ...

    def sent(self, *, session_id: str, generation: int, payload: dict[str, Any]) -> None: ...

    def received(self, *, session_id: str, generation: int, event: dict[str, Any]) -> None: ...

    def response_gate(self, event_type: str, *, session_id: str, generation: int, reason: str,
                      detail: dict[str, Any] | None) -> None: ...
