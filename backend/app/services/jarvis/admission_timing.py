"""Metadata-only admission spans on one server clock, with no telemetry writes.

Spans may nest or overlap. Their durations
must not be added together to obtain elapsed time. No arguments/content enter
the trace. ContextVar isolation keeps overlapping turns independent.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import perf_counter_ns as monotonic_ns
import json
import logging


_trace = ContextVar("admission_timing", default=None)
logger = logging.getLogger(__name__)


def mark(name):
    trace = _trace.get()
    if trace is not None and not trace.get("_closed"):
        trace[name] = monotonic_ns()


def timestamp(name):
    trace = _trace.get()
    return trace.get(name) if trace and not trace.get("_closed") else None


def record_interval(stage, started, *, outcome="completed"):
    trace = _trace.get()
    if trace is not None and not trace.get("_closed"):
        ended = monotonic_ns()
        trace["spans"].append({"stage": stage, "start_ns": started, "end_ns": ended,
            "duration_ms": (ended-started)/1_000_000, "outcome": outcome})


@contextmanager
def span(stage):
    trace = _trace.get()
    if trace is None or trace.get("_closed"):
        yield
        return
    started = monotonic_ns()
    outcome = "completed"
    try:
        yield
    except BaseException:
        outcome = "failed"
        raise
    finally:
        ended = monotonic_ns()
        if not trace.get("_closed"):
            trace["spans"].append({"stage": stage, "start_ns": started, "end_ns": ended,
                "duration_ms": (ended - started) / 1_000_000, "outcome": outcome})


def timed(stage):
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            with span(stage):
                return await function(*args, **kwargs)
        return wrapped
    return decorate


def observe_admission(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        trace = {"clock": "server_monotonic", "clock_source": "python.perf_counter_ns", "gateway_started_ns": monotonic_ns(), "spans": []}
        token = _trace.set(trace)
        try:
            result = await function(*args, **kwargs)
            trace["gateway_finished_ns"] = monotonic_ns()
            trace["gateway_duration_ms"] = (trace["gateway_finished_ns"] - trace["gateway_started_ns"]) / 1_000_000
            # Replays keep the original durable commit timestamp but measure
            # this delivery's work independently of the original trace.
            result.output["admission_timing"] = {**result.output.get("admission_timing", {}), **trace,
                "commit_is_original": bool(result.output.get("replayed"))}
            return result
        except BaseException as error:
            ended = monotonic_ns()
            logger.info(json.dumps({"event": "admission_timing_failed", **trace,
                "gateway_finished_ns": ended,
                "gateway_duration_ms": (ended-trace["gateway_started_ns"])/1_000_000,
                "error_type": type(error).__name__, "session_id": kwargs.get("session_id"),
                "generation": kwargs.get("generation"), "provider_call_id": kwargs.get("call_id")}, separators=(",", ":")))
            raise
        finally:
            trace["_closed"] = True
            _trace.reset(token)
    return wrapped
