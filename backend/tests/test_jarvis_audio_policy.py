from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import RealtimeAudioPolicy, Settings, build_realtime_audio_policy


def test_policy_builder_assigns_one_commit_owner_for_each_mode() -> None:
    server = build_realtime_audio_policy(Settings(jarvis_realtime_turn_mode="server_vad"))
    assert server.configuration_id == "server_vad:s350:t0.5:p300"
    assert server.commit_owner == "provider"
    assert server.provider_turn_detection() == {
        "type": "server_vad",
        "threshold": 0.5,
        "prefix_padding_ms": 300,
        "silence_duration_ms": 350,
        "create_response": False,
        "interrupt_response": False,
    }

    semantic = build_realtime_audio_policy(Settings(jarvis_realtime_turn_mode="semantic_vad"))
    assert semantic.configuration_id == "semantic_vad:emedium"
    assert semantic.commit_owner == "provider"
    assert semantic.provider_turn_detection() == {
        "type": "semantic_vad",
        "eagerness": "medium",
        "create_response": False,
        "interrupt_response": False,
    }

    client = build_realtime_audio_policy(Settings(jarvis_realtime_turn_mode="client_vad"))
    assert client.configuration_id == "client_vad:v1"
    assert client.commit_owner == "client"
    assert client.provider_turn_detection() is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"jarvis_realtime_server_vad_silence_ms": 99},
        {"jarvis_realtime_server_vad_silence_ms": 2_001},
        {"jarvis_realtime_server_vad_threshold": -0.01},
        {"jarvis_realtime_server_vad_threshold": 1.01},
        {"jarvis_realtime_server_vad_prefix_ms": -1},
        {"jarvis_realtime_server_vad_prefix_ms": 2_001},
    ],
)
def test_server_vad_ranges_are_rejected_before_provider_setup(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(**kwargs)


def test_policy_rejects_dual_ownership() -> None:
    with pytest.raises(ValueError):
        RealtimeAudioPolicy(
            configuration_id="invalid",
            turn_mode="server_vad",
            commit_owner="client",
            silence_duration_ms=350,
            vad_threshold=0.5,
            prefix_padding_ms=300,
            semantic_eagerness=None,
        )
