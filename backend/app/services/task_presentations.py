"""Deterministic task-fact coalescing and generation-bound presentation."""
from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import aliased
from app.config import settings

from app.db.repositories.jarvis import JarvisDomainRepository
from app.db.session import session_scope
from app.db.tables import (
    NotificationOutbox,
    JarvisLiveEvent,
    LiveSession,
    Task,
    TaskEvent,
    TaskPresentation,
    TaskProjectionCheckpoint,
    TaskQuestion,
    LiveSessionEvent,
)
from app.services.task_notifications import enqueue_task_notification
from app.services.task_error_messages import public_failure_summary
from app.services.runtime_health import runtime_health_service
from app.services.task_update_policy import FOLLOW_PROGRESS, SILENT, normalize_task_update_policy
from app.services.orchestration_visibility import visible_attention, live_context_visible
from app.services.task_notification_preferences import current as current_notification


PRESENTABLE_EVENT_TYPES = {
    "task.progress": "progress",
    "task.requires_input": "question",
    "task.completed": "terminal_result",
    "task.failed": "terminal_result",
    "task.interrupted": "terminal_result",
    "task.cancelled": "terminal_result",
    "task.reminder.due": "reminder",
}
ATTENTION_STATES = {"pending", "handled", "superseded"}
UNFINISHED_PRESENTATION_STATES = {"pending"}
DEFAULT_LEASE_SECONDS = 30
MAX_APPEND_TOKENS = 500
# Every model token contains at least one UTF-8 byte. Keeping the serialized
# append at or below this byte ceiling is therefore a conservative, explicit
# upper bound of MAX_APPEND_TOKENS even without a model-specific tokenizer.
# Reserve room for the short routing sentence wrapped around the JSON by each
# provider adapter, so the complete append remains under 500 UTF-8 bytes.
MAX_APPEND_UTF8_BYTES = 400


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class PresentationReservation:
    presentation_ids: tuple[str, ...]
    facts: tuple[dict, ...]
    total_pending: int
    exposure_tokens: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AttentionChecklist:
    items: tuple[dict, ...]
    next_cursor: str | None
    total_pending: int = 0


@dataclass(frozen=True, slots=True)
class AttentionContextUpdate:
    items: tuple[dict, ...]
    total_pending: int
    next_cursor: str | None
    deltas: tuple[dict, ...] = ()


class TaskPresentationService:
    _PROJECTION_KEY = "task_attention"
    _BATCH_SIZE = 250

    def __init__(self) -> None:
        self._projection_lock = asyncio.Lock()
        self._projection_detail = "Awaiting the first projection pass."

    async def recover_reservations(self) -> int:
        async with session_scope() as session:
            recovered = await session.execute(
                update(TaskPresentation)
                .where(TaskPresentation.state == "pending")
                .where(TaskPresentation.lease_token.is_not(None))
                .values(
                    reserved_session_id=None,
                    reserved_generation=None,
                    reserved_at=None,
                    lease_token=None,
                    defer_until=None,
                    retry_count=TaskPresentation.retry_count + 1,
                    last_error="process_restarted",
                    updated_at=utc_now(),
                )
            )
            return int(recovered.rowcount or 0)

    async def sync_candidates(self) -> int:
        try:
            created = await self._sync_candidates()
            await runtime_health_service.healthy(
                "task_projector",
                detail=self._projection_detail,
            )
            return created
        except Exception as exc:
            await runtime_health_service.failed("task_projector", exc)
            raise

    async def _sync_candidates(self) -> int:
        """Incrementally project every committed task event exactly once."""
        if settings.task_orchestration_enabled:
            # One feedback owner, including when voice is connected. This
            # performs only code/DB work and never waits for the decision loop.
            from app.services.task_orchestration import task_orchestration_service
            return await task_orchestration_service.intake()
        created = 0
        async with self._projection_lock:
            target_sequence: int | None = None
            while True:
                async with session_scope() as session:
                    checkpoint = await session.get(TaskProjectionCheckpoint, self._PROJECTION_KEY)
                    if checkpoint is None:
                        checkpoint = TaskProjectionCheckpoint(
                            projection_key=self._PROJECTION_KEY,
                            last_sequence=0,
                        )
                        session.add(checkpoint)
                    if target_sequence is None:
                        target_sequence = int((await session.execute(
                            select(func.max(TaskEvent.sequence))
                        )).scalar_one_or_none() or 0)
                    if checkpoint.last_sequence >= target_sequence:
                        self._projection_detail = (
                            f"Caught up through task event {checkpoint.last_sequence}."
                        )
                        return created
                    events = list((await session.execute(
                        select(TaskEvent)
                        .where(TaskEvent.sequence > checkpoint.last_sequence)
                        .where(TaskEvent.sequence <= target_sequence)
                        .order_by(TaskEvent.sequence.asc())
                        .limit(self._BATCH_SIZE)
                    )).scalars().all())
                    if not events:
                        checkpoint.last_sequence = target_sequence
                        checkpoint.updated_at = utc_now()
                        self._projection_detail = (
                            f"Caught up through task event {target_sequence}."
                        )
                        return created
                    created += await self._project_batch(session, events)
                    checkpoint.last_sequence = events[-1].sequence
                    checkpoint.updated_at = utc_now()

    async def _project_batch(self, session, events: list[TaskEvent], *, judged_event_ids: frozenset[str] = frozenset()) -> int:
        # Preserve the canonical candidates while the receipt-based visibility
        # gate holds them. Disablement must not lose skipped worker questions.
        relevant = [
            event for event in events
            if (event.event_type in PRESENTABLE_EVENT_TYPES or event.event_type == "task.question.answered")
        ]
        if not relevant:
            return 0
        task_ids = {event.task_id for event in relevant}
        tasks = {
            task.id: task
            for task in (await session.execute(select(Task).where(Task.id.in_(task_ids)))).scalars()
        }
        presentations = list((await session.execute(
            select(TaskPresentation).where(TaskPresentation.task_id.in_(task_ids))
        )).scalars().all())
        by_event = {
            item.source_task_event_id: item
            for item in presentations
            if item.source_task_event_id
        }
        by_task: dict[str, list[TaskPresentation]] = {}
        for item in presentations:
            by_task.setdefault(item.task_id, []).append(item)

        question_ids = {
            str((event.payload_json or {}).get("question_id") or "")
            for event in relevant
            if event.event_type == "task.requires_input"
        }
        questions = {
            item.id: item
            for item in (
                (await session.execute(select(TaskQuestion).where(TaskQuestion.id.in_(question_ids)))).scalars()
                if question_ids else []
            )
        }
        notification_event_ids = {
            item.source_task_event_id for item in presentations if item.source_task_event_id
        } | {event.id for event in relevant}
        notifications = list((await session.execute(
            select(NotificationOutbox).where(NotificationOutbox.task_event_id.in_(notification_event_ids))
        )).scalars().all()) if notification_event_ids else []
        notifications_by_event: dict[str, list[NotificationOutbox]] = {}
        for item in notifications:
            if item.task_event_id:
                notifications_by_event.setdefault(item.task_event_id, []).append(item)

        async def supersede_notification(event_id: str | None) -> None:
            if not event_id:
                return
            for row in notifications_by_event.get(event_id, []):
                if row.status in {"queued", "failed"}:
                    row.status = "superseded"
                    row.next_attempt_at = None
            await session.execute(
                update(NotificationOutbox)
                .where(NotificationOutbox.task_event_id == event_id)
                .where(NotificationOutbox.status.in_(["queued", "failed"]))
                .values(status="superseded", next_attempt_at=None)
            )

        async def supersede(candidate: TaskPresentation) -> None:
            candidate.state = "superseded"
            candidate.reserved_session_id = None
            candidate.reserved_generation = None
            candidate.reserved_at = None
            candidate.lease_token = None
            await supersede_notification(candidate.source_task_event_id)
            active_sessions = list((await session.execute(
                select(LiveSession)
                .where(LiveSession.lifecycle_state.in_(["connecting", "ready"]))
            )).scalars().all())
            domain = JarvisDomainRepository(session)
            for realtime in active_sessions:
                await domain.append_session_event_once(
                    session_id=realtime.id,
                    event_type="attention.superseded",
                    payload_json={
                        "presentation_id": candidate.id,
                        "source_task_event_id": candidate.source_task_event_id,
                        "state": "superseded",
                        "session_id": realtime.id,
                        "generation": realtime.generation,
                    },
                    idempotency_key=(
                        f"attention-superseded:{realtime.id}:{realtime.generation}:{candidate.id}"
                    ),
                )

        created = 0
        for event in relevant:
            if event.event_type == "task.question.answered":
                question_id = str((event.payload_json or {}).get("question_id") or "")
                for candidate in presentations:
                    if (
                        candidate.candidate_type == "question"
                        and candidate.state in UNFINISHED_PRESENTATION_STATES
                        and str((candidate.required_facts_json or {}).get("question_id") or "") == question_id
                    ):
                        await supersede(candidate)
                continue

            task = tasks.get(event.task_id)
            if task is None:
                continue
            candidate_type = PRESENTABLE_EVENT_TYPES[event.event_type]
            existing = by_event.get(event.id)
            if event.event_type == "task.reminder.due":
                cutoff = int((await session.execute(
                    text(
                        "SELECT task_event_cutoff FROM attention_migration_state "
                        "WHERE id = 'current'"
                    )
                )).scalar_one_or_none() or 0)
                if cutoff and event.sequence <= cutoff:
                    if existing is None:
                        retired = TaskPresentation(
                            id=str(uuid4()),
                            task_id=task.id,
                            candidate_type=candidate_type,
                            coalescing_key=f"task:{task.id}:{candidate_type}",
                            importance=task.importance,
                            state="handled",
                            source_task_event_id=event.id,
                            required_facts_json=self._facts(event, task),
                            handled_at=utc_now(),
                            handled_by="migration_legacy_retired",
                            last_error="legacy_attention_retired_at_migration",
                            idempotency_key=f"task-event-presentation:{event.id}",
                        )
                        session.add(retired)
                        retired.handled_event_id = await self._append_handled_audit(
                            session,
                            retired,
                            handled_by="migration_legacy_retired",
                            idempotency_key=f"attention-migration-retired:{retired.id}",
                            payload={"reason": "projector_lag_cutoff"},
                        )
                        presentations.append(retired)
                        by_event[event.id] = retired
                        by_task.setdefault(task.id, []).append(retired)
                    continue
            policy = normalize_task_update_policy(task.presentation_policy)
            if task.presentation_policy != policy:
                task.presentation_policy = policy
            if not self._policy_allows_candidate(candidate_type, policy):
                if existing is not None and existing.state in UNFINISHED_PRESENTATION_STATES:
                    await supersede(existing)
                continue

            facts = self._facts(event, task)
            if candidate_type == "terminal_result":
                facts["notification"] = await current_notification(session,task)
            question = questions.get(str((event.payload_json or {}).get("question_id") or ""))
            if existing is not None:
                existing.required_facts_json = facts
                existing.importance = task.importance
                await enqueue_task_notification(session, task=task, event=event, question=question)
                continue

            task_presentations = by_task.setdefault(task.id, [])
            if candidate_type == "terminal_result":
                for candidate in task_presentations:
                    if (
                        candidate.candidate_type in {"progress", "terminal_result"}
                        and candidate.state in UNFINISHED_PRESENTATION_STATES
                    ):
                        await supersede(candidate)
            elif candidate_type == "progress":
                if any(
                    candidate.candidate_type == "terminal_result"
                    and candidate.state in {"pending"}
                    for candidate in task_presentations
                ):
                    continue
                for candidate in task_presentations:
                    if candidate.candidate_type == "progress" and candidate.state in UNFINISHED_PRESENTATION_STATES:
                        await supersede(candidate)

            candidate = TaskPresentation(
                id=str(uuid4()),
                task_id=task.id,
                candidate_type=candidate_type,
                coalescing_key=f"task:{task.id}:{candidate_type}",
                importance=task.importance,
                state="pending",
                source_task_event_id=event.id,
                required_facts_json=facts,
                idempotency_key=f"task-event-presentation:{event.id}",
            )
            session.add(candidate)
            presentations.append(candidate)
            task_presentations.append(candidate)
            by_event[event.id] = candidate
            await enqueue_task_notification(session, task=task, event=event, question=question)
            held = (settings.task_orchestration_enabled
                    and event.event_type in {"task.requires_input", "task.failed", "task.interrupted"}
                    and event.id not in judged_event_ids)
            created += int(not held)
        return created

    @staticmethod
    def _policy_allows_candidate(candidate_type: str, policy: str) -> bool:
        return candidate_type == "terminal_result" or (policy != SILENT and (candidate_type != "progress" or policy == FOLLOW_PROGRESS))

    async def list_pending(self, *, include_terminal_context: bool = False) -> tuple[TaskPresentation, ...]:
        async with session_scope() as session:
            result = await session.execute(
                select(TaskPresentation)
                .where(TaskPresentation.state == "pending")
                .where(live_context_visible() if include_terminal_context else visible_attention())
                .order_by(
                    TaskPresentation.created_at.asc(),
                    TaskPresentation.id.asc(),
                )
            )
            return tuple(sorted(result.scalars().all(), key=self._delivery_priority))

    @classmethod
    def semantic_fingerprint(cls, candidates: tuple[TaskPresentation, ...]) -> str:
        """Hash only facts that change the model's authoritative attention view."""
        payload = [
            {
                "id": item.id,
                "state": item.state,
                "source_task_event_id": item.source_task_event_id,
                "importance": item.importance,
                "facts": item.required_facts_json or {},
                "handled_at": None if item.handled_at is None else item.handled_at.isoformat(),
                "handled_by": item.handled_by,
                "handled_event_id": item.handled_event_id,
            }
            for item in sorted(candidates, key=cls._delivery_priority)
        ]
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def semantic_item_fingerprint(candidate: TaskPresentation) -> str:
        payload = {
            "id": candidate.id,
            "state": candidate.state,
            "source_task_event_id": candidate.source_task_event_id,
            "importance": candidate.importance,
            "facts": candidate.required_facts_json or {},
            "handled_at": (
                None if candidate.handled_at is None else candidate.handled_at.isoformat()
            ),
            "handled_by": candidate.handled_by,
            "handled_event_id": candidate.handled_event_id,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    async def build_attention_context(
        self,
        *,
        session_id: str,
        generation: int,
        limit: int = 20,
    ) -> AttentionContextUpdate:
        """Build the bounded checklist that will actually be supplied to a model."""
        async with session_scope() as session:
            return await self.build_attention_context_in_session(
                session,
                session_id=session_id,
                generation=generation,
                limit=limit,
            )

    async def build_attention_context_in_session(
        self,
        session,
        *,
        session_id: str,
        generation: int,
        limit: int = 20,
    ) -> AttentionContextUpdate:
        realtime = await session.get(LiveSession, session_id)
        if (
            realtime is None
            or realtime.generation != generation
            or realtime.lifecycle_state not in {"connecting", "ready"}
        ):
            raise ValueError("Live session generation is stale")
        all_rows = list((await session.execute(
            select(TaskPresentation).where(TaskPresentation.state == "pending").where(
                live_context_visible() if realtime.provider_adapter == "openai_live" else visible_attention())
        )).scalars().all())
        ordered = sorted(all_rows, key=self._delivery_priority)
        bounded_limit = min(max(int(limit or 20), 1), 50)
        rows = ordered[:bounded_limit]
        domain = JarvisDomainRepository(session)
        items: list[dict] = []
        for row in rows:
            exposure = await domain.append_session_event_once(
                session_id=session_id,
                event_type="attention.context_exposed",
                payload_json={
                    "presentation_id": row.id,
                    "source_task_event_id": row.source_task_event_id,
                    "session_id": session_id,
                    "generation": generation,
                },
                idempotency_key=f"attention-exposed:{session_id}:{generation}:{row.id}",
            )
            facts = self._context_fact(
                row,
                exposure_token=exposure.event.id,
                commentary=False,
            )
            items.append({
                "presentation_id": row.id,
                "task_id": row.task_id,
                "summary": self._checklist_summary(facts),
                "facts": facts,
                "priority": row.importance,
                "exposure_token": exposure.event.id,
                "state": row.state,
            })
        return AttentionContextUpdate(
            items=tuple(items),
            total_pending=len(ordered),
            next_cursor=(
                self._encode_checklist_cursor(rows[-1])
                if len(ordered) > len(rows) and rows
                else None
            ),
        )

    async def reserve(
        self,
        *,
        session_id: str,
        generation: int,
        user_owns_floor: bool,
        limit: int = 3,
    ) -> PresentationReservation | None:
        await self.sync_candidates()
        async with session_scope() as session:
            return await self.reserve_in_session(
                session,
                session_id=session_id,
                generation=generation,
                user_owns_floor=user_owns_floor,
                limit=limit,
            )

    async def reserve_in_session(
        self,
        session,
        *,
        session_id: str,
        generation: int,
        user_owns_floor: bool,
        limit: int = 3,
        commentary: bool = False,
        exclude_presentation_ids: frozenset[str] = frozenset(),
        terminal_only: bool = False,
        quiet_context: bool = False,
        conversation_idle: bool = False,
    ) -> PresentationReservation | None:
        """Claim one short transport lease without changing attention state."""
        if user_owns_floor and not quiet_context:
            return None
        realtime = await session.get(LiveSession, session_id)
        if (
            realtime is None
            or realtime.generation != generation
            or realtime.lifecycle_state != "ready"
        ):
            raise ValueError("Live session generation is stale")
        now = utc_now()
        # Expired or newly held leases are transport state, not attention.
        # Release hidden leases before claiming: the database enforces one
        # pending transport lease per session, including invisible candidates.
        await session.execute(
            update(TaskPresentation)
            .where(TaskPresentation.state == "pending")
            .where(TaskPresentation.lease_token.is_not(None))
            .where(or_(
                TaskPresentation.reserved_at < now - timedelta(seconds=DEFAULT_LEASE_SECONDS),
                ~(live_context_visible() if realtime.provider_adapter == "openai_live" else visible_attention()),
            ))
            .values(
                lease_token=None,
                reserved_session_id=None,
                reserved_generation=None,
                reserved_at=None,
            )
        )
        result = await session.execute(
            select(TaskPresentation)
            .where(TaskPresentation.state == "pending")
            .where(TaskPresentation.lease_token.is_(None))
            .where(live_context_visible() if realtime.provider_adapter == "openai_live" else visible_attention())
            .where(TaskPresentation.candidate_type == "terminal_result" if terminal_only or quiet_context else True)
            .order_by(
                TaskPresentation.created_at.asc(),
                TaskPresentation.id.asc(),
            )
        )
        pending = sorted(result.scalars().all(), key=self._delivery_priority)
        # A voice session can have at most one proactive append lease. The
        # checklist can still expose all pending items independently.
        candidates = [
            candidate for candidate in pending
            if candidate.id not in exclude_presentation_ids
            # A spoken-update prompt while the user is talking makes Live talk over
            # them; hold it until the next opening (Live's next reply re-queues it).
            and not (user_owns_floor and commentary and candidate.candidate_type == "terminal_result"
                and self._requires_commentary(candidate) and not self._urgent(candidate))
        ][: min(max(1, limit), 1)]
        if not candidates:
            return None
        facts: list[dict] = []
        claimed_ids: list[str] = []
        exposure_tokens: list[str] = []
        domain = JarvisDomainRepository(session)
        lease_owner = aliased(TaskPresentation)
        for candidate in candidates:
            lease_token = str(uuid4())
            commentary_attempt = commentary and (self._requires_commentary(candidate)
                or (conversation_idle and self._optional_result(candidate)))
            claimed = await session.execute(
                update(TaskPresentation)
                .where(TaskPresentation.id == candidate.id)
                .where(TaskPresentation.state == "pending")
                .where(TaskPresentation.lease_token.is_(None))
                .where(live_context_visible() if realtime.provider_adapter == "openai_live" else visible_attention())
                .where(
                    ~select(lease_owner.id)
                    .where(lease_owner.state == "pending")
                    .where(live_context_visible(lease_owner) if realtime.provider_adapter == "openai_live" else visible_attention(lease_owner))
                    .where(lease_owner.lease_token.is_not(None))
                    .where(lease_owner.reserved_session_id == session_id)
                    .exists()
                )
                .values(
                    reserved_session_id=session_id,
                    reserved_generation=generation,
                    reserved_at=now,
                    lease_token=lease_token,
                    commentary_attempted_at=(
                        now
                        if (
                            commentary_attempt
                            and candidate.commentary_attempted_at is None
                        )
                        else candidate.commentary_attempted_at
                    ),
                    updated_at=now,
                )
            )
            if claimed.rowcount != 1:
                continue
            claimed_ids.append(candidate.id)
            await session.refresh(candidate)
            exposure = await domain.append_session_event_once(
                session_id=session_id,
                event_type="attention.context_exposed",
                payload_json={
                    "presentation_id": candidate.id,
                    "source_task_event_id": candidate.source_task_event_id,
                    "session_id": session_id,
                    "generation": generation,
                },
                idempotency_key=f"attention-exposed:{session_id}:{generation}:{candidate.id}",
            )
            exposure_tokens.append(exposure.event.id)
            fact = self._context_fact(
                candidate,
                exposure_token=exposure.event.id,
                commentary=commentary_attempt,
            )
            if fact.get("commentary") and candidate.candidate_type == "terminal_result":
                # A preference reason/revision refresh is factual context, not
                # another request to announce the same unchanged outcome.
                receipts = (await session.execute(select(LiveSessionEvent).where(
                    LiveSessionEvent.session_id == session_id,
                    LiveSessionEvent.event_type == "provider.assistant_update_submitted")
                    .order_by(LiveSessionEvent.sequence.desc()).limit(100))).scalars().all()
                for receipt in receipts:
                    rp = receipt.payload_json or {}
                    if rp.get("generation") != generation:
                        continue
                    if candidate.id not in rp.get("presentation_ids", []):
                        continue
                    try:
                        previous = json.loads(rp.get("submitted_transcript") or "{}")
                    except (ValueError, TypeError):
                        previous = {}
                    if (previous.get("notification") or {}).get("intent") == "requested":
                        # One re-offer is allowed when the user may not have heard it.
                        unheard = ((fact.get("communication_assessment") or {}).get("classification") == "possibly_unheard"
                            and ((previous.get("communication_assessment") or {}).get("classification")) != "possibly_unheard")
                        if not unheard:
                            fact["commentary"] = False
                    break
            facts.append(fact)
        if not facts:
            return None
        return PresentationReservation(
            presentation_ids=tuple(claimed_ids),
            facts=tuple(facts),
            total_pending=len(pending),
            exposure_tokens=tuple(exposure_tokens),
        )

    async def get_attention_checklist(
        self,
        *,
        session_id: str,
        generation: int,
        cursor: str | None = None,
        limit: int = 20,
    ) -> AttentionChecklist:
        """Expose pending attention with a session-bound acknowledgement token."""
        bounded_limit = min(max(int(limit or 20), 1), 50)
        async with session_scope() as session:
            realtime = await session.get(LiveSession, session_id)
            if (
                realtime is None
                or realtime.generation != generation
                or realtime.lifecycle_state not in {"connecting", "ready"}
            ):
                raise ValueError("Live session generation is stale")
            query = (
                select(TaskPresentation)
                .where(TaskPresentation.state == "pending")
                .where(live_context_visible() if realtime.provider_adapter == "openai_live" else visible_attention())
                .order_by(TaskPresentation.created_at.asc(), TaskPresentation.id.asc())
            )
            if cursor:
                decoded = self._decode_checklist_cursor(str(cursor))
                if decoded is None:
                    # Accept the pre-opaque id cursor during the compatibility
                    # release, while all newly issued cursors are stable
                    # created-at/id pairs.
                    query = query.where(TaskPresentation.id > str(cursor))
                else:
                    created_at, presentation_id = decoded
                    query = query.where(
                        or_(
                            TaskPresentation.created_at > created_at,
                            and_(
                                TaskPresentation.created_at == created_at,
                                TaskPresentation.id > presentation_id,
                            ),
                        )
                    )
            rows = list((await session.execute(query.limit(bounded_limit + 1))).scalars().all())
            has_more = len(rows) > bounded_limit
            rows = rows[:bounded_limit]
            domain = JarvisDomainRepository(session)
            items: list[dict] = []
            for row in rows:
                exposure = await domain.append_session_event_once(
                    session_id=session_id,
                    event_type="attention.context_exposed",
                    payload_json={
                        "presentation_id": row.id,
                        "source_task_event_id": row.source_task_event_id,
                        "session_id": session_id,
                        "generation": generation,
                    },
                    idempotency_key=f"attention-exposed:{session_id}:{generation}:{row.id}",
                )
                facts = dict(row.required_facts_json or {})
                facts.pop("required_spoken_phrase", None)
                facts["presentation_id"] = row.id
                facts["exposure_token"] = exposure.event.id
                items.append({
                    "presentation_id": row.id,
                    "task_id": row.task_id,
                    "summary": self._checklist_summary(facts),
                    "facts": facts,
                    "priority": row.importance,
                    "exposure_token": exposure.event.id,
                    "state": row.state,
                })
            return AttentionChecklist(
                items=tuple(items),
                next_cursor=(self._encode_checklist_cursor(rows[-1]) if has_more and rows else None),
                total_pending=int((await session.execute(
                    select(func.count()).select_from(TaskPresentation).where(
                        TaskPresentation.state == "pending"
                    ).where(live_context_visible() if realtime.provider_adapter == "openai_live" else visible_attention())
                )).scalar_one()),
            )

    async def acknowledge_task_update(
        self,
        *,
        session_id: str,
        generation: int,
        presentation_id: str,
        exposure_token: str,
        provider_event_id: str | None = None,
        provider_call_id: str | None = None,
    ) -> dict:
        """Trust one valid model self-report in a standalone transaction."""
        async with session_scope() as session:
            return await self.acknowledge_task_update_in_session(
                session,
                session_id=session_id,
                generation=generation,
                presentation_id=presentation_id,
                exposure_token=exposure_token,
                provider_event_id=provider_event_id,
                provider_call_id=provider_call_id,
            )

    async def acknowledge_task_update_in_session(
        self,
        session,
        *,
        session_id: str,
        generation: int,
        presentation_id: str,
        exposure_token: str,
        provider_event_id: str | None = None,
        provider_call_id: str | None = None,
    ) -> dict:
        """Transition attention without committing the caller's transaction."""
        realtime = await session.get(LiveSession, session_id)
        if (
            realtime is None
            or realtime.generation != generation
            or realtime.lifecycle_state not in {"connecting", "ready"}
        ):
            return {"status": "invalid_exposure", "attention": None}
        exposure = await session.get(LiveSessionEvent, str(exposure_token))
        if (
            exposure is None
            or exposure.session_id != session_id
            or exposure.event_type != "attention.context_exposed"
            or int((exposure.payload_json or {}).get("generation") or 0) != generation
            or str((exposure.payload_json or {}).get("presentation_id") or "") != presentation_id
        ):
            return {"status": "invalid_exposure", "attention": None}
        candidate = await session.get(TaskPresentation, presentation_id)
        if candidate is None:
            return {"status": "invalid_exposure", "attention": None}
        if candidate.state == "superseded":
            return {"status": "superseded", "attention": self._attention_value(candidate)}
        if candidate.state == "handled":
            return {"status": "already_handled", "attention": self._attention_value(candidate)}
        if candidate.candidate_type == "terminal_result" and settings.jarvis_conversation_observer_mode != "off":
            return {"status": "verification_pending", "attention": self._attention_value(candidate)}

        visible = (await session.execute(select(TaskPresentation.id).where(
            TaskPresentation.id == candidate.id, live_context_visible() if realtime.provider_adapter == "openai_live" else visible_attention(),
        ))).scalar_one_or_none()
        if visible is None:
            return {"status": "invalid_exposure", "attention": None}

        now = utc_now()
        transitioned = await session.execute(
            update(TaskPresentation)
            .where(TaskPresentation.id == candidate.id)
            .where(TaskPresentation.state == "pending")
            .where(live_context_visible() if realtime.provider_adapter == "openai_live" else visible_attention())
            .values(
                state="handled",
                handled_at=now,
                handled_by="model_report",
                handled_turn_id=None,
                reserved_session_id=None,
                reserved_generation=None,
                reserved_at=None,
                lease_token=None,
                updated_at=now,
            )
        )
        if transitioned.rowcount != 1:
            await session.refresh(candidate)
            if candidate.state == "superseded":
                return {"status": "superseded", "attention": self._attention_value(candidate)}
            return {"status": "already_handled", "attention": self._attention_value(candidate)}
        await session.refresh(candidate)
        candidate.handled_event_id = await self._append_handled_audit(
            session,
            candidate,
            handled_by="model_report",
            idempotency_key=(
                f"attention-handled:{session_id}:{generation}:{candidate.id}:{exposure.id}"
            ),
            payload={
                "session_id": session_id,
                "generation": generation,
                "exposure_token": exposure.id,
                "provider_event_id": provider_event_id,
                "provider_call_id": provider_call_id,
            },
        )
        if candidate.source_task_event_id:
            await session.execute(
                update(NotificationOutbox)
                .where(NotificationOutbox.task_event_id == candidate.source_task_event_id)
                .where(NotificationOutbox.status.in_(["queued", "failed"]))
                .values(status="superseded", next_attempt_at=None)
            )
        return {"status": "handled", "attention": self._attention_value(candidate)}

    async def acknowledge_notification(
        self,
        *,
        delivery_id: str,
    ) -> bool:
        """Handle the exact task event represented by an opened notification."""
        async with session_scope() as session:
            return await self.acknowledge_notification_in_session(
                session,
                delivery_id=delivery_id,
            )

    async def acknowledge_notification_in_session(self, session, *, delivery_id: str) -> bool:
        row = await session.get(NotificationOutbox, delivery_id)
        if row is None:
            return False
        if not row.task_event_id:
            if row.status in {"queued", "failed"}:
                row.status = "superseded"
                row.next_attempt_at = None
            return True
        presentation = (await session.execute(
            select(TaskPresentation)
            .where(TaskPresentation.source_task_event_id == row.task_event_id)
            .where(TaskPresentation.state == "pending")
            .limit(1)
        )).scalar_one_or_none()
        if presentation is None:
            if row.status in {"queued", "failed"}:
                row.status = "superseded"
                row.next_attempt_at = None
            return True
        now = utc_now()
        transitioned = await session.execute(
            update(TaskPresentation)
            .where(TaskPresentation.id == presentation.id)
            .where(TaskPresentation.state == "pending")
            .values(
                state="handled",
                handled_at=now,
                handled_by="notification_opened",
                handled_turn_id=None,
                reserved_session_id=None,
                reserved_generation=None,
                reserved_at=None,
                lease_token=None,
                updated_at=now,
            )
        )
        await session.refresh(presentation)
        if row.status in {"queued", "failed"}:
            row.status = "superseded"
            row.next_attempt_at = None
        if transitioned.rowcount != 1:
            return True
        presentation.handled_event_id = await self._append_handled_audit(
            session,
            presentation,
            handled_by="notification_opened",
            idempotency_key=f"attention-notification-opened:{delivery_id}",
            payload={"notification_delivery_id": delivery_id},
        )
        return True

    @staticmethod
    def _checklist_summary(facts: dict) -> str:
        return str(
            facts.get("summary")
            or facts.get("question")
            or facts.get("status")
            or facts.get("event_type")
            or "Task update"
        ).strip()

    @staticmethod
    async def _append_handled_audit(
        session,
        candidate: TaskPresentation,
        *,
        handled_by: str,
        idempotency_key: str,
        payload: dict | None = None,
    ) -> str:
        event_id = str(uuid4())
        event_payload = {
            "presentation_id": candidate.id,
            "task_id": candidate.task_id,
            "source_task_event_id": candidate.source_task_event_id,
            "handled_by": handled_by,
            **(payload or {}),
        }
        await session.execute(
            sqlite_insert(JarvisLiveEvent)
            .values(
                id=event_id,
                event_type="attention.handled",
                source_domain="attention",
                source_id=candidate.id,
                source_sequence=None,
                payload_json=event_payload,
                idempotency_key=idempotency_key,
                created_at=candidate.handled_at or utc_now(),
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
        stored_id = (await session.execute(
            select(JarvisLiveEvent.id).where(
                JarvisLiveEvent.idempotency_key == idempotency_key
            )
        )).scalar_one()
        return str(stored_id)

    @staticmethod
    def _context_fact(
        candidate: TaskPresentation,
        *,
        exposure_token: str,
        commentary: bool,
    ) -> dict[str, object]:
        """Build one bounded append item without truncating opaque IDs."""
        raw = dict(candidate.required_facts_json or {})
        if candidate.candidate_type == "terminal_result":
            # Shared A payload keeps bounded human facts and full opaque handles.
            notification = raw.get("notification") or {"intent":"discretionary","reason":"No notification preference recorded","revision":0,"recorded":False}
            return {"schema":"jarvis.task_update.v1","task_id":candidate.task_id,"event_type":raw.get("event_type"),
                "goal":" ".join(str(raw.get("goal") or "").split())[:90],
                "summary":" ".join(str(raw.get("summary") or "").split())[:180],"status":raw.get("status"),
                "notification":notification,"presentation_id":candidate.id,"exposure_token":exposure_token,
                "source_task_event_id":candidate.source_task_event_id,"completed_at":raw.get("completed_at") or candidate.created_at.isoformat(),
                "delivery_state":candidate.state,
                "communication_assessment":raw.get("communication_assessment"),
                "commentary":settings.jarvis_conversation_observer_mode == "enabled" and commentary and notification["intent"] != "suppressed"}
        # Legacy projections may still contain the old canonical phrase. It
        # is diagnostic migration residue, never model-facing attention data.
        raw.pop("required_spoken_phrase", None)

        def text_value(value: object, limit: int = 320) -> str | None:
            normalized = " ".join(str(value or "").split())
            if not normalized:
                return None
            return normalized if len(normalized) <= limit else normalized[: limit - 1].rstrip() + "…"

        task = raw.get("task") if isinstance(raw.get("task"), dict) else {}
        source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
        presentation = raw.get("presentation") if isinstance(raw.get("presentation"), dict) else {}
        fact = {
            "schema": "jarvis.task_update.v1",
            "task_id": candidate.task_id,
            "goal": text_value(raw.get("goal") or task.get("goal")),
            "status": raw.get("status") or task.get("status"),
            "event_type": raw.get("event_type"),
            "kind": raw.get("kind"),
            "summary": text_value(raw.get("summary")),
            "question": text_value(raw.get("question")),
            "importance": candidate.importance,
            "source": {
                "task_event_id": source.get("task_event_id"),
                "sequence": source.get("sequence"),
            },
            "presentation": {
                "candidate_type": presentation.get("candidate_type") or candidate.candidate_type,
                "coalescing_key": presentation.get("coalescing_key") or candidate.coalescing_key,
            },
            "presentation_id": candidate.id,
            "exposure_token": exposure_token,
            "commentary": commentary,
        }
        return TaskPresentationService._bounded_append_fact(
            {key: value for key, value in fact.items() if value is not None}
        )

    @staticmethod
    def _bounded_append_fact(fact: dict[str, object]) -> dict[str, object]:
        """Conservatively guarantee that one serialized append is <=500 tokens."""
        bounded = json.loads(json.dumps(fact, ensure_ascii=False))

        def size() -> int:
            return len(
                json.dumps(
                    bounded,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )

        # Semantic content is why this item is appended. Remove redundant
        # metadata before shortening it; opaque acknowledgement IDs stay intact.
        presentation = bounded.get("presentation")
        if size() > MAX_APPEND_UTF8_BYTES and isinstance(presentation, dict):
            presentation.pop("coalescing_key", None)
        source = bounded.get("source")
        if size() > MAX_APPEND_UTF8_BYTES and isinstance(source, dict):
            source.pop("sequence", None)
        for key in ("schema", "status", "kind", "presentation", "importance"):
            if size() <= MAX_APPEND_UTF8_BYTES:
                break
            bounded.pop(key, None)

        text_keys = ("goal", "summary", "question")
        while size() > MAX_APPEND_UTF8_BYTES:
            candidates = [
                key for key in text_keys
                if isinstance(bounded.get(key), str) and bounded.get(key)
            ]
            if not candidates:
                break
            # Goal is explanatory context; a question/summary is the update.
            key = "goal" if "goal" in candidates and (bounded.get("question") or bounded.get("summary")) else max(
                candidates, key=lambda item: len(str(bounded[item]).encode("utf-8")))
            value = str(bounded[key])
            encoded = value.encode("utf-8")
            if len(encoded) <= 24:
                if key in {"question", "summary"}:
                    raise ValueError("Attention content cannot fit beside its acknowledgement identifiers")
                bounded.pop(key, None)
                continue
            shortened = encoded[: max(1, len(encoded) - 32)].decode("utf-8", errors="ignore").rstrip()
            bounded[key] = shortened + "…"

        if size() > MAX_APPEND_UTF8_BYTES:
            raise ValueError("Attention append identifiers exceed the 500-token safety bound")
        return bounded

    @staticmethod
    def _encode_checklist_cursor(row: TaskPresentation) -> str:
        value = json.dumps(
            {
                "created_at": row.created_at.isoformat(),
                "id": row.id,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_checklist_cursor(value: str) -> tuple[datetime, str] | None:
        try:
            padded = value + "=" * (-len(value) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
            created_at = datetime.fromisoformat(str(decoded["created_at"]))
            presentation_id = str(decoded["id"])
            if not presentation_id:
                return None
            return created_at, presentation_id
        except (ValueError, TypeError, KeyError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
            return None

    @staticmethod
    def _optional_result(candidate: TaskPresentation) -> bool:
        """An unrequested result, offered once when the conversation is idle (a person waiting would hear it)."""
        facts = candidate.required_facts_json or {}
        return (candidate.candidate_type == "terminal_result" and candidate.commentary_attempted_at is None
            and settings.jarvis_conversation_observer_mode == "enabled"
            and ((facts.get("notification") or {}).get("intent") or "discretionary") == "discretionary")

    @staticmethod
    def _urgent(candidate: TaskPresentation) -> bool:
        """A failure of an important task the user is waiting on may politely interrupt."""
        event_type = str((candidate.required_facts_json or {}).get("event_type") or "")
        return candidate.importance == "high" and event_type in {"task.failed", "task.interrupted"}

    @staticmethod
    def _requires_commentary(candidate: TaskPresentation) -> bool:
        if candidate.candidate_type == "terminal_result" and settings.jarvis_conversation_observer_mode == "enabled":
            return (candidate.required_facts_json or {}).get("notification", {}).get("intent") == "requested"
        if candidate.commentary_attempted_at is not None:
            return False
        facts = candidate.required_facts_json if isinstance(candidate.required_facts_json, dict) else {}
        event_type = str(facts.get("event_type") or "")
        return event_type in {"task.reminder.due", "task.requires_input"}

    @staticmethod
    def _attention_value(candidate: TaskPresentation) -> dict:
        facts = dict(candidate.required_facts_json or {})
        return {
            "presentation_id": candidate.id,
            "task_id": candidate.task_id,
            "state": candidate.state,
            "summary": TaskPresentationService._checklist_summary(facts),
            "priority": candidate.importance,
            "handled_at": None if candidate.handled_at is None else candidate.handled_at.isoformat(),
            "handled_by": candidate.handled_by,
            "source_task_event_id": candidate.source_task_event_id,
        }

    async def release_append_lease(
        self,
        *,
        presentation_ids: tuple[str, ...],
        session_id: str,
        generation: int,
        turn_id: str | None,
        delivery_id: str | None,
        allow_superseded: bool = False,
    ) -> None:
        now = utc_now()
        async with session_scope() as session:
            if not presentation_ids:
                return
            query = (
                update(TaskPresentation)
                .where(TaskPresentation.id.in_(presentation_ids))
                .where(TaskPresentation.state == "pending")
                .where(TaskPresentation.reserved_session_id == session_id)
                .where(TaskPresentation.reserved_generation == generation)
                .values(
                    reserved_session_id=None,
                    reserved_generation=None,
                    reserved_at=None,
                    lease_token=None,
                    updated_at=now,
                )
            )
            await session.execute(query)

    async def release_transport_leases(
        self,
        *,
        presentation_ids: tuple[str, ...],
        session_id: str,
        generation: int,
        turn_id: str | None,
        delivery_id: str | None,
        heard_transcript: str,
        response_kind: str | None = None,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Release append ownership without changing authoritative attention."""
        now = utc_now()
        async with session_scope() as session:
            if not presentation_ids:
                return (), ()
            await session.execute(
                update(TaskPresentation)
                .where(TaskPresentation.id.in_(presentation_ids))
                .where(TaskPresentation.state == "pending")
                .where(TaskPresentation.reserved_session_id == session_id)
                .where(TaskPresentation.reserved_generation == generation)
                .values(
                    reserved_session_id=None,
                    reserved_generation=None,
                    reserved_at=None,
                    lease_token=None,
                    defer_until=None,
                    updated_at=now,
                )
            )
        return (), ()

    async def requeue(
        self,
        *,
        presentation_ids: tuple[str, ...],
        session_id: str,
        generation: int,
        error: str | None = None,
        allow_superseded: bool = False,
    ) -> None:
        now = utc_now()
        async with session_scope() as session:
            if not presentation_ids:
                return
            await session.execute(
                update(TaskPresentation)
                .where(TaskPresentation.id.in_(presentation_ids))
                .where(TaskPresentation.state == "pending")
                .where(TaskPresentation.reserved_session_id == session_id)
                .where(TaskPresentation.reserved_generation == generation)
                .values(
                    reserved_session_id=None,
                    reserved_generation=None,
                    reserved_at=None,
                    lease_token=None,
                    retry_count=TaskPresentation.retry_count + 1,
                    defer_until=None,
                    last_error=error,
                    updated_at=now,
                )
            )

    async def release_session_reservations(
        self,
        *,
        session_id: str,
        generation: int,
        error: str,
    ) -> int:
        """Return orphaned reservations to the retry queue after session loss."""
        now = utc_now()
        released = 0
        async with session_scope() as session:
            result = await session.execute(
                update(TaskPresentation)
                .where(TaskPresentation.state == "pending")
                .where(TaskPresentation.reserved_session_id == session_id)
                .where(TaskPresentation.reserved_generation == generation)
                .values(
                    reserved_session_id=None,
                    reserved_generation=None,
                    reserved_at=None,
                    lease_token=None,
                    defer_until=now,
                    retry_count=TaskPresentation.retry_count + 1,
                    last_error=error,
                    updated_at=now,
                )
            )
            released = int(result.rowcount or 0)
        return released

    @staticmethod
    async def _supersede_progress(session, task_id: str) -> None:
        result = await session.execute(
            select(TaskPresentation)
            .where(TaskPresentation.task_id == task_id)
            .where(TaskPresentation.candidate_type == "progress")
            .where(TaskPresentation.state.in_(sorted(UNFINISHED_PRESENTATION_STATES)))
        )
        for candidate in result.scalars().all():
            candidate.state = "superseded"
            candidate.reserved_session_id = None
            candidate.reserved_generation = None
            candidate.reserved_at = None
            candidate.lease_token = None
            if candidate.source_task_event_id:
                await TaskPresentationService._supersede_notification(
                    session, candidate.source_task_event_id
                )

    @staticmethod
    async def _supersede_answered_questions(session, events: list[TaskEvent]) -> None:
        answered_ids = {
            str((event.payload_json or {}).get("question_id") or "")
            for event in events
        }
        candidates = list((await session.execute(
            select(TaskPresentation)
            .where(TaskPresentation.candidate_type == "question")
            .where(TaskPresentation.state.in_(sorted(UNFINISHED_PRESENTATION_STATES)))
        )).scalars().all())
        for candidate in candidates:
            question_id = str((candidate.required_facts_json or {}).get("question_id") or "")
            if question_id in answered_ids:
                candidate.state = "superseded"
                candidate.reserved_session_id = None
                candidate.reserved_generation = None
                candidate.reserved_at = None
                candidate.lease_token = None
                if candidate.source_task_event_id:
                    await TaskPresentationService._supersede_notification(
                        session, candidate.source_task_event_id
                    )

    @staticmethod
    async def _supersede_notification(session, task_event_id: str) -> None:
        rows = list((await session.execute(
            select(NotificationOutbox)
            .where(NotificationOutbox.task_event_id == task_event_id)
            .where(NotificationOutbox.status.in_(["queued", "failed"]))
        )).scalars().all())
        for row in rows:
            row.status = "superseded"
            row.next_attempt_at = None

    @staticmethod
    async def _ensure_notification(session, task: Task, event: TaskEvent) -> None:
        question = None
        if event.event_type == "task.requires_input":
            question_id = str((event.payload_json or {}).get("question_id") or "")
            question = await session.get(TaskQuestion, question_id) if question_id else None
            if question is None:
                return
        await enqueue_task_notification(session, task=task, event=event, question=question)

    @staticmethod
    def _facts(event: TaskEvent, task: Task) -> dict:
        payload = dict(event.payload_json or {})
        payload.pop("required_spoken_phrase", None)
        payload.pop("orchestration_decision_id", None)
        if event.event_type in {"task.failed", "task.interrupted"}:
            raw_failure = str(payload.get("summary") or task.terminal_summary or "")
            payload["summary"] = public_failure_summary(raw_failure)
            for key in ("error", "exception", "traceback", "stack", "stderr"):
                payload.pop(key, None)
            payload["diagnostics_available"] = bool(raw_failure)
        candidate_type = PRESENTABLE_EVENT_TYPES[event.event_type]
        kind = {
            "progress": "progress",
            "question": "question",
            "terminal_result": "result",
            "reminder": "reminder",
        }[candidate_type]
        coalescing_key = f"task:{task.id}:{candidate_type}"
        return {
            "task_id": task.id,
            "goal": task.goal,
            "event_type": event.event_type,
            "completed_at": event.created_at.isoformat(),
            **payload,
            "schema": "jarvis.task_update.v1",
            "kind": kind,
            "task": {
                "id": task.id,
                "goal": task.goal,
                "status": task.status,
                "importance": task.importance,
            },
            "fact": {
                "event_type": event.event_type,
                "data": payload,
            },
            "source": {
                "domain": "task",
                "task_id": task.id,
                "task_event_id": event.id,
                "sequence": event.sequence,
            },
            "presentation": {
                "candidate_type": candidate_type,
                "coalescing_key": coalescing_key,
                "importance": task.importance,
            },
        }

    @staticmethod
    def _delivery_priority(candidate: TaskPresentation) -> tuple[int, datetime, str]:
        event_type = str((candidate.required_facts_json or {}).get("event_type") or "")
        terminal = event_type in {
            "task.failed",
            "task.interrupted",
            "task.cancelled",
            "task.completed",
        }
        if event_type == "task.reminder.due":
            rank = 0
        elif event_type == "task.requires_input":
            rank = 1
        elif terminal and str(candidate.importance or "normal") == "high":
            rank = 2
        elif terminal:
            rank = 3
        elif event_type == "task.progress":
            rank = 4
        else:
            rank = 5
        return rank, candidate.created_at, candidate.id


task_presentation_service = TaskPresentationService()
