"""Heuristic extraction of proposed knowledge from durable conversations."""
from __future__ import annotations

from dataclasses import dataclass
import re

from app.services.conversation_store import MessageRecord


@dataclass(slots=True)
class MemoryProposalDraft:
    scope: str
    kind: str
    title: str
    content: str
    confidence: float
    source_event_id: str | None


_PREFERENCE_PATTERNS = [
    re.compile(r"\bI prefer (?P<value>[^.!?]+)", re.IGNORECASE),
    re.compile(r"\bI like (?P<value>[^.!?]+)", re.IGNORECASE),
    re.compile(r"\bplease (?P<value>[^.!?]+)", re.IGNORECASE),
    re.compile(r"\bmy default(?: is| should be)? (?P<value>[^.!?]+)", re.IGNORECASE),
]
_DECISION_PATTERNS = [
    re.compile(r"\b(?:we|I) decided to (?P<value>[^.!?]+)", re.IGNORECASE),
    re.compile(r"\bdecision:? (?P<value>[^.!?]+)", re.IGNORECASE),
    re.compile(r"\bwe should (?P<value>[^.!?]+)", re.IGNORECASE),
]
_PROJECT_CONTEXT_PATTERNS = [
    re.compile(r"\bthis (?:project|repo|app) (?P<value>[^.!?]+)", re.IGNORECASE),
    re.compile(r"\bthe (?:frontend|backend|architecture) (?P<value>[^.!?]+)", re.IGNORECASE),
    re.compile(r"\bphase \d+(?::| -)? (?P<value>[^.!?]+)", re.IGNORECASE),
]


def _slice_messages(
    messages: list[MessageRecord],
    *,
    start_message_id: str | None,
    end_message_id: str | None,
) -> list[MessageRecord]:
    if not messages:
        return []
    start_index = 0
    end_index = len(messages) - 1
    if start_message_id is not None:
        start_index = next((index for index, message in enumerate(messages) if message.id == start_message_id), start_index)
    if end_message_id is not None:
        end_index = next((index for index, message in enumerate(messages) if message.id == end_message_id), end_index)
    if end_index < start_index:
        return []
    return messages[start_index:end_index + 1]


def _build_title(prefix: str, value: str) -> str:
    normalized = " ".join(value.split())
    return f"{prefix}: {normalized[:60]}"


def _proposal_from_match(
    *,
    message: MessageRecord,
    scope: str,
    kind: str,
    prefix: str,
    value: str,
    confidence: float,
) -> MemoryProposalDraft:
    normalized = " ".join(value.split())
    return MemoryProposalDraft(
        scope=scope,
        kind=kind,
        title=_build_title(prefix, normalized),
        content=normalized,
        confidence=confidence,
        source_event_id=message.source_event_id,
    )


def extract_memory_proposals(
    messages: list[MessageRecord],
    *,
    start_message_id: str | None = None,
    end_message_id: str | None = None,
) -> list[MemoryProposalDraft]:
    proposals: list[MemoryProposalDraft] = []
    seen_keys: set[tuple[str, str]] = set()

    for message in _slice_messages(
        messages,
        start_message_id=start_message_id,
        end_message_id=end_message_id,
    ):
        content = message.content.strip()
        if not content:
            continue

        for pattern in _PREFERENCE_PATTERNS:
            match = pattern.search(content)
            if match:
                value = match.group("value").strip()
                key = ("preference", value.lower())
                if key not in seen_keys:
                    seen_keys.add(key)
                    proposals.append(
                        _proposal_from_match(
                            message=message,
                            scope="preference",
                            kind="preference",
                            prefix="Preference",
                            value=value,
                            confidence=0.88,
                        )
                    )

        for pattern in _DECISION_PATTERNS:
            match = pattern.search(content)
            if match:
                value = match.group("value").strip()
                key = ("decision", value.lower())
                if key not in seen_keys:
                    seen_keys.add(key)
                    proposals.append(
                        _proposal_from_match(
                            message=message,
                            scope="decision",
                            kind="decision",
                            prefix="Decision",
                            value=value,
                            confidence=0.78,
                        )
                    )

        for pattern in _PROJECT_CONTEXT_PATTERNS:
            match = pattern.search(content)
            if match:
                value = match.group("value").strip()
                key = ("note", value.lower())
                if key not in seen_keys:
                    seen_keys.add(key)
                    proposals.append(
                        _proposal_from_match(
                            message=message,
                            scope="note",
                            kind="lesson",
                            prefix="Project context",
                            value=value,
                            confidence=0.62,
                        )
                    )

    return proposals[:8]
