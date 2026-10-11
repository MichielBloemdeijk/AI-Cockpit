"""Bounded, loop-local diagnostic work; never task or publication authority."""
import asyncio
from collections import deque
from contextvars import Context
from copy import deepcopy
from dataclasses import dataclass, field
import logging
import math
from statistics import median
from time import perf_counter
from weakref import WeakKeyDictionary

logger = logging.getLogger(__name__)


@dataclass
class _State:
    queue: asyncio.Queue
    flush: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    accepting: bool = True
    accepted: int = 0
    completed: int = 0
    dropped: int = 0
    failed: int = 0
    in_flight: int = 0
    batches: int = 0
    queue_wait_ms: deque = field(default_factory=lambda: deque(maxlen=1024))
    batch_write_ms: deque = field(default_factory=lambda: deque(maxlen=1024))


class ObservationQueue:
    def __init__(self, writer, *, capacity=512, batch_size=32, batch_delay=.02):
        if capacity < 1 or batch_size < 1 or batch_delay < 0:
            raise ValueError("Invalid observation queue bounds")
        self.writer = writer
        self.capacity, self.batch_size, self.batch_delay = capacity, batch_size, batch_delay
        self._states = WeakKeyDictionary()

    def start(self):
        loop = asyncio.get_running_loop()
        state = self._states.get(loop)
        if state is None:
            state = _State(asyncio.Queue(maxsize=self.capacity))
            self._states[loop] = state
            # Do not inherit request traces, content or cancellation scopes.
            state.task = loop.create_task(self._run(state), name="result-observations", context=Context())
        return state

    def submit(self, job):
        state = self.start()
        if not state.accepting or state.queue.full():
            state.dropped += 1
            # Counts only: no payloads, exception strings or credentials.
            if state.dropped & (state.dropped-1) == 0:
                logger.warning("observation_queue_dropped count=%d", state.dropped)
            return False
        state.queue.put_nowait((deepcopy(job), perf_counter()))
        state.accepted += 1
        if state.queue.qsize() >= self.batch_size:
            state.flush.set()
        return True

    def status(self):
        state = self._states.get(asyncio.get_running_loop())
        if state is None:
            return dict(accepted=0, completed=0, dropped=0, failed=0, pending=0, batches=0,
                queue_wait=None, batch_write=None)
        def timing(values):
            ordered = sorted(values)
            if not ordered:
                return None
            return dict(sample_count=len(ordered), median_ms=median(ordered),
                p95_ms=ordered[math.ceil(.95*len(ordered))-1], max_ms=ordered[-1])
        return dict(accepted=state.accepted, completed=state.completed, dropped=state.dropped,
            failed=state.failed, pending=state.queue.qsize()+state.in_flight, batches=state.batches,
            queue_wait=timing(state.queue_wait_ms), batch_write=timing(state.batch_write_ms))

    async def drain(self, *, timeout=2.):
        state = self._states.get(asyncio.get_running_loop())
        if state is None:
            return True
        state.flush.set()
        try:
            await asyncio.wait_for(state.queue.join(), timeout)
            return True
        except TimeoutError:
            logger.warning("observation_queue_drain_timeout pending=%d", state.queue.qsize()+state.in_flight)
            return False

    async def stop(self, *, timeout=2.):
        loop = asyncio.get_running_loop()
        state = self._states.get(loop)
        if state is None:
            return
        state.accepting = False
        await self.drain(timeout=timeout)
        state.task.cancel()
        # Finish rollback/connection cleanup before the database is disposed.
        await asyncio.gather(state.task, return_exceptions=True)
        while not state.queue.empty():
            state.queue.get_nowait()
            state.queue.task_done()
            state.dropped += 1
        if state.dropped or state.failed:
            logger.warning("observation_queue_stopped dropped=%d failed=%d", state.dropped, state.failed)
        self._states.pop(loop, None)

    async def _run(self, state):
        while True:
            batch = [await state.queue.get()]
            state.in_flight = 1
            try:
                if state.accepting and state.queue.qsize()+1 < self.batch_size:
                    try:
                        await asyncio.wait_for(state.flush.wait(), self.batch_delay)
                    except TimeoutError:
                        pass
                state.flush.clear()
                while len(batch) < self.batch_size and not state.queue.empty():
                    batch.append(state.queue.get_nowait())
                state.in_flight = len(batch)
                state.batches += 1
                started = perf_counter()
                state.queue_wait_ms.extend((started-queued)*1000 for _, queued in batch)
                try:
                    await self.writer([job for job, _ in batch])
                finally:
                    state.batch_write_ms.append((perf_counter()-started)*1000)
                state.completed += len(batch)
            except asyncio.CancelledError:
                state.dropped += len(batch)
                raise
            except Exception as error:
                state.failed += len(batch)
                logger.warning("observation_queue_batch_failed count=%d error_type=%s", len(batch), type(error).__name__)
            finally:
                state.in_flight = 0
                for _ in batch:
                    state.queue.task_done()
