"""Best-effort metadata observations, never publication authority or content.

Lifecycle decorators enqueue only after the owning transaction has committed or
rolled back. Batches use independent transactions; failure cannot fail publication.
"""
from __future__ import annotations

from contextvars import ContextVar
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import wraps
import asyncio
import logging
import math
import json
from time import perf_counter
from uuid import uuid4
from anyio import CancelScope

from sqlalchemy import func, select, or_, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import OperationalError

from app.db.session import observation_session_scope as session_scope
from app.db.session import finish_database_operation
from app.db.tables import ResultMetric, ResultPage, ResultPageRevision, TaskExecutionRun, TaskAttempt, TaskEvent, NotificationOutbox
from app.config import settings
from app.services.admission_timing import mark, timed
from app.services.observation_queue import ObservationQueue

logger = logging.getLogger(__name__)
_trace = ContextVar("result_observation", default=None)
OBSERVATION_QUERY_SECONDS = .25
_CURRENT = object()


class _ObservationSession:
    def __init__(self, session):
        self.session = session

    def __getattr__(self, name):
        return getattr(self.session, name)

    async def execute(self, *args, **kwargs):
        # Cancelling an aiosqlite await does not cancel the SQLite worker thread.
        # Let the short-budget operation finish before rollback/close, rather
        # than abandon a connection that may subsequently acquire a write lock.
        return await finish_database_operation(self.session.execute(*args, **kwargs))


@asynccontextmanager
async def observation_session():
    # Like SSE reads, finish this *short* transaction/close on disconnect.
    # Never shield the live stream or any task/publication transaction.
    with CancelScope(shield=True):
        async with session_scope() as session:
            diagnostic = _ObservationSession(session)
            # Bound queries/writes, not rollback/commit/connection cleanup. An
            # asyncio deadline during close can leave a committed metric without
            # its log and produce cancelled-connection errors under CPU load.
            async with asyncio.timeout(OBSERVATION_QUERY_SECONDS):
                if settings.database_url.startswith("sqlite"):
                    await diagnostic.execute(text("PRAGMA busy_timeout=100"))
                yield diagnostic


def phase(name, *, outcome=None):
    trace = _trace.get()
    if trace is not None:
        trace["publication"] = True
        if name == "validation" and trace.get("phase") != name:
            trace["validation_started"] = perf_counter()
        trace["phase"] = name
        if outcome:
            trace["outcome"] = outcome


def allocated(page, started):
    trace = _trace.get()
    if trace is not None:
        trace["allocations"].append((page.id, (perf_counter() - started) * 1000,
            page.active_execution_run_id, page.revision_number))


def validated(started):
    trace = _trace.get()
    if trace is not None:
        trace["validation_ms"] = (perf_counter() - started) * 1000
        trace["phase"] = "transaction"
        trace["transaction_started"] = perf_counter()


def completed():
    trace = _trace.get()
    if trace is not None:
        trace["completion_started"] = perf_counter()


def notification_queued(page, event):
    trace = _trace.get()
    if trace is not None and "completion_started" in trace:
        trace["notifications"].append((page.id, event.id, (perf_counter() - trace["completion_started"]) * 1000,
            event.execution_run_id, page.revision_number))


def age_ms(value):
    return max(0, (datetime.now(timezone.utc) - value.replace(tzinfo=timezone.utc)).total_seconds() * 1000)


async def record(session, *, kind, key, page=None, duration_ms=None, values=None, run_id=_CURRENT, attempt_id=None, revision_number=_CURRENT):
    if duration_ms is not None and (not math.isfinite(duration_ms) or duration_ms < 0):
        raise ValueError("Invalid observation duration")
    # All callers supply code-owned small metadata dictionaries, never payloads.
    metadata = {"source": "server", **(values or {})}
    if page:
        metadata.update(app=page.app_slug, result_kind=page.result_kind, public_id=page.public_id)
    result = await session.execute(insert(ResultMetric).values(metric_key=key, kind=kind,
        result_page_id=page.id if page else None, task_id=page.task_id if page else None,
        execution_run_id=(page.active_execution_run_id if page else None) if run_id is _CURRENT else run_id,
        attempt_id=attempt_id, revision_number=(page.revision_number if page else None) if revision_number is _CURRENT else revision_number,
        duration_ms=duration_ms, values_json=metadata).on_conflict_do_nothing(index_elements=["metric_key"]).returning(ResultMetric.id))
    return result.scalar_one_or_none() is not None


async def _write_batch(jobs):
    try:
        await _write_batch_once(jobs)
    except (OperationalError, TimeoutError) as error:
        if isinstance(error, OperationalError):
            code = getattr(error.orig, "sqlite_errorcode", 0)
            if (code & 255) not in {5, 6}:  # SQLITE_BUSY/LOCKED, including snapshot contention
                raise
        # A fresh short transaction can recover a stale read snapshot. Metric
        # keys make this one retry safe even if commit acknowledgement was lost.
        logger.warning("result_observation_bounded_retry error_type=%s", type(error).__name__)
        await asyncio.sleep(.02)
        await _write_batch_once(jobs)


async def _write_batch_once(jobs):
    logs = []
    # One short transaction for a bounded batch, not one per observation.
    async with observation_session() as session:
        observations = []
        for job in jobs:
            if job["type"] == "publication":
                observations.extend(await _publication_observations(session, job))
            else:
                observations.append((job["page_id"], job["metric"]))
        page_ids = {page_id for page_id, _ in observations if page_id}
        pages = {page.id: page for page in (await session.execute(
            select(ResultPage).where(ResultPage.id.in_(page_ids)))).scalars()} if page_ids else {}
        for page_id, kwargs in observations:
            page = pages.get(page_id)
            if page_id and page is None:
                logger.warning("result_observation_page_unavailable")
                continue
            if not await record(session, page=page, **kwargs):
                continue
            logs.append({"event": "result_observation", "result_metric_kind": kwargs["kind"],
                "result_page_id": page_id, "duration_ms": kwargs.get("duration_ms"),
                "result_public_id": page.public_id if page else None,
                "result_task_id": page.task_id if page else None,
                "result_run_id": kwargs.get("run_id", page.active_execution_run_id if page else None),
                "result_attempt_id": kwargs.get("attempt_id"),
                "result_revision": kwargs.get("revision_number", page.revision_number if page else None),
                "result_app": page.app_slug if page else None, "result_kind": page.result_kind if page else None,
                "result_outcome": (kwargs.get("values") or {}).get("outcome")})
    # Never claim persistence before the whole batch's commit succeeds.
    for entry in logs:
        logger.info(json.dumps(entry, separators=(",", ":")))


observation_queue = ObservationQueue(_write_batch)


@timed("result_observation_enqueue")
async def observe(page_id=None, **kwargs):
    """Nonblocking metadata submission; acceptance is not a persistence receipt."""
    return observation_queue.submit(dict(type="metric", page_id=page_id, metric=kwargs))


async def _publication_observations(session, job):
    task_id, run_id = job["task_id"], job["run_id"]
    query = select(ResultPage).where(ResultPage.task_id == task_id)
    if run_id:
        query = query.where(or_(ResultPage.active_execution_run_id == run_id,
            ResultPage.initial_execution_run_id == run_id,
            ResultPage.id.in_(select(ResultPageRevision.result_page_id).where(ResultPageRevision.execution_run_id == run_id))))
    pages = list((await session.execute(query)).scalars())
    run = await session.get(TaskExecutionRun, run_id) if run_id else None
    owned_run = run_id if run and run.task_id == task_id else None
    attempt = await session.get(TaskAttempt, job["attempt_id"]) if job["attempt_id"] else None
    owned_attempt = attempt.id if attempt and attempt.task_id == task_id and attempt.execution_run_id == owned_run else None
    # A later successful publication must not give an earlier failed operation
    # a revision it never committed. Keep such associations explicitly unknown.
    revisions = dict((await session.execute(select(ResultPageRevision.result_page_id, ResultPageRevision.revision_number)
        .where(ResultPageRevision.execution_run_id == owned_run))).all()) if owned_run and job["values"]["outcome"] in {"committed", "replayed"} else {}
    return [(page.id, dict(kind="publication", key=f"{job['key']}:{page.id}", duration_ms=job["duration_ms"],
        run_id=owned_run, attempt_id=owned_attempt, revision_number=revisions.get(page.id, job["revision_number"]),
        values=job["values"])) for page in pages]


def observe_lifecycle(kind):
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            trace = {"allocations": [], "notifications": [], "phase": "fence", "publication": kwargs.get("workflow_result") is not None}
            token = _trace.set(trace)
            started = perf_counter()
            outcome = "committed"
            try:
                return await function(*args, **kwargs)
            except ValueError:
                outcome = "validation_failed" if trace["phase"] == "validation" else "rejected"
                raise
            except asyncio.CancelledError:
                outcome = "cancelled"
                trace["allocations"], trace["notifications"], trace["publication"] = [], [], False
                logger.info("result_observation_cancelled")
                raise
            except Exception:
                outcome = "rollback"
                raise
            finally:
                if outcome == "committed" and kind in {"admission", "continuation"}:
                    mark("admission_commit_ns")
                elapsed = (perf_counter() - started) * 1000
                if trace["phase"] == "validation" and "validation_started" in trace:
                    trace["validation_ms"] = (perf_counter() - trace["validation_started"]) * 1000
                transaction_ms = (perf_counter() - trace["transaction_started"]) * 1000 if "transaction_started" in trace else None
                _trace.reset(token)
                for page_id, allocation_ms, run_id, revision in trace["allocations"]:
                    # A rolled-back allocation no longer exists, so observe skips it.
                    await observe(page_id, kind="allocation", key=f"allocation:{page_id}", duration_ms=allocation_ms,
                        run_id=run_id, revision_number=revision,
                        values={"clock": "server_monotonic"})
                    await observe(page_id, kind="admission_to_allocation", key=f"admission:{page_id}", duration_ms=elapsed,
                        run_id=run_id, revision_number=revision,
                        values={"stage": kind, "includes_capture_preflight": False, "includes_transaction_commit": True, "clock": "server_monotonic"})
                if outcome == "committed":
                    for page_id, event_id, enqueue_ms, run_id, revision in trace["notifications"]:
                        await observe(page_id, kind="completion_to_notification_enqueue", key=f"enqueue:{event_id}", duration_ms=enqueue_ms,
                            run_id=run_id, revision_number=revision,
                            values={"clock": "server_monotonic", "task_event_id": event_id, "committed_with_publication": True})
                if trace["publication"]:
                    task_id = kwargs.get("task_id") or (args[1] if len(args) > 1 else None)
                    run_id = kwargs.get("execution_run_id")
                    observation_queue.submit(dict(type="publication", task_id=task_id, run_id=run_id,
                        attempt_id=kwargs.get("attempt_id"), key=f"publication:{uuid4()}", duration_ms=elapsed,
                        revision_number=kwargs.get("expected_revision"),
                        values={"outcome": trace.get("outcome", outcome), "phase": trace["phase"],
                            "validation_ms": trace.get("validation_ms"), "transaction_ms": transaction_ms, "clock": "server_monotonic"}))
        return wrapped
    return decorate


def observe_preflight(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        started, outcome = perf_counter(), "captured"
        try:
            return await function(*args, **kwargs)
        except ValueError as exc:
            outcome = "timeout" if str(exc) == "Capture was not ready before the pre-admission deadline" else "rejected"
            raise
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception:
            outcome = "failed"
            raise
        finally:
            if outcome != "cancelled":
                await observe(kind="capture_preflight", key=f"capture:{uuid4()}", duration_ms=(perf_counter() - started) * 1000,
                    values={"outcome": outcome, "clock": "server_monotonic"})
    return wrapped


async def observe_notification(outbox_id, kind):
    try:
        async with observation_session() as session:
            outbox = await session.get(NotificationOutbox, outbox_id)
            if outbox is None or not outbox.deep_link.startswith("/results/"):
                return
            public_id = outbox.deep_link.removeprefix("/results/").split("?", 1)[0]
            page = (await session.execute(select(ResultPage).where(ResultPage.public_id == public_id,
                ResultPage.task_id == outbox.task_id))).scalar_one_or_none()
            if page is None:
                return
            event = (await session.execute(select(TaskEvent).where(TaskEvent.id == outbox.task_event_id))).scalar_one_or_none() if outbox.task_event_id else None
            run_id = event.execution_run_id if event and event.task_id == page.task_id else None
            revision = (await session.execute(select(ResultPageRevision.revision_number).where(
                ResultPageRevision.result_page_id == page.id, ResultPageRevision.execution_run_id == run_id)
                .order_by(ResultPageRevision.revision_number.desc()).limit(1))).scalar_one_or_none() if run_id else None
            page_id, elapsed, delivery_id, status = page.id, age_ms(outbox.created_at), outbox.id, outbox.status
        await observe(page_id, kind=kind, key=f"{kind}:{delivery_id}:{status}" if kind == "notification_transport" else f"{kind}:{delivery_id}",
            duration_ms=elapsed, run_id=run_id, revision_number=revision, values={"delivery_id": delivery_id, "outbox_status": status, "clock": "server_wall",
                "source": "client_acknowledgement" if kind == "notification_opened" else "server_transport",
                "android_display_verified": False})
    except Exception:
        logger.warning("result_observation_unavailable")


def observe_notification_transport(function):
    @wraps(function)
    async def wrapped(outbox_id):
        try:
            return await function(outbox_id)
        finally:
            await observe_notification(outbox_id, "notification_transport")
    return wrapped


async def snapshot(session, page, limit=100):
    counts = dict((await session.execute(select(ResultMetric.kind, func.count()).where(ResultMetric.result_page_id == page.id)
        .group_by(ResultMetric.kind))).all())
    rows = list((await session.execute(select(ResultMetric).where(ResultMetric.result_page_id == page.id)
        .order_by(ResultMetric.id.desc()).limit(limit))).scalars())
    durations = {}
    for kind in {row.kind for row in rows}:
        samples = sorted(row.duration_ms for row in rows if row.kind == kind and row.duration_ms is not None)
        if samples:
            durations[kind] = {"sample_count": len(samples), "max_ms": max(samples), "p95_ms": samples[math.ceil(.95 * len(samples)) - 1]}
    outcomes = dict((await session.execute(select(ResultMetric.values_json["outcome"].as_string(), func.count())
        .where(ResultMetric.result_page_id == page.id, ResultMetric.kind == "publication")
        .group_by(ResultMetric.values_json["outcome"].as_string()))).all())
    replayed = (await session.execute(select(func.sum(ResultMetric.values_json["events"].as_integer())).where(
        ResultMetric.result_page_id == page.id, ResultMetric.kind == "sse_replay"))).scalar_one() or 0
    return {"public_id": page.public_id, "counts": counts, "publication_outcomes": outcomes,
        "observation_queue": observation_queue.status(),
        "sse_replay_events_emitted": replayed,
        "recent_durations": durations, "sample_window": limit,
        "recent": [{"kind": row.kind, "run_id": row.execution_run_id, "attempt_id": row.attempt_id,
            "revision": row.revision_number, "duration_ms": row.duration_ms, "metadata": row.values_json} for row in rows]}
