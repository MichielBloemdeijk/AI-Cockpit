"""Headers for private responses and a write-rate guard for browser state writes."""
from __future__ import annotations

from collections import OrderedDict, deque
from hashlib import sha256
import time

from fastapi import HTTPException, Request

from app.services.jarvis_devices import require_unlocked_device

PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow",
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
_write_windows: OrderedDict[str, deque] = OrderedDict()


async def protect_state_write(request: Request):
    """Bound abuse without retaining cookies/device credentials in memory/logs."""
    await require_unlocked_device(request)
    identity = request.headers.get("authorization") or request.cookies.get("cockpit_session") or (request.client.host if request.client else "local")
    key = sha256(identity.encode()).hexdigest()
    now = time.monotonic()
    times = _write_windows.setdefault(key, deque())
    _write_windows.move_to_end(key)
    while times and times[0] <= now - 60:
        times.popleft()
    if len(times) >= 120:
        raise HTTPException(429, "Too many app state writes", headers={"Retry-After": "60"})
    times.append(now)
    while len(_write_windows) > 4096:
        _write_windows.popitem(last=False)
    if len(await request.body()) > 256 * 1024:
        raise HTTPException(413, "App state request too large")
