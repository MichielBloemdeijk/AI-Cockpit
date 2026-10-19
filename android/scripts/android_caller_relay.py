"""Live audio relay between the simulated user (app.selftest.caller) and the Android app.

The app exposes a debug-only loopback socket (AcceptanceAudioStream) reached through
`adb forward`: it accepts 16 kHz mono PCM as microphone input and emits its
playback PCM as JSON lines. The simulated user speaks/hears 24 kHz mono PCM.
"""
from __future__ import annotations

import audioop  # Python 3.12: resampling only
import base64
import json
import queue
import socket
import subprocess
import threading
import time
from pathlib import Path

MIC_RATE = 16_000
CALLER_RATE = 24_000
CHUNK_MS = 20


class Relay:
    def __init__(self, port: int, caller: subprocess.Popen, root: Path):
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.socket.settimeout(None)
        self.caller = caller
        self.root = root
        self.mic: queue.Queue[bytes] = queue.Queue()
        self.stop = threading.Event()
        self.events: list[dict] = []
        self.error: str | None = None
        self.caller_started = threading.Event()
        self.caller_closed = threading.Event()
        self._app_state = None
        self._caller_state = None

    def send_mic(self, pcm16k: bytes) -> None:
        for i in range(0, len(pcm16k), MIC_RATE * 2 * CHUNK_MS // 1000):
            self.mic.put(pcm16k[i:i + MIC_RATE * 2 * CHUNK_MS // 1000])

    def start(self) -> None:
        for target in (self._pace_mic, self._from_app, self._from_caller):
            threading.Thread(target=self._guard(target), daemon=True).start()

    def _guard(self, target):
        def run():
            try:
                target()
            except Exception as error:  # surfaced to the driver; the relay never retries
                if not self.stop.is_set():
                    self.error = f"{target.__name__}: {type(error).__name__}: {error}"
                    self.stop.set()
        return run

    def _pace_mic(self) -> None:
        """Real-time pacing: the app consumes microphone audio at 16 kHz."""
        next_tick = time.monotonic()
        while not self.stop.is_set():
            next_tick += CHUNK_MS / 1000
            try:
                self.socket.sendall(self.mic.get_nowait())
            except queue.Empty:
                pass
            time.sleep(max(0, next_tick - time.monotonic()))

    def _from_app(self) -> None:
        reader = self.socket.makefile("r", encoding="utf-8")
        for line in reader:
            if self.stop.is_set():
                return
            message = json.loads(line)
            pcm = base64.b64decode(message["pcm"])
            if message.get("channels", 1) == 2:
                pcm = audioop.tomono(pcm, 2, .5, .5)
            pcm, self._app_state = audioop.ratecv(pcm, 2, 1, message["rate"], CALLER_RATE, self._app_state)
            if self.caller.stdin and not self.caller_closed.is_set():
                self.caller.stdin.write(json.dumps({"type": "audio", "audio": base64.b64encode(pcm).decode()}) + "\n")
                self.caller.stdin.flush()

    def _from_caller(self) -> None:
        with (self.root / "caller-events.jsonl").open("a", encoding="utf-8") as log:
            for line in self.caller.stdout:
                event = json.loads(line)
                if event.get("type") == "session.output_audio.delta":
                    pcm = base64.b64decode(event["delta"])
                    pcm, self._caller_state = audioop.ratecv(pcm, 2, 1, CALLER_RATE, MIC_RATE, self._caller_state)
                    self.send_mic(pcm)
                    continue
                log.write(json.dumps({"epoch_ms": time.time() * 1000, "event": event}) + "\n")
                if event.get("type") == "session.started":
                    self.caller_started.set()
                if event.get("type") == "session.closed":
                    self.caller_closed.set()
                if event.get("type") == "error":
                    raise RuntimeError("Simulated user provider error: " + json.dumps(event)[:300])

    def close(self) -> None:
        if self.caller.poll() is None and self.caller.stdin:
            try:
                self.caller.stdin.write(json.dumps({"type": "close"}) + "\n"); self.caller.stdin.flush()
            except OSError:
                pass
            self.caller_closed.wait(15)
        self.stop.set()
        try:
            self.socket.close()
        except OSError:
            pass
        try:
            self.caller.wait(60)  # the simulated user writes its questionnaire after closing
        except subprocess.TimeoutExpired:
            self.caller.kill()


class MicStream:
    """Scripted speech over the same live stream (no simulated user); app output is drained."""

    def __init__(self, port: int):
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.socket.settimeout(None)
        self.mic: queue.Queue[bytes] = queue.Queue()
        self.stop = threading.Event()
        self.queued_ms = 0.0
        self.lock = threading.Lock()
        threading.Thread(target=self._pace, daemon=True).start()
        threading.Thread(target=self._drain, daemon=True).start()

    def send(self, pcm16k: bytes) -> float:
        """Queue speech; returns the epoch ms at which it will have been fully sent."""
        step = MIC_RATE * 2 * CHUNK_MS // 1000
        with self.lock:
            for i in range(0, len(pcm16k), step):
                self.mic.put(pcm16k[i:i + step])
            self.queued_ms = max(self.queued_ms, time.time() * 1000) + len(pcm16k) / 32
            return self.queued_ms

    def _pace(self):
        next_tick = time.monotonic()
        while not self.stop.is_set():
            next_tick += CHUNK_MS / 1000
            try:
                self.socket.sendall(self.mic.get_nowait())
            except queue.Empty:
                pass
            except OSError:
                return
            time.sleep(max(0, next_tick - time.monotonic()))

    def _drain(self):
        try:
            for _ in self.socket.makefile("r", encoding="utf-8"):
                if self.stop.is_set():
                    return
        except OSError:
            return

    def close(self):
        self.stop.set()
        try:
            self.socket.close()
        except OSError:
            pass
