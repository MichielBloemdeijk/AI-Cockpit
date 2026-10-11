"""Opt-in local provider output recording, outside the admission write path.

Only provider-exposed summaries are retained; opaque encrypted reasoning,
credentials and media are excluded. Bounded buffers are checkpointed at provider
termination and written on adapter close.
"""
import asyncio
import hashlib
import json
import logging
from pathlib import Path
from time import perf_counter_ns
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
MAX_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 20_000
RESPONSE_EVENTS = {
    "response.created", "response.in_progress", "response.completed", "response.done", "response.failed",
    "response.incomplete", "response.cancelled", "response.output_item.added", "response.output_item.done",
    "response.content_part.added", "response.content_part.done", "response.output_text.delta", "response.output_text.done",
    "response.function_call_arguments.delta", "response.function_call_arguments.done",
    "response.reasoning_summary_part.added", "response.reasoning_summary_part.done",
    "response.reasoning_summary_text.delta", "response.reasoning_summary_text.done",
}
VOICE_EVENTS = {"session.input_transcript.delta", "session.input_transcript.done", "session.output_transcript.delta",
    "session.delegation.created", "session.closed", "session.started", "error"}


def clean(value):
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items() if key not in {
            "encrypted_content", "encrypted_signature", "authorization", "api_key", "client_secret", "audio", "sdp",
            "instructions", "tools", "input", "metadata"}}
    if isinstance(value, list):
        return [clean(item) for item in value]
    return value


def tool_metadata(tools):
    return [{"name": tool.get("name"), "strict": tool.get("strict"),
        "required": (tool.get("parameters") or {}).get("required", []),
        "properties": list((tool.get("parameters") or {}).get("properties", {}))}
        for tool in tools if isinstance(tool, dict) and tool.get("type") == "function"]


class LiveModelDiagnostics:
    def __init__(self, folder, *, session_id, generation, configuration):
        self.folder = Path(folder).resolve()
        if not self.folder.is_relative_to((ROOT / "data").resolve()) or self.folder == (ROOT / "data").resolve():
            raise ValueError("Live diagnostics require a scoped project data directory")
        self.path = self.folder / f"{hashlib.sha256(str(session_id).encode()).hexdigest()[:16]}-{generation}-{uuid4().hex}.json"
        self.header = {"schema": "jarvis-live-model-diagnostics.v1", "session_id": session_id, "generation": generation,
            "configuration": configuration, "clock": "python.perf_counter_ns", "raw_reasoning_available": False}
        self.events = []
        self.bytes = 0
        self.dropped = 0
        self.closed = False
        self.write_lock = asyncio.Lock()

    def record(self, stream, event, **identity):
        if self.closed:
            return
        kind = str(event.get("type") or "")
        if kind not in RESPONSE_EVENTS | VOICE_EVENTS and not kind.startswith("selector."):
            return
        stamp = perf_counter_ns()
        row = {"observed_ns": stamp, "stream": stream, **identity, "event": clean(event)}
        if isinstance(event.get("response"), dict) and isinstance(event["response"].get("tools"), list):
            row["returned_tools"] = tool_metadata(event["response"]["tools"])
        encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        size = len(encoded.encode("utf-8"))
        if len(self.events) >= MAX_EVENTS or self.bytes + size > MAX_BYTES:
            self.dropped += 1
            return
        # Snapshot mutable provider objects while recording the arrival stamp.
        self.events.append(json.loads(encoded))
        self.bytes += size

    async def flush(self, reason):
        if self.closed:
            return
        self.closed = True
        await self.checkpoint(reason)

    async def checkpoint(self, reason):
        """Keep terminal-provider evidence even if coordinator cleanup rejects a stale session."""
        async with self.write_lock:
            await self._write_snapshot(reason)

    async def _write_snapshot(self, reason):
        document = {**self.header, "close_reason": reason, "events": list(self.events),
            "final_adapter_flush": self.closed,
            "capture_health": {"events": len(self.events), "bytes": self.bytes, "dropped": self.dropped,
                "complete_capture": self.dropped == 0, "limits": {"bytes": MAX_BYTES, "events": MAX_EVENTS}}}
        def write():
            self.folder.mkdir(parents=True, exist_ok=True)
            # Recheck after resolving any existing directory links.
            if not self.folder.resolve().is_relative_to((ROOT / "data").resolve()):
                raise ValueError("Diagnostic path escaped project data")
            temporary = self.path.with_suffix(".partial")
            temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.path)
        try:
            task = asyncio.create_task(asyncio.to_thread(write))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
        except Exception as error:
            logging.getLogger(__name__).warning("Live diagnostic output failed (%s)", type(error).__name__)
