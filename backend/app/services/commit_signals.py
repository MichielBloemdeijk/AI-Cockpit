"""In-process commit wakeups; the database remains the recovery authority.

Each reader snapshots an Event before reading. Writers set and replace it only
after commit. This broadcasts to every waiter without clear()/subscribe races.
Other processes and missed signals are recovered by bounded database rescans.
"""
import asyncio
from weakref import WeakKeyDictionary, ref


class CommitSignal:
    def __init__(self):
        self._events = WeakKeyDictionary()

    def snapshot(self):
        loop = asyncio.get_running_loop()
        reference = self._events.get(loop)
        event = reference() if reference is not None else None
        if event is None:
            event = asyncio.Event()
            self._events[loop] = ref(event)
        return event

    def notify(self):
        # Synchronous maintenance writers may have no event loop. Their changes
        # are still visible to the periodic recovery read in each consumer.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        reference = self._events.pop(loop, None)
        event = reference() if reference is not None else None
        if event is not None:
            event.set()

    async def wait(self, observed, *, timeout):
        if observed.is_set():
            return
        try:
            await asyncio.wait_for(observed.wait(), timeout=max(0., timeout))
        except TimeoutError:
            pass


live_event_committed = CommitSignal()
capture_state_committed = CommitSignal()
result_event_committed = CommitSignal()
