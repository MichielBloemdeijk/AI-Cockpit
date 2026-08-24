"""Local speech pipeline contracts for the Jarvis voice POC.

The heavyweight STT and TTS dependencies are deliberately imported lazily. A
backend can therefore start and expose an actionable health response before
the optional model packages and checkpoints have been installed.
"""
from __future__ import annotations

import asyncio
from array import array
from dataclasses import dataclass
import importlib.util
import io
import re
from pathlib import Path
import tempfile
import time
import wave
from typing import Any

import httpx

from app.config import settings


class VoiceDependencyUnavailable(RuntimeError):
    """Raised when an optional local speech dependency is not installed."""


class InvalidVoiceAudio(ValueError):
    """Raised when a browser upload is not a supported 16 kHz PCM WAV."""


@dataclass(frozen=True, slots=True)
class WavInfo:
    duration_seconds: float
    frame_rate: int
    channels: int
    sample_width: int
    frames: int


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    transcript: str
    duration_seconds: float
    inference_ms: float


def validate_wav_audio(audio: bytes) -> WavInfo:
    if not audio:
        raise InvalidVoiceAudio("Audio upload is empty")
    if len(audio) > settings.voice_max_upload_bytes:
        raise InvalidVoiceAudio("Audio upload exceeds the configured size limit")

    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            info = WavInfo(
                duration_seconds=wav.getnframes() / max(wav.getframerate(), 1),
                frame_rate=wav.getframerate(),
                channels=wav.getnchannels(),
                sample_width=wav.getsampwidth(),
                frames=wav.getnframes(),
            )
            compression = wav.getcomptype()
    except (EOFError, wave.Error) as exc:
        raise InvalidVoiceAudio("Upload is not a readable WAV file") from exc

    if compression != "NONE":
        raise InvalidVoiceAudio("Only uncompressed PCM WAV audio is supported")
    if info.frame_rate != 16_000:
        raise InvalidVoiceAudio("WAV audio must use a 16 kHz sample rate")
    if info.channels != 1 or info.sample_width != 2:
        raise InvalidVoiceAudio("WAV audio must be mono PCM16")
    if info.frames <= 0 or info.duration_seconds <= 0:
        raise InvalidVoiceAudio("WAV audio contains no speech frames")
    if info.duration_seconds > settings.voice_max_segment_seconds:
        raise InvalidVoiceAudio("WAV audio exceeds the configured segment duration")
    return info


def strip_wake_word(transcript: str, wake_word: str | None = None) -> tuple[str, bool]:
    """Return the command after a wake word found in the first three words."""
    text = " ".join(str(transcript or "").split()).strip()
    configured = " ".join((wake_word or settings.voice_wake_word).split()).strip()
    if not text or not configured:
        return text, False

    words = list(re.finditer(r"[\w']+", text, flags=re.UNICODE))
    wake_tokens = re.findall(r"[\w']+", configured, flags=re.UNICODE)
    if not words or not wake_tokens:
        return text, False

    # A multi-token wake phrase must still begin within the first three
    # recognized words, while allowing punctuation between its tokens.
    for start in range(min(3, len(words))):
        candidate = [match.group(0).casefold() for match in words[start : start + len(wake_tokens)]]
        if candidate != [token.casefold() for token in wake_tokens]:
            continue
        command_start = words[start + len(wake_tokens) - 1].end()
        return text[command_start:].lstrip(" ,:;.!?-\n\t"), True
    return text, False


def sanitize_voice_response(text: str, *, max_words: int = 45) -> str:
    """Keep spoken responses short, plain-text, and safe to send to TTS."""
    value = str(text or "")
    value = re.sub(r"```(?:\w+)?\s*.*?```", "", value, flags=re.DOTALL)
    value = re.sub(r"https?://\S+", "", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"[*_`>#]", "", value)
    value = " ".join(value.replace("\r", " ").replace("\n", " ").split()).strip()
    if not value:
        return ""
    words = value.split()
    if len(words) <= max_words:
        return value
    return " ".join(words[:max_words]).rstrip(" ,;:") + "."


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _resolve_backend_path(raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else settings.backend_root / path


class VoiceService:
    def __init__(self) -> None:
        self._stt_model: Any | None = None
        self._tts_model: Any | None = None
        self._stt_lock = asyncio.Lock()
        self._tts_lock = asyncio.Lock()
        self._stt_inference_lock = asyncio.Lock()
        self._tts_inference_lock = asyncio.Lock()
        self._warm_task: asyncio.Task[None] | None = None
        self._stt_state = "disabled" if not settings.voice_enabled else "loading"
        self._tts_state = "disabled" if not settings.voice_enabled or not settings.voice_tts_enabled else "loading"
        self._stt_error: str | None = None
        self._tts_error: str | None = None
        self._stt_load_ms: float | None = None
        self._tts_load_ms: float | None = None

    async def start(self) -> None:
        if self._warm_task is None or self._warm_task.done():
            self._warm_task = asyncio.create_task(self._warm(), name="voice-model-warmup")

    async def stop(self) -> None:
        if self._warm_task is not None and not self._warm_task.done():
            self._warm_task.cancel()
            await asyncio.gather(self._warm_task, return_exceptions=True)
        self._warm_task = None

    async def _warm(self) -> None:
        if not settings.voice_enabled:
            return
        started = time.perf_counter()
        try:
            await self._get_stt_model()
            self._stt_state = "ready"
            self._stt_load_ms = round((time.perf_counter() - started) * 1000, 1)
        except Exception as exc:
            self._stt_state = "error"
            self._stt_error = str(exc)
        if settings.voice_tts_enabled:
            started = time.perf_counter()
            try:
                await self._get_tts_model()
                self._tts_state = "ready"
                self._tts_load_ms = round((time.perf_counter() - started) * 1000, 1)
            except Exception as exc:
                self._tts_state = "error"
                self._tts_error = str(exc)

    async def health(self) -> dict[str, Any]:
        llm = {
            "configured": bool(settings.local_llm_enabled),
            "base_url": settings.local_llm_base_url,
            "model": settings.local_llm_alias,
            "ready": False,
            "state": "disabled" if not settings.local_llm_enabled else "loading",
            "latency_ms": None,
        }
        if settings.local_llm_enabled:
            started = time.perf_counter()
            try:
                async with httpx.AsyncClient(
                    base_url=settings.local_llm_base_url,
                    headers={"Authorization": f"Bearer {settings.local_llm_api_key}"},
                    timeout=httpx.Timeout(1.5, connect=0.5),
                ) as client:
                    response = await client.get("/models")
                llm["ready"] = response.is_success
                llm["state"] = "ready" if response.is_success else "error"
            except (httpx.HTTPError, OSError):
                llm["ready"] = False
                llm["state"] = "error"
            llm["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)

        stt_dependency = _module_available("faster_whisper")
        tts_dependency = _module_available("kokoro_onnx")
        tts_model_path = _resolve_backend_path(settings.voice_tts_model_path)
        tts_voices_path = _resolve_backend_path(settings.voice_tts_voices_path)
        return {
            "enabled": bool(settings.voice_enabled),
            "wake_word": settings.voice_wake_word,
            "llm": llm,
            "stt": {
                "configured": bool(settings.voice_enabled),
                "dependency_available": stt_dependency,
                "model": settings.voice_stt_model,
                "device": settings.voice_stt_device,
                "compute_type": settings.voice_stt_compute_type,
                "state": self._stt_state,
                "load_ms": self._stt_load_ms,
                "error": self._stt_error,
                "ready": self._stt_state == "ready",
            },
            "tts": {
                "configured": bool(settings.voice_enabled and settings.voice_tts_enabled),
                "dependency_available": tts_dependency,
                "model_present": tts_model_path.is_file() and tts_voices_path.is_file(),
                "voice": settings.voice_tts_voice,
                "state": self._tts_state,
                "load_ms": self._tts_load_ms,
                "error": self._tts_error,
                "ready": self._tts_state == "ready",
            },
        }

    async def transcribe(self, audio: bytes) -> TranscriptionResult:
        info = validate_wav_audio(audio)
        started = time.perf_counter()
        model = await self._get_stt_model()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            temp_file.write(audio)
            temp_path = Path(temp_file.name)
        try:
            async with self._stt_inference_lock:
                transcript = await asyncio.to_thread(self._transcribe_path, model, temp_path)
        finally:
            temp_path.unlink(missing_ok=True)
        return TranscriptionResult(
            transcript=transcript,
            duration_seconds=round(info.duration_seconds, 3),
            inference_ms=round((time.perf_counter() - started) * 1000, 1),
        )

    async def synthesize(self, text: str, *, voice: str | None = None, speed: float | None = None) -> tuple[bytes, float]:
        if not settings.voice_enabled or not settings.voice_tts_enabled:
            raise VoiceDependencyUnavailable("Local TTS is disabled")
        started = time.perf_counter()
        model = await self._get_tts_model()
        async with self._tts_inference_lock:
            wav_bytes = await asyncio.to_thread(
                self._synthesize_with_model,
                model,
                text.strip(),
                voice or settings.voice_tts_voice,
                float(speed if speed is not None else settings.voice_tts_speed),
            )
        return wav_bytes, round((time.perf_counter() - started) * 1000, 1)

    async def _get_stt_model(self) -> Any:
        if not settings.voice_enabled:
            raise VoiceDependencyUnavailable("Local voice is disabled")
        async with self._stt_lock:
            if self._stt_model is not None:
                return self._stt_model
            if not _module_available("faster_whisper"):
                raise VoiceDependencyUnavailable(
                    "faster-whisper is not installed; install the local voice dependencies first"
                )
            from faster_whisper import WhisperModel

            self._stt_model = await asyncio.to_thread(
                WhisperModel,
                settings.voice_stt_model,
                device=settings.voice_stt_device,
                compute_type=settings.voice_stt_compute_type,
            )
            self._stt_state = "ready"
            return self._stt_model

    async def _get_tts_model(self) -> Any:
        async with self._tts_lock:
            if self._tts_model is not None:
                return self._tts_model
            if not _module_available("kokoro_onnx"):
                raise VoiceDependencyUnavailable(
                    "kokoro-onnx is not installed; install the local voice dependencies first"
                )
            model_path = _resolve_backend_path(settings.voice_tts_model_path)
            voices_path = _resolve_backend_path(settings.voice_tts_voices_path)
            if not model_path.is_file() or not voices_path.is_file():
                raise VoiceDependencyUnavailable("Kokoro model and voice files are not installed")
            from kokoro_onnx import Kokoro

            self._tts_model = await asyncio.to_thread(Kokoro, str(model_path), str(voices_path))
            self._tts_state = "ready"
            return self._tts_model

    @staticmethod
    def _transcribe_path(model: Any, path: Path) -> str:
        segments, _info = model.transcribe(
            str(path),
            language=settings.voice_stt_language,
            beam_size=settings.voice_stt_beam_size,
            vad_filter=True,
        )
        return " ".join(str(segment.text).strip() for segment in segments if str(segment.text).strip()).strip()

    @staticmethod
    def _synthesize_with_model(model: Any, text: str, voice: str, speed: float) -> bytes:
        samples, sample_rate = model.create(text, voice=voice, speed=speed, lang="en-us")
        try:
            # kokoro-onnx returns float32 samples in [-1, 1]. Convert them to
            # the PCM16 payload expected by the browser audio element.
            clipped = samples.clip(-1.0, 1.0)
            pcm_bytes = (clipped * 32767).astype("int16").tobytes()
        except AttributeError:
            pcm_bytes = array(
                "h",
                [int(max(-1.0, min(1.0, float(sample))) * 32767) for sample in samples],
            ).tobytes()
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(int(sample_rate))
            wav.writeframes(pcm_bytes)
        return output.getvalue()


voice_service = VoiceService()
