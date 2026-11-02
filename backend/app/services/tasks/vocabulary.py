"""Status and control vocabularies of the task state machines.

Each state machine has one enum; the named sets below are the only place a
category of statuses is spelled out. Values are ``StrEnum`` members, so they
compare equal to the strings stored in the database and sent over the API.
"""
from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    queued = "queued"
    waiting_for_worker = "waiting_for_worker"
    running = "running"
    waiting_for_input = "waiting_for_input"
    waiting_until = "waiting_until"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"
    cancelled = "cancelled"


class RunStatus(StrEnum):
    """Status of one execution run. Worker availability is a task-level hint:
    a run stays ``queued`` while its task waits for a worker."""
    pending = "pending"  # a follow-up turn waiting behind the current run
    queued = "queued"
    running = "running"
    waiting_for_input = "waiting_for_input"
    waiting_until = "waiting_until"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"
    cancelled = "cancelled"


class ControlAction(StrEnum):
    set_importance = "set_importance"
    yield_ = "yield"
    suspend = "suspend"
    resume = "resume"
    cancel = "cancel"
    restart_execution = "restart_execution"
    retry = "retry"
    restart_from_beginning = "restart_from_beginning"


class ControlState(StrEnum):
    requested = "requested"  # waits for the runtime's next safe boundary
    stop_requested = "stop_requested"  # waits for proof that the writers stopped
    confirmed = "confirmed"


ACTIVE_TASK_STATUSES = frozenset({
    TaskStatus.queued, TaskStatus.waiting_for_worker, TaskStatus.running,
    TaskStatus.waiting_for_input, TaskStatus.waiting_until, TaskStatus.paused,
})
TERMINAL_TASK_STATUSES = frozenset({
    TaskStatus.completed, TaskStatus.failed, TaskStatus.interrupted, TaskStatus.cancelled,
})
RETRYABLE_TASK_STATUSES = frozenset({TaskStatus.failed, TaskStatus.interrupted, TaskStatus.cancelled})
# Ended without the owner's intent: worth reporting or repairing.
UNSUCCESSFUL_TASK_STATUSES = frozenset({TaskStatus.failed, TaskStatus.interrupted})
# The owner stopped the task; background orchestration leaves it alone.
OWNER_HELD_TASK_STATUSES = frozenset({TaskStatus.paused, TaskStatus.cancelled})
# Admitted but not yet claimed by a worker.
WAITING_TO_RUN_STATUSES = frozenset({TaskStatus.queued, TaskStatus.waiting_for_worker})
TERMINAL_RUN_STATUSES = frozenset({RunStatus.completed, RunStatus.failed, RunStatus.interrupted, RunStatus.cancelled})

TASK_IMPORTANCE = ("low", "normal", "high")
# Why an attempt stopped before reacquiring a shared target (task.status_reason).
# A changed target may be retried once the owner accepts the change; one that
# cannot be observed is never accepted blindly.
RESOURCE_CHANGED_REASON = "shared_resource_changed"
RESOURCE_UNOBSERVABLE_REASON = "shared_resource_unobservable"
# Controls that stop a running attempt; they confirm once its writers stopped.
STOPPING_CONTROLS = frozenset({
    ControlAction.yield_, ControlAction.suspend, ControlAction.cancel,
    ControlAction.restart_execution, ControlAction.restart_from_beginning,
})
# Controls that stop by default rather than at the next safe boundary.
IMMEDIATE_CONTROLS = frozenset({
    ControlAction.cancel, ControlAction.restart_execution, ControlAction.restart_from_beginning,
})
# The subset background orchestration may apply; it never overrides a user's
# pause, cancellation or restart.
ORCHESTRATION_CONTROLS = (
    ControlAction.retry, ControlAction.set_importance, ControlAction.suspend, ControlAction.yield_,
)
CONTROL_ACTIONS = tuple(action.value for action in ControlAction)
