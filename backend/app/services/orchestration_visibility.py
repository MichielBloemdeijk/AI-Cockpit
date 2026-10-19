"""An additive rollout gate, not another attention state or inbox."""
from sqlalchemy import func, or_, select, true

from app.config import settings
from app.db.tables import OrchestrationAction, OrchestrationDecision, TaskEvent, TaskPresentation


def visible_attention(presentation=TaskPresentation):
    if not settings.task_orchestration_enabled:
        return true()
    raw_events = select(TaskEvent.id).where(TaskEvent.event_type.in_(
        ["task.requires_input", "task.failed", "task.interrupted"],
    ))
    # Trust durable backend receipts, never a marker in worker/model facts.
    # This also recognizes pre-rollout judged candidates without rewriting them.
    sources = func.json_each(OrchestrationDecision.source_event_ids_json).table_valued("value")
    retained = select(OrchestrationAction.id).join(
        OrchestrationDecision, OrchestrationDecision.id == OrchestrationAction.decision_id,
    ).where(
        OrchestrationDecision.task_id == presentation.task_id,
        OrchestrationAction.outcome_json["retained"].as_boolean().is_(True),
        select(sources.c.value).where(
            sources.c.value == presentation.source_task_event_id,
        ).correlate(OrchestrationDecision, presentation).exists(),
    ).correlate(presentation).exists()
    return or_(presentation.source_task_event_id.is_(None),
               presentation.source_task_event_id.not_in(raw_events),
               retained)


def live_context_visible(presentation=TaskPresentation):
    """Verified terminal facts and task questions do not wait for a worker-action decision.

    A user in an active conversation is the best person to answer a task's question,
    so Live may ask it right away; background orchestration can still decide in
    parallel and whichever answer arrives first resolves the question. Remediation
    authority and away notification routing keep their existing orchestration gate.
    """
    return or_(presentation.candidate_type.in_(["terminal_result", "question"]), visible_attention(presentation))
