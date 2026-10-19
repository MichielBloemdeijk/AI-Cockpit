"""Actual signal overlap and independent clocks must corroborate transcript spans."""
from app.announcement_eval.verification import intersect, native_span, matrix, manifest
from app.announcement_eval import assessment
from test_announcement_assessment import fixture


def test_silent_span_is_not_simultaneous_speech():
    assert intersect([[0,3]], [[0,.1],[2.9,3]], 1,2) == 0


def test_raw_intersection_does_not_join_pauses():
    assert abs(intersect([[0,.1],[.2,.3]],[[0,.3]],0,.3)-.2) < .0001


def test_native_provider_clock_is_independent_of_receipt_wall_clock():
    events = [{"epoch_ms": 999999,"event":{"type":"session.output_transcript.delta", "delta":"The page", "start_ms":100,"end_ms":200}},
        {"epoch_ms":1000000,"event":{"type":"session.output_transcript.delta", "delta":" is ready.","start_ms":200,"end_ms":300}}]
    assert native_span(events,"The page is ready") == (100,300)
    assert native_span(events,"The page is finished") is None


def test_phase_freezes_eight_conditions_and_matching_controls():
    cases = matrix()
    assert len(cases) == manifest()["session_limit"] == 8
    for rep in (1,2):
        control = next(c for c in cases if c["id"]=="occupied_control" and c["repetition"]==rep)
        treatment = next(c for c in cases if c["id"]=="occupied" and c["repetition"]==rep)
        assert control["steps"][:5] == treatment["steps"][:5]
        speech = next(c for c in cases if c["id"]=="explicit_speaking" and c["repetition"]==rep)
        assert speech["steps"][4] == {"type":"wait_input", "pattern":"finished speaking"}
        assert not any("page can wait" in s.get("text","").lower() for s in speech["steps"])


def test_absent_required_outcome_cannot_pass_deadline_dimension():
    e, _, _ = fixture()
    e["state"]["provider_events"][0]["event"]["delta"] = ""
    result = assessment.assess(e,{"words":[]},{"utterances":[]})
    assert result["dimensions"]["deadline"]["status"] == "failed"


def test_split_listening_sound_and_empty_asr_entries_are_not_phantom_words():
    words = [{"word":w} for w in ("Mm","hmm","", "you", "get", "st")]
    assert assessment.align_words(words,"Mhm you get steady") == [1,1,1,1,1,0]
    assert assessment.align_words(words,"you get steady")[:2] == [0,0]


def test_homophonic_punctuation_and_backchannel_do_not_license_different_results():
    assert assessment.tokens("The notebook's") == assessment.tokens("The notebooks")
    assert assessment.tokens("M-kay") == assessment.tokens("Okay")
    assert assessment.tokens("page is ready") != assessment.tokens("page isn't ready")
    assert assessment.align_words([{"word":"st"}],"steady") == [0]
    assert assessment.tokens("web page") == assessment.tokens("webpage")
    assert assessment.tokens("queued") == assessment.tokens("cued")
    assert assessment.tokens("queued") != assessment.tokens("cute")


def test_asr_omitted_negation_cannot_qualify_a_terminal_claim():
    evidence,audio,judgment=fixture()
    event=evidence["state"]["provider_events"][0]["event"]
    event["delta"]=event["delta"].replace("is ready","is not ready")
    result=assessment.assess(evidence,audio,judgment)
    assert result["selection_status"]=="inconclusive"
    assert any(x.startswith("provider_words_missing_from_audio_transcription:") for x in result["dimensions"]["evidence_validity"]["unknowns"])
    assert not any(x.startswith("unmatched_audio_words:") for x in result["dimensions"]["evidence_validity"]["unknowns"])


def test_omitted_listening_acknowledgment_remains_uncertain_for_conversation():
    evidence,audio,judgment=fixture()
    event=evidence["state"]["provider_events"][0]["event"]
    event["delta"]="Understood. "+event["delta"]
    result=assessment.assess(evidence,audio,judgment)
    assert result["selection_status"]=="passed"
    assert result["dimensions"]["conversation_interruption"]["status"]=="inconclusive"


def test_packed_silence_cannot_turn_backchannel_into_substantive_interruption():
    audio = assessment.restore_words({"words":[{"word":"it","start":.8,"end":1.1}]},[
        {"packed_start":0,"packed_end":1,"original_start":2,"original_end":3},
        {"packed_start":1,"packed_end":2,"original_start":14,"original_end":15}])
    assert audio["words"][0]["packed_gap_crossing"] is True
    e, _, j = fixture("it", "backchannel", "optional")
    e["state"]["provider_events"][0]["event"]["delta"] = "it"
    e["caller_voiced_windows"] = [[4000,7000]]
    result = assessment.assess(e,audio,j)
    assert result["interaction_status"] == "inconclusive"
    assert not result["dimensions"]["conversation_interruption"]["failures"]


def test_internal_word_gap_does_not_move_verified_outcome_deadline():
    e, audio, j = fixture()
    audio["words"][2]["packed_gap_crossing"] = True
    result = assessment.assess(e,audio,j)
    assert result["selection_status"] == "passed"
    assert result["utterances"][0]["word_timing_gap_indices"] == [2]
