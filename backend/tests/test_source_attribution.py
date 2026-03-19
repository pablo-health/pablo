# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for source attribution service and SOAPSentence data model."""

import os
from dataclasses import asdict
from typing import Any

os.environ["ENVIRONMENT"] = "development"

from app.models import (
    AssessmentNote,
    ObjectiveNote,
    PlanNote,
    SOAPNote,
    SOAPSentence,
    SubjectiveNote,
    Transcript,
)
from app.models.session import (
    CONFIDENCE_THRESHOLDS,
    SessionResponse,
    SOAPSentenceModel,
    TherapySession,
    _parse_transcript_segments,
    _to_sentence,
    _to_sentence_list,
)
from app.services.source_attribution_service import (
    _extract_json,
    _parse_segment_ids,
    build_attribution_prompt,
    build_claims_from_soap,
    format_transcript_with_segment_ids,
    parse_attribution_response,
)

# --- SOAPSentence serialization/deserialization ---


class TestSOAPSentenceSerialization:
    """Test SOAPSentence round-trip through to_dict/from_dict."""

    def test_soap_sentence_to_dict(self) -> None:
        s = SOAPSentence(text="Feels anxious.", source_segment_ids=[0, 3])
        d = {
            "text": "Feels anxious.",
            "source_segment_ids": [0, 3],
            "confidence_score": 0.0,
            "confidence_level": "unverified",
            "possible_match_segment_ids": [],
            "signal_used": "",
        }
        assert asdict(s) == d

    def test_soap_sentence_to_dict_with_confidence(self) -> None:
        s = SOAPSentence(
            text="Anxious.",
            source_segment_ids=[0],
            confidence_score=0.92,
            confidence_level="high",
            possible_match_segment_ids=[3, 5],
        )
        d = asdict(s)
        assert d["confidence_score"] == 0.92
        assert d["confidence_level"] == "high"
        assert d["possible_match_segment_ids"] == [3, 5]

    def test_soap_sentence_default(self) -> None:
        s = SOAPSentence()
        assert s.text == ""
        assert s.source_segment_ids == []
        assert s.confidence_score == 0.0
        assert s.confidence_level == "unverified"
        assert s.possible_match_segment_ids == []

    def test_to_sentence_from_string(self) -> None:
        result = _to_sentence("hello")
        assert result.text == "hello"
        assert result.source_segment_ids == []
        assert result.confidence_score == 0.0
        assert result.confidence_level == "unverified"
        assert result.possible_match_segment_ids == []

    def test_to_sentence_from_dict(self) -> None:
        result = _to_sentence({"text": "hello", "source_segment_ids": [1, 2]})
        assert result.text == "hello"
        assert result.source_segment_ids == [1, 2]
        assert result.confidence_score == 0.0
        assert result.confidence_level == "unverified"

    def test_to_sentence_from_dict_with_confidence(self) -> None:
        result = _to_sentence(
            {
                "text": "hello",
                "source_segment_ids": [1, 2],
                "confidence_score": 0.75,
                "confidence_level": "medium",
                "possible_match_segment_ids": [4, 6],
            }
        )
        assert result.text == "hello"
        assert result.source_segment_ids == [1, 2]
        assert result.confidence_score == 0.75
        assert result.confidence_level == "medium"
        assert result.possible_match_segment_ids == [4, 6]

    def test_to_sentence_from_none(self) -> None:
        result = _to_sentence(None)
        assert result.text == ""
        assert result.source_segment_ids == []
        assert result.confidence_score == 0.0

    def test_to_sentence_list_from_strings(self) -> None:
        result = _to_sentence_list(["a", "b"])
        assert result is not None
        assert len(result) == 2
        assert result[0].text == "a"
        assert result[1].text == "b"

    def test_to_sentence_list_from_dicts(self) -> None:
        result = _to_sentence_list(
            [
                {"text": "a", "source_segment_ids": [0]},
                {"text": "b", "source_segment_ids": [1, 2]},
            ]
        )
        assert result is not None
        assert result[0].source_segment_ids == [0]
        assert result[1].source_segment_ids == [1, 2]

    def test_to_sentence_list_none(self) -> None:
        assert _to_sentence_list(None) is None


class TestSOAPNoteRoundTrip:
    """Test SOAPNote to_dict/from_dict with SOAPSentence fields."""

    def test_round_trip_preserves_source_ids(self) -> None:
        note = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(text="Anxiety.", source_segment_ids=[0, 1]),
                mood_affect=SOAPSentence(text="Low."),
            ),
        )
        d = note.to_dict()
        restored = SOAPNote.from_dict(d)
        assert restored.subjective.chief_complaint.text == "Anxiety."
        assert restored.subjective.chief_complaint.source_segment_ids == [0, 1]
        assert restored.subjective.mood_affect.text == "Low."

    def test_round_trip_preserves_confidence_fields(self) -> None:
        note = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(
                    text="Anxiety.",
                    source_segment_ids=[0, 1],
                    confidence_score=0.92,
                    confidence_level="high",
                    possible_match_segment_ids=[3],
                ),
            ),
        )
        d = note.to_dict()
        restored = SOAPNote.from_dict(d)
        cc = restored.subjective.chief_complaint
        assert cc.confidence_score == 0.92
        assert cc.confidence_level == "high"
        assert cc.possible_match_segment_ids == [3]

    def test_from_dict_legacy_string_section(self) -> None:
        """Legacy flat string sections are wrapped into SOAPSentence."""
        data: dict[str, Any] = {
            "subjective": "Patient feels anxious.",
            "objective": "Fidgety.",
            "assessment": "GAD.",
            "plan": "Continue CBT.",
        }
        note = SOAPNote.from_dict(data)
        assert note.subjective.client_narrative.text == "Patient feels anxious."
        assert note.objective.behavior.text == "Fidgety."
        assert note.assessment.clinical_impression.text == "GAD."
        assert note.plan.next_session.text == "Continue CBT."

    def test_from_dict_plain_string_subfields(self) -> None:
        """Structured dicts with plain strings (no SOAPSentence wrapping) work."""
        data: dict[str, Any] = {
            "subjective": {
                "chief_complaint": "Anxiety.",
                "symptoms": ["Insomnia", "Racing thoughts"],
            },
            "plan": {
                "interventions_used": ["CBT"],
                "next_session": "One week.",
            },
        }
        note = SOAPNote.from_dict(data)
        assert note.subjective.chief_complaint.text == "Anxiety."
        assert note.subjective.symptoms is not None
        assert len(note.subjective.symptoms) == 2
        assert note.subjective.symptoms[0].text == "Insomnia"
        assert note.plan.interventions_used is not None
        assert note.plan.interventions_used[0].text == "CBT"

    def test_from_dict_soap_sentence_dicts(self) -> None:
        """New format with SOAPSentence dicts (text + source_segment_ids)."""
        data: dict[str, Any] = {
            "subjective": {
                "chief_complaint": {
                    "text": "Anxiety.",
                    "source_segment_ids": [0, 3],
                    "confidence_score": 0.85,
                    "confidence_level": "high",
                    "possible_match_segment_ids": [5],
                },
                "symptoms": [
                    {"text": "Insomnia", "source_segment_ids": [2]},
                ],
            },
        }
        note = SOAPNote.from_dict(data)
        cc = note.subjective.chief_complaint
        assert cc.text == "Anxiety."
        assert cc.source_segment_ids == [0, 3]
        assert cc.confidence_score == 0.85
        assert cc.confidence_level == "high"
        assert cc.possible_match_segment_ids == [5]
        assert note.subjective.symptoms is not None
        assert note.subjective.symptoms[0].source_segment_ids == [2]
        assert note.subjective.symptoms[0].confidence_score == 0.0


# --- Narrative rendering from SOAPSentence ---


class TestNarrativeFromSOAPSentence:
    """Verify to_narrative() extracts .text from SOAPSentence objects."""

    def test_narrative_extracts_text(self) -> None:
        note = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(text="Work stress.", source_segment_ids=[0]),
                mood_affect=SOAPSentence(text="Anxious.", source_segment_ids=[1]),
            ),
            objective=ObjectiveNote(
                behavior=SOAPSentence(text="Cooperative."),
            ),
            assessment=AssessmentNote(
                clinical_impression=SOAPSentence(text="GAD."),
                risk_assessment=SOAPSentence(text="Low risk."),
            ),
            plan=PlanNote(
                next_steps=[SOAPSentence(text="Follow up.")],
            ),
        )
        narrative = note.to_narrative()
        assert "**Chief Complaint:** Work stress." in narrative["subjective"]
        assert "**Mood/Affect:** Anxious." in narrative["subjective"]
        assert "**Behavior:** Cooperative." in narrative["objective"]
        assert "**Clinical Impression:** GAD." in narrative["assessment"]
        assert "- Follow up." in narrative["plan"]

    def test_empty_soap_sentence_omitted(self) -> None:
        note = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(text="Stress."),
                mood_affect=SOAPSentence(text=""),  # empty
            ),
        )
        narrative = note.to_narrative()
        assert "**Chief Complaint:** Stress." in narrative["subjective"]
        assert "**Mood/Affect:**" not in narrative["subjective"]


# --- Structured model conversion ---


class TestStructuredModel:
    """Test to_structured_model() produces correct Pydantic models."""

    def test_structured_model_includes_source_ids(self) -> None:
        note = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(
                    text="Anxiety.",
                    source_segment_ids=[0, 1],
                    confidence_score=0.9,
                    confidence_level="high",
                    possible_match_segment_ids=[4],
                ),
            ),
        )
        model = note.to_structured_model()
        cc = model.subjective.chief_complaint
        assert cc.text == "Anxiety."
        assert cc.source_segment_ids == [0, 1]
        assert cc.confidence_score == 0.9
        assert cc.confidence_level == "high"
        assert cc.possible_match_segment_ids == [4]
        assert model.narrative.subjective != ""

    def test_structured_model_list_fields(self) -> None:
        note = SOAPNote(
            plan=PlanNote(
                interventions_used=[
                    SOAPSentence(text="CBT", source_segment_ids=[5]),
                    SOAPSentence(text="DBT", source_segment_ids=[6]),
                ],
            ),
        )
        model = note.to_structured_model()
        assert model.plan.interventions_used is not None
        assert len(model.plan.interventions_used) == 2
        assert model.plan.interventions_used[0].source_segment_ids == [5]

    def test_structured_model_none_list(self) -> None:
        note = SOAPNote()
        model = note.to_structured_model()
        assert model.subjective.symptoms is None
        assert model.plan.interventions_used is None


# --- Confidence fields ---


class TestSOAPSentenceConfidenceFields:
    """Test confidence_score, confidence_level, and possible_match_segment_ids."""

    def test_confidence_fields_on_dataclass(self) -> None:
        s = SOAPSentence(
            text="Patient reports anxiety.",
            source_segment_ids=[0, 1],
            confidence_score=0.85,
            confidence_level="high",
            possible_match_segment_ids=[3, 5],
        )
        assert s.confidence_score == 0.85
        assert s.confidence_level == "high"
        assert s.possible_match_segment_ids == [3, 5]

    def test_confidence_fields_default_values(self) -> None:
        s = SOAPSentence(text="Simple claim.")
        assert s.confidence_score == 0.0
        assert s.confidence_level == "unverified"
        assert s.possible_match_segment_ids == []

    def test_pydantic_model_includes_confidence_fields(self) -> None:
        m = SOAPSentenceModel(
            text="Claim.",
            source_segment_ids=[0],
            confidence_score=0.6,
            confidence_level="medium",
            possible_match_segment_ids=[2, 4],
        )
        assert m.confidence_score == 0.6
        assert m.confidence_level == "medium"
        assert m.possible_match_segment_ids == [2, 4]

    def test_pydantic_model_defaults(self) -> None:
        m = SOAPSentenceModel()
        assert m.confidence_score == 0.0
        assert m.confidence_level == "unverified"
        assert m.possible_match_segment_ids == []

    def test_confidence_thresholds_constant(self) -> None:
        assert CONFIDENCE_THRESHOLDS["verified"] == 0.97
        assert CONFIDENCE_THRESHOLDS["high"] == 0.90
        assert CONFIDENCE_THRESHOLDS["medium"] == 0.60
        assert CONFIDENCE_THRESHOLDS["low"] == 0.30

    def test_to_sentence_partial_confidence_dict(self) -> None:
        """Dict with only some confidence fields still works."""
        result = _to_sentence(
            {
                "text": "Claim.",
                "source_segment_ids": [1],
                "confidence_score": 0.3,
            }
        )
        assert result.confidence_score == 0.3
        assert result.confidence_level == "unverified"
        assert result.possible_match_segment_ids == []


# --- Transcript segment parsing ---


class TestTranscriptSegmentParsing:
    """Test _parse_transcript_segments from raw transcript content."""

    def test_parse_basic_transcript(self) -> None:
        content = (
            "[00:01] Therapist: How have you been feeling?\n"
            "[00:06] Client: Overall better this week.\n"
            "[00:15] Therapist: Tell me more about that.\n"
        )
        segments = _parse_transcript_segments(content)
        assert len(segments) == 3
        assert segments[0].index == 0
        assert segments[0].speaker == "Therapist"
        assert segments[0].text == "How have you been feeling?"
        assert segments[0].start_time == 1.0
        assert segments[1].speaker == "Client"
        assert segments[2].index == 2

    def test_parse_hms_timestamps(self) -> None:
        content = "[01:02:03] Speaker: Some text.\n"
        segments = _parse_transcript_segments(content)
        assert len(segments) == 1
        assert segments[0].start_time == 3723.0

    def test_parse_empty_content(self) -> None:
        assert _parse_transcript_segments("") == []
        assert _parse_transcript_segments("   \n  \n ") == []

    def test_parse_non_matching_lines_skipped(self) -> None:
        content = "Not a transcript line\n[00:05] Speaker: Valid line.\n"
        segments = _parse_transcript_segments(content)
        assert len(segments) == 1
        assert segments[0].index == 0
        assert segments[0].text == "Valid line."


# --- Source attribution service ---


class TestFormatTranscriptWithSegmentIds:
    def test_basic_formatting(self) -> None:
        content = "[00:01] Therapist: Hello.\n" "[00:05] Client: Hi.\n"
        result = format_transcript_with_segment_ids(content)
        assert result == ("[S0] [00:01] Therapist: Hello.\n" "[S1] [00:05] Client: Hi.")

    def test_empty_lines_skipped(self) -> None:
        content = "[00:01] A: Hello.\n\n[00:05] B: Hi.\n"
        result = format_transcript_with_segment_ids(content)
        assert "[S0]" in result
        assert "[S1]" in result
        assert result.count("\n") == 1  # Only one newline between two lines


class TestBuildClaimsFromSoap:
    def test_extracts_all_non_empty_claims(self) -> None:
        note = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(text="Anxiety."),
                mood_affect=SOAPSentence(text=""),  # empty, should be skipped
                symptoms=[
                    SOAPSentence(text="Insomnia"),
                    SOAPSentence(text="Fatigue"),
                ],
            ),
            plan=PlanNote(
                next_session=SOAPSentence(text="One week."),
            ),
        )
        claims = build_claims_from_soap(note)
        assert "subjective.chief_complaint" in claims
        assert "subjective.mood_affect" not in claims
        assert "subjective.symptoms.0" in claims
        assert "subjective.symptoms.1" in claims
        assert "plan.next_session" in claims


class TestBuildAttributionPrompt:
    def test_prompt_includes_claims_and_transcript(self) -> None:
        claims = {
            "subjective.chief_complaint": SOAPSentence(text="Anxiety."),
            "plan.next_session": SOAPSentence(text="One week."),
        }
        transcript = "[S0] [00:01] Therapist: Hello.\n[S1] [00:05] Client: I feel anxious."
        prompt = build_attribution_prompt(claims, transcript)
        assert "Anxiety." in prompt
        assert "One week." in prompt
        assert "[S0]" in prompt
        assert "[S1]" in prompt
        assert "Return ONLY valid JSON" in prompt


class TestParseAttributionResponse:
    def test_parse_valid_response(self) -> None:
        claims = {
            "subjective.chief_complaint": SOAPSentence(text="Anxiety."),
            "plan.next_session": SOAPSentence(text="One week."),
        }
        response = '{"1": [0, 3], "2": [5]}'
        parse_attribution_response(response, claims)
        assert claims["subjective.chief_complaint"].source_segment_ids == [0, 3]
        assert claims["plan.next_session"].source_segment_ids == [5]

    def test_parse_response_with_code_block(self) -> None:
        claims = {
            "subjective.chief_complaint": SOAPSentence(text="Anxiety."),
        }
        response = '```json\n{"1": [0, 1]}\n```'
        parse_attribution_response(response, claims)
        assert claims["subjective.chief_complaint"].source_segment_ids == [0, 1]

    def test_parse_invalid_json_no_crash(self) -> None:
        claims = {
            "subjective.chief_complaint": SOAPSentence(text="Anxiety."),
        }
        parse_attribution_response("not json at all", claims)
        assert claims["subjective.chief_complaint"].source_segment_ids == []

    def test_parse_out_of_range_index_ignored(self) -> None:
        claims = {
            "subjective.chief_complaint": SOAPSentence(text="Anxiety."),
        }
        # Index "99" is out of range (only 1 claim)
        response = '{"99": [0, 1]}'
        parse_attribution_response(response, claims)
        assert claims["subjective.chief_complaint"].source_segment_ids == []

    def test_parse_non_int_segment_ids_filtered(self) -> None:
        claims = {
            "subjective.chief_complaint": SOAPSentence(text="Anxiety."),
        }
        response = '{"1": [0, "bad", 2]}'
        parse_attribution_response(response, claims)
        assert claims["subjective.chief_complaint"].source_segment_ids == [0, 2]

    def test_parse_string_segment_ids_converted(self) -> None:
        """LLM sometimes returns segment IDs as strings like ["1", "3"]."""
        claims = {
            "subjective.chief_complaint": SOAPSentence(text="Anxiety."),
        }
        response = '{"1": ["0", "3", "5"]}'
        parse_attribution_response(response, claims)
        assert claims["subjective.chief_complaint"].source_segment_ids == [0, 3, 5]

    def test_parse_mixed_int_and_string_segment_ids(self) -> None:
        claims = {
            "subjective.chief_complaint": SOAPSentence(text="Anxiety."),
        }
        response = '{"1": [0, "3", 5, "7"]}'
        parse_attribution_response(response, claims)
        assert claims["subjective.chief_complaint"].source_segment_ids == [0, 3, 5, 7]

    def test_max_segment_id_filters_out_of_bounds(self) -> None:
        claims = {
            "subjective.chief_complaint": SOAPSentence(text="Anxiety."),
        }
        response = '{"1": [0, 3, 18, 25]}'
        parse_attribution_response(response, claims, max_segment_id=14)
        assert claims["subjective.chief_complaint"].source_segment_ids == [0, 3]

    def test_max_segment_id_with_string_ids(self) -> None:
        claims = {
            "subjective.chief_complaint": SOAPSentence(text="Anxiety."),
        }
        response = '{"1": ["0", "14", "15"]}'
        parse_attribution_response(response, claims, max_segment_id=14)
        assert claims["subjective.chief_complaint"].source_segment_ids == [0, 14]

    def test_max_segment_id_none_allows_all(self) -> None:
        """When max_segment_id is not provided, all non-negative IDs pass through."""
        claims = {
            "subjective.chief_complaint": SOAPSentence(text="Anxiety."),
        }
        response = '{"1": [0, 100, 999]}'
        parse_attribution_response(response, claims)
        assert claims["subjective.chief_complaint"].source_segment_ids == [0, 100, 999]


class TestExtractJson:
    def test_plain_json(self) -> None:
        assert _extract_json('{"1": [0, 3]}') == '{"1": [0, 3]}'

    def test_json_with_surrounding_text(self) -> None:
        text = 'Here is the result: {"1": [0]} and some trailing text'
        assert _extract_json(text) == '{"1": [0]}'

    def test_markdown_json_code_block(self) -> None:
        text = '```json\n{"1": [0, 1]}\n```'
        assert _extract_json(text) == '{"1": [0, 1]}'

    def test_markdown_plain_code_block(self) -> None:
        text = '```\n{"1": [2, 3]}\n```'
        assert _extract_json(text) == '{"1": [2, 3]}'

    def test_nested_json(self) -> None:
        text = '{"outer": {"inner": [1, 2]}}'
        result = _extract_json(text)
        assert result == '{"outer": {"inner": [1, 2]}}'

    def test_no_json_returns_none(self) -> None:
        assert _extract_json("no json here") is None

    def test_empty_string(self) -> None:
        assert _extract_json("") is None


class TestParseSegmentIds:
    def test_all_ints(self) -> None:
        assert _parse_segment_ids([0, 3, 5]) == [0, 3, 5]

    def test_all_strings(self) -> None:
        assert _parse_segment_ids(["0", "3", "5"]) == [0, 3, 5]

    def test_mixed_types(self) -> None:
        assert _parse_segment_ids([0, "3", 5, "7"]) == [0, 3, 5, 7]

    def test_invalid_strings_filtered(self) -> None:
        assert _parse_segment_ids(["abc", "1", "xyz"]) == [1]

    def test_negative_ids_filtered(self) -> None:
        assert _parse_segment_ids([-1, 0, 3, -5]) == [0, 3]

    def test_max_segment_id_filters_high_values(self) -> None:
        assert _parse_segment_ids([0, 14, 15, 100], max_segment_id=14) == [0, 14]

    def test_max_segment_id_none_allows_all(self) -> None:
        assert _parse_segment_ids([0, 100, 999]) == [0, 100, 999]

    def test_empty_list(self) -> None:
        assert _parse_segment_ids([]) == []


# --- SessionResponse integration ---


class TestSessionResponseWithSources:
    """Test that SessionResponse.from_session includes structured SOAP + segments."""

    def _make_session(self) -> Any:
        return TherapySession(
            id="sess-1",
            user_id="u1",
            patient_id="p1",
            session_date="2024-06-01",
            session_number=1,
            status="pending_review",
            transcript=Transcript(
                format="txt",
                content=("[00:01] Therapist: How are you?\n" "[00:06] Client: Better this week.\n"),
            ),
            created_at="2024-06-01T00:00:00Z",
            soap_note=SOAPNote(
                subjective=SubjectiveNote(
                    chief_complaint=SOAPSentence(text="Feeling better.", source_segment_ids=[1]),
                ),
            ),
        )

    def test_response_includes_structured_soap(self) -> None:
        session = self._make_session()
        resp = SessionResponse.from_session(session, "Jane Doe")
        assert resp.soap_note_structured is not None
        assert resp.soap_note_structured.subjective.chief_complaint.text == "Feeling better."
        assert resp.soap_note_structured.subjective.chief_complaint.source_segment_ids == [1]

    def test_response_includes_narrative(self) -> None:
        session = self._make_session()
        resp = SessionResponse.from_session(session, "Jane Doe")
        assert resp.soap_note is not None
        assert "Feeling better." in resp.soap_note.subjective

    def test_response_includes_transcript_segments(self) -> None:
        session = self._make_session()
        resp = SessionResponse.from_session(session, "Jane Doe")
        assert resp.transcript_segments is not None
        assert len(resp.transcript_segments) == 2
        assert resp.transcript_segments[0].speaker == "Therapist"
        assert resp.transcript_segments[1].speaker == "Client"

    def test_response_no_soap_no_structured(self) -> None:
        session = TherapySession(
            id="sess-2",
            user_id="u1",
            patient_id="p1",
            session_date="2024-06-01",
            session_number=1,
            status="queued",
            transcript=Transcript(format="txt", content=""),
            created_at="2024-06-01T00:00:00Z",
        )
        resp = SessionResponse.from_session(session, "Jane Doe")
        assert resp.soap_note_structured is None
        assert resp.transcript_segments is None
