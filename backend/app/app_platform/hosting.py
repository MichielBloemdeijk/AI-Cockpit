"""How app UIs are served; shared by the real host and the package check."""
from __future__ import annotations

from pathlib import Path

SDK_FILE = Path(__file__).parent / "sdk" / "cockpit-app.js"
# App UIs run in an opaque-origin sandbox and may not call the network
# themselves; they reach their data through the Cockpit page's bridge.
HOST_CSP = (
    "sandbox allow-scripts allow-forms allow-popups allow-modals; default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "font-src 'self' data:; connect-src 'none'; frame-ancestors 'self'"
)
