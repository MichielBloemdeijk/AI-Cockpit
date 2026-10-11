"""Durable timing/usage metadata. No prompts, arguments, credentials or prices."""
from __future__ import annotations

from datetime import datetime, timezone
import math

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert

from app.db.tables import OrchestrationMetric


def timestamp() -> datetime:
    return datetime.now(timezone.utc)


def age_ms(value: datetime, *, at: datetime | None = None) -> float:
    return max(0.0, ((at or timestamp()) - value.replace(tzinfo=timezone.utc)).total_seconds() * 1000)


async def record(session, *, key: str, kind: str, values: dict, duration_ms: float | None = None,
                 decision_id: str | None = None, generation: int | None = None,
                 task_id: str | None = None, source_sequence: int | None = None, replace: bool = False) -> None:
    statement = insert(OrchestrationMetric).values(metric_key=key, kind=kind, values_json=values,
        duration_ms=max(0.0, duration_ms) if duration_ms is not None else None,
        decision_id=decision_id, claim_generation=generation, task_id=task_id,
        source_sequence=source_sequence, created_at=timestamp())
    statement = statement.on_conflict_do_update(index_elements=["metric_key"], set_={
        "values_json": values, "created_at": timestamp(),
    }) if replace else statement.on_conflict_do_nothing(index_elements=["metric_key"])
    await session.execute(statement)


def token_usage(payload: dict, *, thread_id: str, turn_id: str) -> dict | None:
    if payload.get("threadId") != thread_id or payload.get("turnId") != turn_id:
        return None
    usage = payload.get("tokenUsage")
    if not isinstance(usage, dict):
        return None
    fields = {"totalTokens", "inputTokens", "cachedInputTokens", "cacheWriteInputTokens", "outputTokens", "reasoningOutputTokens"}
    result = {}
    for name in ("total", "last"):
        breakdown = usage.get(name)
        if not isinstance(breakdown, dict):
            return None
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for key, value in breakdown.items() if key in fields and value is not None):
            return None
        result[name] = {key: breakdown[key] for key in fields if isinstance(breakdown.get(key), int)}
    if not result["total"]:
        return None
    # A snapshot replaces the previous one; cumulative usage is never added
    # once per notification. Pricing/account-tier data is not guessed.
    return {**result, "cost_usd": None, "cost_status": "provider_cost_unavailable"}


async def snapshot(session, *, limit: int = 100) -> dict:
    counts = dict((await session.execute(select(OrchestrationMetric.kind, func.count())
                                         .group_by(OrchestrationMetric.kind))).all())
    rows = list((await session.execute(select(OrchestrationMetric).order_by(OrchestrationMetric.id.desc()).limit(limit))).scalars())
    kinds = {row.kind for row in rows}
    durations = {}
    for kind in kinds:
        samples = sorted(row.duration_ms for row in rows if row.kind == kind and row.duration_ms is not None)
        if samples:
            durations[kind] = {"sample_count": len(samples), "max_ms": max(samples),
                               "p95_ms": samples[math.ceil(0.95 * len(samples)) - 1]}
    action_counts = dict((await session.execute(select(
        OrchestrationMetric.values_json["outcome"].as_string(), func.count(),
    ).where(OrchestrationMetric.kind == "action_attempt")
        .group_by(OrchestrationMetric.values_json["outcome"].as_string()))).all())
    usage_rows = (await session.execute(select(OrchestrationMetric.values_json).where(
        OrchestrationMetric.kind == "native_usage",
    ))).scalars()
    total_usage = {}
    observed = 0
    for value in usage_rows:
        observed += 1
        for key, amount in value.get("total", {}).items():
            total_usage[key] = total_usage.get(key, 0) + amount
    return {"counts": counts, "action_outcomes": action_counts,
            "recent_duration_samples": durations, "sample_window": limit,
            "native_usage": {"observed_turns": observed, "totals": total_usage,
                             "cost_usd": None, "cost_status": "provider_cost_unavailable"}}
