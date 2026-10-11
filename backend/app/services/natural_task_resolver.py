"""Deterministic, user-facing task references without exposed task IDs."""
from __future__ import annotations

from dataclasses import dataclass
import re

from sqlalchemy import and_, desc, exists, func, or_, select
from sqlalchemy.dialects.sqlite import insert

from app.db.session import session_scope, read_session_scope
from app.db.tables import JarvisState, Task, TaskEvent, TaskQuestion
from app.services.task_statuses import ACTIVE_TASK_STATUSES
from app.services.admission_timing import timed


_PRONOUNS = {"this", "that", "it", "this task", "that task", "the task"}
_STOP = {
    "a", "an", "and", "about", "check", "did", "do", "for", "how", "is", "of", "on",
    "doing", "going", "please", "progress", "status", "task", "the", "that", "this", "to",
    "was", "what", "with",
}


def task_title(task: Task) -> str:
    compact = " ".join(task.goal.split()).strip()
    return compact[:93].rstrip() + ("…" if len(compact) > 93 else "")


@dataclass(frozen=True, slots=True)
class TaskResolution:
    task: Task | None
    ambiguous: bool = False
    candidates: tuple[Task, ...] = ()
    clarification: str | None = None


class NaturalTaskResolver:
    @timed("focus_update")
    async def touch(self, task_id: str) -> None:
        async with session_scope() as session:
            # Acquire SQLite's writer before reading focus/history. Concurrent
            # admissions must neither insert the singleton twice nor lose refs.
            await session.execute(insert(JarvisState).values(id="current",
                state_revision=0, live_event_cursor=0).on_conflict_do_nothing(index_elements=["id"]))
            task = await self._by_id(session, task_id)
            if task is None:
                return
            state = await session.get(JarvisState, "current")
            assert state is not None
            recent = [str(item) for item in (state.recent_task_refs_json or []) if str(item) != task_id]
            state.focused_task_id = task_id
            state.recent_task_refs_json = [task_id, *recent[:11]]
            state.state_revision = int(state.state_revision or 0) + 1

    async def resolve(self, reference: str, *, active_only: bool = False) -> TaskResolution:
        normalized = " ".join(str(reference or "").lower().split()).strip()
        if not normalized:
            return TaskResolution(task=None)
        async with read_session_scope() as session:
            state = await session.get(JarvisState, "current")
            recent = [str(item) for item in ((state.recent_task_refs_json if state else None) or [])]
            focused_id = state.focused_task_id if state else None
            words = set(re.findall(r"[\w'-]+", normalized, flags=re.UNICODE))

            if normalized in _PRONOUNS or (
                words.intersection({"it", "that", "this"})
                and not words.difference(_STOP | {"it"})
            ):
                task = await self._get_allowed(session, focused_id, active_only=active_only)
                return TaskResolution(task=task)

            if normalized in {"current", "current task", "the current task"} or (
                "current" in words and not words.difference(_STOP | {"current"})
            ):
                focused = await self._get_allowed(session, focused_id, active_only=True)
                if focused is not None:
                    return TaskResolution(task=focused)
                return TaskResolution(task=await self._first_recent(session, recent, statuses=ACTIVE_TASK_STATUSES))

            if normalized in {"last", "last task", "the last task"}:
                task = await self._first_recent(session, recent, statuses=ACTIVE_TASK_STATUSES if active_only else None)
                if task is None:
                    statement = select(Task).order_by(Task.creation_order.desc()).limit(1)
                    if active_only:
                        statement = statement.where(Task.status.in_(sorted(ACTIVE_TASK_STATUSES)))
                    task = (await session.execute(statement)).scalar_one_or_none()
                return TaskResolution(task=task)

            if normalized in {"last completed", "last completed task", "the last completed task"}:
                task = await self._first_recent(session, recent, statuses={"completed"})
                if task is None:
                    task = (await session.execute(
                        select(Task).where(Task.status == "completed")
                        .order_by(Task.finished_at.desc(), Task.creation_order.desc()).limit(1)
                    )).scalar_one_or_none()
                return TaskResolution(task=task)

            # Exact internal identities remain accepted by trusted callers, but
            # are never required or included in clarification text.
            exact = await self._by_id(session, reference.strip())
            if exact is not None and (not active_only or exact.status in ACTIVE_TASK_STATUSES):
                return TaskResolution(task=exact)

            terms = [item for item in re.findall(r"[\w'-]+", normalized, flags=re.UNICODE) if item not in _STOP]
            if not terms:
                return TaskResolution(task=None)
            clauses = []
            for term in terms[:8]:
                pattern = f"%{term}%"
                clauses.append(or_(
                    func.lower(Task.goal).like(pattern),
                    func.lower(func.coalesce(Task.terminal_summary, "")).like(pattern),
                    exists(select(TaskQuestion.id).where(
                        TaskQuestion.task_id == Task.id,
                        func.lower(TaskQuestion.question).like(pattern),
                    )),
                    exists(select(TaskEvent.id).where(
                        TaskEvent.task_id == Task.id,
                        func.lower(func.coalesce(TaskEvent.payload_json, "")).like(pattern),
                    )),
                ))
            statement = select(Task).where(or_(*clauses))
            if active_only:
                statement = statement.where(Task.status.in_(sorted(ACTIVE_TASK_STATUSES)))
            rows = list((await session.execute(
                statement.order_by(desc(Task.updated_at), desc(Task.creation_order)).limit(50)
            )).scalars().all())
            if not rows:
                return TaskResolution(task=None)

            scored = sorted(
                ((self._score(task, terms, recent), task) for task in rows),
                key=lambda item: (item[0], item[1].creation_order),
                reverse=True,
            )
            best_score = scored[0][0]
            plausible = tuple(task for score, task in scored if score == best_score and score[0] > 0)
            if len(plausible) == 1:
                return TaskResolution(task=plausible[0])
            candidates = plausible[:3]
            labels = [task_title(item) for item in candidates[:2]]
            if not labels:
                return TaskResolution(task=None)
            clarification = (
                f"Do you mean {labels[0]} or {labels[1]}?"
                if len(labels) > 1 else f"Do you mean {labels[0]}?"
            )
            return TaskResolution(
                task=None, ambiguous=True, candidates=candidates, clarification=clarification
            )

    @staticmethod
    async def _get_allowed(session, task_id: str | None, *, active_only: bool) -> Task | None:
        task = None if not task_id else await NaturalTaskResolver._by_id(session, task_id)
        if task is not None and active_only and task.status not in ACTIVE_TASK_STATUSES:
            return None
        return task

    @staticmethod
    async def _first_recent(session, recent: list[str], *, statuses: set[str] | None) -> Task | None:
        for task_id in recent:
            task = await NaturalTaskResolver._by_id(session, task_id)
            if task is not None and (statuses is None or task.status in statuses):
                return task
        return None

    @staticmethod
    async def _by_id(session, task_id: str) -> Task | None:
        return (await session.execute(
            select(Task).where(Task.id == task_id).limit(1)
        )).scalar_one_or_none()

    @staticmethod
    def _score(task: Task, terms: list[str], recent: list[str]) -> tuple[int, int, int]:
        haystack = f"{task.goal}\n{task.terminal_summary or ''}".lower()
        matched = [term for term in terms if term in haystack]
        specificity = sum(3 if re.search(rf"\b{re.escape(term)}\b", haystack) else 1 for term in matched)
        recency = max(1, 6 - recent.index(task.id)) if task.id in recent else 0
        # A partial descriptive match must never outrank a more complete one
        # merely because it was recently focused (especially for cancellation).
        return len(matched), specificity, recency


natural_task_resolver = NaturalTaskResolver()
