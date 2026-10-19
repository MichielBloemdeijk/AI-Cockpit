"""Network-free tests of actual failure modes found in the recorded screen."""
from copy import deepcopy
import json
import wave

import pytest

from app.announcement_eval import assessment as a
from app.announcement_eval.focused import matrix, schedule, calibration_fixtures, PROFILES, recommendation
from app.announcement_eval.profiles import profile, append_commands, preference_live_prompt


def fixture(text="Your Hello World page is ready.", kind="outcome", expectation="required"):
    words = [{"word": word, "start": 2 + i * .2, "end": 2.2 + i * .2} for i, word in enumerate(text.split())]
    utterance = {"start_word": 0, "end_word": len(words)-1, "kind": kind,
        "outcome": "completed" if kind == "outcome" else "none", "task_id": "t" if kind == "outcome" else "",
        "complete": kind == "outcome", "relevant": True, "explanation": "fixture"}
    evidence = {"input_understood": True, "clock_valid": True, "recording_complete": True,
        "assistant_audio_epoch_ms": 1000, "expectation": expectation, "profile": PROFILES[0],
        "target_task_ids": ["t"], "eligible_ms": {"t": 2000}, "caller_voiced_windows": [],
        "state": {"tasks": [{"id": "t", "status": "completed", "finished_at": 2000}],
            "presentations": [{"id": "p", "task_id": "t", "state": "pending"}],
            "provider_events": [{"stream": "incoming", "event": {"type": "session.output_transcript.delta", "delta": text}},
                {"stream": "outgoing", "epoch_ms": 2050, "event": {"event_id": "jarvis-live-update:1", "type": "session.thinking.append",
                    "content": json.dumps({"task_id": "t", "event_type": "task.completed", "notification": {"intent": "requested"}})}},
                {"stream": "incoming", "epoch_ms": 2100, "event": {"type": "session.thinking.appended", "client_event_id": "jarvis-live-update:1"}}]},
        "capture": {"rate": 24000, "playback": [{"audio_start_ms": 2500+i*50, "audio_end_ms": 2550+i*50,
            "position": i*.05, "muted": False, "volume": 1, "track_id": "one", "paused": False, "track_live": True} for i in range(60)]}}
    return evidence, {"words": words}, {"utterances": [utterance]}


def test_complete_selection_does_not_hide_missing_ack():
    result = a.assess(*fixture())
    assert result["selection_status"] == "passed"
    assert result["interaction_status"] == "failed"


def test_contextual_complete_is_not_rejected_for_using_the_adjective():
    result=a.assess(*fixture("It's complete."))
    assert result["selection_status"]=="passed"
    assert not result["dimensions"]["task_truth"]["unknowns"]


def test_trailing_synthetic_silence_does_not_extend_deadline():
    evidence,audio,judgment=fixture()
    tid=evidence["target_task_ids"][0]
    evidence["markers"]=[{"type":"caller.start","epoch_ms":1500},{"type":"caller.end","epoch_ms":3000}]
    evidence["caller_voiced_windows"]=[[1500,2250]]
    evidence["eligible_ms"][tid]=3000
    result=a.assess(evidence,audio,judgment)
    assert result["nominal_clock_origins"][tid]==3000
    assert result["notification_clock_origins"][tid]==2250
    assert result["conclusion"] == "selection_demonstrated_in_screening"
    assert result["dimensions"]["acknowledgment"]["failures"] == ["spoken_outcome_not_acknowledged:p"]


@pytest.mark.parametrize("text", ["Your task has been added.", "The task is queued.", "Done."])
def test_mislabeled_admission_cannot_qualify(text):
    result = a.assess(*fixture(text))
    assert result["selection_status"] == "inconclusive"
    assert result["dimensions"]["task_truth"]["unknowns"]


def test_admission_does_not_satisfy_requested_outcome():
    result = a.assess(*fixture("Your task has been added.", "admission"))
    assert result["selection_status"] == "failed"
    assert result["dimensions"]["notification_selection"]["failures"] == ["requested_outcome_not_spoken:t"]


def test_phantom_asr_words_checked_without_outcome_claim():
    evidence, audio, judgment = fixture("Thank you for watching.", "conversation", "optional")
    evidence["state"]["provider_events"][0]["event"]["delta"] = "Okay."
    assert a.assess(evidence, audio, judgment)["selection_status"] == "inconclusive"


def test_repetition_cannot_borrow_prior_transcript_words():
    words = [{"word": w} for w in "The page is ready The page is ready".split()]
    assert a.align_words(words, "The page is ready") == [1]*4+[0]*4


@pytest.mark.parametrize("field,value", [("muted", True), ("volume", 0), ("paused", True), ("track_live", False)])
def test_unplayed_audio_cannot_pass(field, value):
    evidence, audio, judgment = fixture()
    for p in evidence["capture"]["playback"]: p[field] = value
    result = a.assess(evidence, audio, judgment)
    assert result["selection_status"] == "failed" and result["dimensions"]["evidence_validity"]["failures"]


def test_playback_gap_and_legacy_flags_are_inconclusive():
    evidence, audio, judgment = fixture()
    evidence["capture"]["playback"] = evidence["capture"]["playback"][:12] + evidence["capture"]["playback"][26:]
    assert a.assess(evidence, audio, judgment)["selection_status"] == "inconclusive"
    evidence["capture"]["playback"] = [{"position": 2, "epoch_ms": 3300, "paused": False, "track_live": True}]
    assert a.assess(evidence, audio, judgment)["selection_status"] == "inconclusive"


def test_accepted_but_unspoken_is_a_selection_failure():
    evidence, _, _ = fixture()
    evidence["state"]["provider_events"][0]["event"]["delta"] = ""
    result = a.assess(evidence, {"words": []}, {"utterances": []})
    assert result["selection_status"] == "failed"
    assert result["dimensions"]["context_delivery"]["status"] == "passed"


def test_no_receipt_cannot_be_a_model_selection_failure():
    evidence, audio, judgment = fixture()
    evidence["state"]["provider_events"] = evidence["state"]["provider_events"][:-1]
    assert a.assess(evidence, audio, judgment)["selection_status"] == "inconclusive"


def test_premature_ack_release_blocker():
    evidence, audio, judgment = fixture()
    evidence["state"]["presentations"][0].update(state="handled", handled_by="model_report", handled_at=2100)
    result = a.assess(evidence, audio, judgment)
    assert result["interaction_status"] == "failed"
    assert result["dimensions"]["acknowledgment"]["failures"] == ["premature_handled_transition:p"]


def test_backchannel_vs_substantive_overlap():
    evidence, audio, judgment = fixture("Mm-hmm.", "backchannel", "optional")
    evidence["caller_voiced_windows"] = [[2800, 4000]]
    assert a.assess(evidence, audio, judgment)["dimensions"]["conversation_interruption"]["status"] == "passed"
    evidence, audio, judgment = fixture("Move your lamp to the left.", "conversation", "optional")
    evidence["caller_voiced_windows"] = [[2800, 4500]]
    result = a.assess(evidence, audio, judgment)
    assert result["selection_status"] == "passed"  # General talk is a separate dimension.
    assert result["dimensions"]["conversation_interruption"]["status"] == "failed"


def test_outcome_overlap_fails_selection():
    evidence, audio, judgment = fixture()
    evidence["caller_voiced_windows"] = [[2800, 4500]]
    result = a.assess(evidence, audio, judgment)
    assert result["selection_status"] == "failed"


def test_later_solicited_status_does_not_violate_silence():
    evidence, audio, judgment = fixture(expectation="forbidden")
    evidence["status_query_end_ms"] = 2800
    result = a.assess(evidence, audio, judgment)
    assert result["dimensions"]["notification_selection"]["status"] == "passed"


def test_partition_checks_unclassified_words():
    evidence, audio, judgment = fixture()
    judgment["utterances"][0]["end_word"] -= 1
    assert a.assess(evidence, audio, judgment)["selection_status"] == "inconclusive"


def test_exact_matrix_and_controls():
    assert len(matrix()) == 12 and len(schedule()) == 24
    assert len({(c["condition"], c["profile"], c["repetition"]) for c in schedule()}) == 24
    cases = {(c["id"], c["repetition"]): c for c in matrix()}
    for n in (1,2):
        ordinary = cases[("occupied", n)]["steps"]
        control = cases[("occupied_control", n)]["steps"]
        assert [s for s in ordinary if s["type"] != "finish"] == [s for s in control if s["type"] != "comparison"]
    assert len(calibration_fixtures()) == 12


def test_a_and_b_share_prompt_and_only_payload_changes():
    assert preference_live_prompt(profile(PROFILES[0])) == preference_live_prompt(profile(PROFILES[1]))
    command = {"type": "session.thinking.append", "event_id": "jarvis-live-update:1", "delegation_id": None,
        "content": json.dumps({"event_type": "task.completed", "task_id": "t", "goal": "Write a page", "summary": "The page is ready.",
            "presentation_id": "p", "exposure_token": "token"})}
    for intent in ("requested", "suppressed", "discretionary"):
        pref = {"intent": intent, "reason": "user preference", "revision": 1, "recorded": True}
        a_command = append_commands(command, profile(PROFILES[0]), pref)[0]
        b_command = append_commands(command, profile(PROFILES[1]), pref)[0]
        assert a_command["type"] == b_command["type"] == "session.thinking.append"
        data = json.loads(b_command["content"])
        assert data["notification"] == pref and data["summary"] == "The page is ready."
        assert data["presentation_id"] == "p" and data["exposure_token"] == "token"
        assert b_command["content"].count("The page is ready.") == 1


def test_packed_audio_preserves_original_timing(tmp_path):
    import array
    source, packed = tmp_path / "source.wav", tmp_path / "packed.wav"
    data = array.array("h", [0]*16000 + [4000]*8000 + [0]*32000 + [4000]*8000)
    with wave.open(str(source), "wb") as f:
        f.setparams((1, 2, 16000, 0, "NONE", "not compressed")); f.writeframes(data.tobytes())
    mapping = a.pack_audio(source, packed)
    assert len(mapping) == 2
    word = {"word": "ready", "start": mapping[1]["packed_start"]+.1, "end": mapping[1]["packed_start"]+.2}
    restored = a.restore_words({"words": [word]}, mapping)["words"][0]
    assert restored["start"] == pytest.approx(mapping[1]["original_start"]+.1)


def test_selection_only_is_not_complete_acceptance():
    rows = [{"profile": p, "case_id": c["id"], "repetition": c["repetition"],
        "assessment": {"selection_status": "passed", "interaction_status": "failed"}} for p in PROFILES for c in matrix()]
    result = recommendation(rows)
    assert result["preferred_for_investigation"] == PROFILES[0] and not result["acceptance_allowed"]


def test_admission_after_settle_is_a_fixture_confound_not_model_failure():
    evidence, audio, judgment = fixture("Your task has been added.", "admission")
    evidence["markers"] = [{"type": "admission.settled", "epoch_ms": 2900}]
    result = a.assess(evidence, audio, judgment)
    assert result["selection_status"] == "inconclusive"
    assert "admission_resumed_after_settle" in result["dimensions"]["evidence_validity"]["unknowns"]


def test_later_status_answer_is_not_an_admission_setup_race():
    evidence, audio, judgment = fixture("Your task was added.", "admission", "optional")
    evidence["markers"] = [{"type": "admission.settled", "epoch_ms": 2900}]
    for word in audio["words"]:
        word["start"] += 20; word["end"] += 20
    result = a.assess(evidence, audio, judgment)
    assert "admission_resumed_after_settle" not in result["dimensions"]["evidence_validity"]["unknowns"]


def test_nonlexical_spelling_normalization_does_not_invent_a_sound():
    assert a.align_words([{"word": "Hmm"}], "Mhm.") == [1]
    assert a.align_words([{"word": "Hmm"}], "Okay.") == [0]
    assert a.align_words([{"word": "Mhm"}, {"word": "Hmm"}], "Mhm.") == [1, 0]


def test_conflicting_caller_script_cannot_fail_model_selection():
    evidence, audio, judgment = fixture()
    evidence["markers"] = [{"type": "caller.start", "epoch_ms": 1200, "text": "The page can wait while we discuss this."}]
    assert a.assess(evidence, audio, judgment)["selection_status"] == "inconclusive"


def test_status_answer_during_question_is_conversation_not_unsolicited_notification():
    evidence, audio, judgment = fixture(expectation="forbidden")
    evidence.update(status_query_start_ms=2900, status_query_end_ms=3600, caller_voiced_windows=[[2900, 3600]])
    result = a.assess(evidence, audio, judgment)
    assert "requested_silence_violated" not in result["dimensions"]["notification_selection"]["failures"]
    assert result["dimensions"]["conversation_interruption"]["status"] == "failed"


def test_usage_snapshots_do_not_count_one_request_twice():
    from app.announcement_eval.focused import canonical_usage
    u = {"jarvis_voice": [{"session_id": "s", "finalized": True}], "jarvis_backend": [],
        "transcription_and_grading_requests": [{"request_id": "req_1", "purpose": "/responses", "status": 400},
            {"request_id": "req_1", "purpose": "/responses", "status": 400, "service_tier": None},
            {"request_id": "req_2", "purpose": "/responses", "status": 200}]}
    assert len(canonical_usage(u)["transcription_and_grading_requests"]) == 2
    assert len(u["transcription_and_grading_requests"]) == 3


def test_stopped_profiles_retain_all_unrun_cells():
    from app.announcement_eval.focused import annotate_report
    report = {"runs": [{"case_id": "explicit_idle", "profile": PROFILES[0], "repetition": 1}],
        "stopped_profiles": {PROFILES[0]: {}}}
    annotate_report(report)
    assert len(report["unrun_cells"]) == 23
    assert sum(c["reason"] == "profile_stopped" for c in report["unrun_cells"]) == 11
