"""Replaceable policy boundary for deciding when Jarvis should present task facts."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.config import settings


PresentationDecision = Literal["PRESENT", "FOLD", "DEFER"]


class TimingJudge(Protocol):
    async def judge_presentation_timing(
        self,
        *,
        session_id: str,
        generation: int,
        facts: tuple[dict[str, Any], ...],
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class TimingPolicyResult:
    decision: PresentationDecision
    reason: str


class UpdateTimingPolicy:
    """Validate provider judgement and fail closed to a deferred delivery."""

    async def decide(
        self,
        *,
        judge: TimingJudge,
        session_id: str,
        generation: int,
        facts: tuple[dict[str, Any], ...],
    ) -> TimingPolicyResult:
        try:
            raw = await asyncio.wait_for(
                judge.judge_presentation_timing(
                    session_id=session_id,
                    generation=generation,
                    facts=facts,
                ),
                timeout=max(0.1, settings.jarvis_presentation_timing_timeout_seconds),
            )
        except TimeoutError:
            return TimingPolicyResult("DEFER", "timing_judgement_timeout")
        except Exception as exc:
            return TimingPolicyResult("DEFER", f"timing_judgement_failed:{type(exc).__name__}")

        normalized = raw.strip()
        if normalized not in {"PRESENT", "FOLD", "DEFER"}:
            return TimingPolicyResult("DEFER", "invalid_timing_judgement")
        return TimingPolicyResult(normalized, "provider_judgement")  # type: ignore[arg-type]


update_timing_policy = UpdateTimingPolicy()
