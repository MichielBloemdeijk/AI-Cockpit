from __future__ import annotations

from array import array
import io
import wave

import pytest

from app.config import settings
from app.services.voice import InvalidVoiceAudio, VoiceService, sanitize_voice_response, strip_wake_word, validate_wav_audio


def _wav(*, sample_rate: int = 16_000, channels: int = 1, seconds: float = 0.25) -> bytes:
    frames = int(sample_rate * seconds)
    raw = array("h", [0] * frames * channels).tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(raw)
    return output.getvalue()


def test_validate_wav_audio_requires_mono_pcm16_at_16khz(monkeypatch) -> None:
    monkeypatch.setattr(settings, "voice_max_segment_seconds", 1.0)

    info = validate_wav_audio(_wav())

    assert info.frame_rate == 16_000
    assert info.channels == 1
    assert info.sample_width == 2
    assert info.duration_seconds == pytest.approx(0.25, abs=0.01)

    with pytest.raises(InvalidVoiceAudio, match="16 kHz"):
        validate_wav_audio(_wav(sample_rate=8_000))
    with pytest.raises(InvalidVoiceAudio, match="mono"):
        validate_wav_audio(_wav(channels=2))


def test_validate_wav_audio_rejects_overlong_segments(monkeypatch) -> None:
    monkeypatch.setattr(settings, "voice_max_segment_seconds", 0.1)

    with pytest.raises(InvalidVoiceAudio, match="duration"):
        validate_wav_audio(_wav(seconds=0.25))


def test_strip_wake_word_accepts_first_three_words_and_returns_command() -> None:
    assert strip_wake_word("Jarvis, check my active tasks") == ("check my active tasks", True)
    assert strip_wake_word("hey Jarvis: what changed?") == ("what changed?", True)
    assert strip_wake_word("please do now Jarvis do this") == ("please do now Jarvis do this", False)
    assert strip_wake_word("Jarvis") == ("", True)


def test_sanitize_voice_response_removes_markup_and_caps_spoken_length() -> None:
    response = sanitize_voice_response("**Done** — see [the report](https://example.test/report). " + "word " * 60)

    assert "**" not in response
    assert "https://" not in response
    assert len(response.rstrip(".").split()) == 45


def test_kokoro_result_is_serialized_as_wav() -> None:
    class FakeKokoro:
        def create(self, text, *, voice, speed, lang):
            assert text == "Hello"
            assert voice == "af_heart"
            assert speed == 1.05
            assert lang == "en-us"
            return array("h", [0, 1000, -1000]), 24_000

    audio = VoiceService._synthesize_with_model(FakeKokoro(), "Hello", "af_heart", 1.05)
    with wave.open(io.BytesIO(audio), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 24_000
        assert wav_file.getnframes() == 3
