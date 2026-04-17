# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Cross-service integration tests for the full verification pipeline.

Tests the complete data flow:
  SOAPNote -> claims extraction -> verification (BM25 + embedding + NLI)
  -> confidence fields -> serialization -> API response (SOAPSentenceModel)
"""

import os

os.environ["ENVIRONMENT"] = "development"

import pytest
from app.models import SOAPNote, SOAPSentence
from app.models.session import (
    AssessmentNote,
    ObjectiveNote,
    PlanNote,
    SubjectiveNote,
)
from app.services.embedding_service import MockEmbeddingService
from app.services.nli_service import MockNLIService, NLIResult
from app.services.source_attribution_service import build_claims_from_soap
from app.services.source_verification_service import (
    SourceVerificationService,
    VerificationResult,
)

# Shared fixtures

TRANSCRIPT_SEGMENTS = [
    "How have you been feeling since our last session?",
    "Work has been really stressful this week.",
    "I feel anxious most days, especially in the morning.",
    "That sounds difficult. Can you tell me more?",
    "I have trouble sleeping. I wake up around 3 AM.",
    "We practiced the 4-7-8 breathing technique.",
    "My mood has improved slightly since starting medication.",
    "Let's schedule a follow-up in one week.",
]


def _make_soap_note() -> SOAPNote:
    """Create a realistic SOAPNote with varied source attributions."""
    return SOAPNote(
        subjective=SubjectiveNote(
            chief_complaint=SOAPSentence(
                text="Patient reports work-related stress and anxiety.",
                source_segment_ids=[1, 2],
            ),
            mood_affect=SOAPSentence(
                text="Anxious but shows slight improvement on medication.",
                source_segment_ids=[2, 6],
            ),
            symptoms=[
                SOAPSentence(text="Difficulty sleeping", source_segment_ids=[4]),
                SOAPSentence(text="Morning anxiety", source_segment_ids=[2]),
            ],
            client_narrative=SOAPSentence(
                text="Describes ongoing stress at work.",
                source_segment_ids=[1],
            ),
        ),
        objective=ObjectiveNote(
            appearance=SOAPSentence(text="Well-groomed, appropriately dressed."),
            behavior=SOAPSentence(text="Cooperative and engaged.", source_segment_ids=[0]),
            speech=SOAPSentence(text="Normal rate and volume."),
            thought_process=SOAPSentence(text="Linear and goal-directed."),
            affect_observed=SOAPSentence(text="Congruent with reported mood."),
        ),
        assessment=AssessmentNote(
            clinical_impression=SOAPSentence(
                text="Generalized anxiety disorder with work stressors.",
                source_segment_ids=[1, 2, 4],
            ),
            progress=SOAPSentence(
                text="Slight improvement with medication.",
                source_segment_ids=[6],
            ),
            risk_assessment=SOAPSentence(text="No acute safety concerns."),
            functioning_level=SOAPSentence(text="Moderate functional impairment."),
        ),
        plan=PlanNote(
            interventions_used=[
                SOAPSentence(text="4-7-8 breathing technique", source_segment_ids=[5]),
            ],
            homework_assignments=[
                SOAPSentence(text="Daily mindfulness practice"),
            ],
            next_steps=[
                SOAPSentence(text="Review medication efficacy"),
            ],
            next_session=SOAPSentence(
                text="Follow-up in one week.",
                source_segment_ids=[7],
            ),
        ),
    )


def _run_verification(
    soap_note: SOAPNote,
    segments: list[str],
    nli_label: str = "entailment",
    nli_score: float = 0.9,
    nli_service: MockNLIService | None = None,
) -> list[VerificationResult]:
    """Helper to run verification and apply results to a SOAPNote."""
    embedding = MockEmbeddingService()
    nli = nli_service or MockNLIService(default_label=nli_label, default_score=nli_score)
    service = SourceVerificationService(embedding, nli)

    claims = build_claims_from_soap(soap_note)
    claim_texts = {key: s.text for key, s in claims.items()}
    attributions = {key: s.source_segment_ids for key, s in claims.items()}

    results = service.verify_attributions(claim_texts, segments, attributions)

    # Apply results back to SOAPSentence objects (mirrors pipeline wiring)
    for result in results:
        if result.claim_key in claims:
            sentence = claims[result.claim_key]
            sentence.confidence_score = result.confidence_score
            sentence.confidence_level = result.confidence_level
            sentence.possible_match_segment_ids = result.possible_match_segment_ids

    return results


# Full pipeline integration tests


class TestFullPipelineIntegration:
    """End-to-end: SOAPNote -> verification -> confidence fields populated."""

    def test_all_confidence_fields_populated_on_every_claim(self) -> None:
        """Every SOAPSentence with text gets confidence_score and confidence_level set."""
        soap = _make_soap_note()
        _run_verification(soap, TRANSCRIPT_SEGMENTS, nli_score=0.85)

        claims = build_claims_from_soap(soap)
        for key, sentence in claims.items():
            if sentence.source_segment_ids:
                assert sentence.confidence_score > 0, f"{key} should have a score > 0"
                assert sentence.confidence_level != "", f"{key} should have a level"
            else:
                assert sentence.confidence_level == "unverified", (
                    f"{key} with no sources should be unverified"
                )

    def test_mixed_confidence_levels_in_same_note(self) -> None:
        """A single SOAP note can have high, medium, low, and unverified claims."""
        soap = _make_soap_note()
        nli = MockNLIService(default_label="entailment", default_score=0.92)

        # Configure specific pairs to get different confidence levels
        # Low confidence: appearance has no sources, but NLI returns low for candidates
        nli.set_response(
            TRANSCRIPT_SEGMENTS[0],
            "Well-groomed, appropriately dressed.",
            NLIResult(
                label="neutral",
                entailment_score=0.1,
                contradiction_score=0.1,
                neutral_score=0.8,
            ),
        )

        _run_verification(soap, TRANSCRIPT_SEGMENTS, nli_service=nli)

        claims = build_claims_from_soap(soap)
        levels = {s.confidence_level for s in claims.values()}
        # Should have at least high (from attributed+entailment) and unverified (no sources)
        assert "high" in levels, "Should have high-confidence claims"
        assert "unverified" in levels, "Should have unverified claims (no sources)"

    def test_possible_match_segment_ids_populated(self) -> None:
        """When NLI finds good candidates not in original attribution, they appear
        in possible_match_segment_ids."""
        soap = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(
                    text="Patient reports work stress.",
                    source_segment_ids=[0],  # Attributed to segment 0 (wrong)
                ),
            ),
        )

        nli = MockNLIService(default_label="entailment", default_score=0.7)
        # The attributed segment gets low entailment
        nli.set_response(
            TRANSCRIPT_SEGMENTS[0],
            "Patient reports work stress.",
            NLIResult(
                label="neutral",
                entailment_score=0.15,
                contradiction_score=0.05,
                neutral_score=0.8,
            ),
        )
        # Default 0.7 entailment means candidates > 0.5 threshold become possible matches

        _run_verification(soap, TRANSCRIPT_SEGMENTS, nli_service=nli)

        claim = soap.subjective.chief_complaint
        # Candidates with entailment > 0.5 (not in original [0]) should be possible matches
        assert len(claim.possible_match_segment_ids) > 0
        assert 0 not in claim.possible_match_segment_ids

    def test_verification_result_count_matches_claim_count(self) -> None:
        """Number of VerificationResults equals number of non-empty claims."""
        soap = _make_soap_note()
        results = _run_verification(soap, TRANSCRIPT_SEGMENTS)
        claims = build_claims_from_soap(soap)
        assert len(results) == len(claims)


# Edge cases


class TestEdgeCases:
    """Edge cases for the verification pipeline."""

    def test_empty_soap_note(self) -> None:
        """Empty SOAPNote produces no claims and no verification results."""
        soap = SOAPNote()
        results = _run_verification(soap, TRANSCRIPT_SEGMENTS)
        assert results == []

    def test_soap_note_with_empty_sections(self) -> None:
        """SOAPNote with only empty-text sentences produces no claims."""
        soap = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(text=""),
                mood_affect=SOAPSentence(text="  "),
            ),
            objective=ObjectiveNote(),
            assessment=AssessmentNote(),
            plan=PlanNote(),
        )
        claims = build_claims_from_soap(soap)
        assert len(claims) == 0
        results = _run_verification(soap, TRANSCRIPT_SEGMENTS)
        assert results == []

    def test_single_segment_transcript(self) -> None:
        """Verification works with just one transcript segment."""
        soap = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(
                    text="Patient feels anxious.",
                    source_segment_ids=[0],
                ),
            ),
        )
        single_segment = ["I feel anxious about everything."]
        results = _run_verification(soap, single_segment, nli_score=0.9)

        assert len(results) == 1
        assert results[0].confidence_level == "high"
        assert results[0].confidence_score == 0.9

    def test_claim_with_many_source_segment_ids(self) -> None:
        """Claims with 5+ source segments are verified correctly."""
        soap = SOAPNote(
            assessment=AssessmentNote(
                clinical_impression=SOAPSentence(
                    text="Complex presentation with multiple stressors.",
                    source_segment_ids=[0, 1, 2, 3, 4, 5, 6],
                ),
            ),
        )
        results = _run_verification(soap, TRANSCRIPT_SEGMENTS, nli_score=0.92)

        assert len(results) == 1
        assert results[0].confidence_score == 0.92
        assert results[0].confidence_level == "high"

    def test_all_claims_unverified(self) -> None:
        """When NLI returns neutral for everything, all attributed claims get low scores."""
        soap = _make_soap_note()
        nli = MockNLIService(default_label="neutral", default_score=0.6)

        _run_verification(soap, TRANSCRIPT_SEGMENTS, nli_service=nli)

        claims = build_claims_from_soap(soap)
        for key, sentence in claims.items():
            if sentence.source_segment_ids:
                # Neutral NLI has entailment_score = remainder = (1 - 0.6) / 2 = 0.2
                assert sentence.confidence_level in (
                    "low",
                    "unverified",
                ), f"{key} should be low/unverified with neutral NLI"
            else:
                assert sentence.confidence_level == "unverified"

    def test_all_claims_high_confidence(self) -> None:
        """Happy path: all claims get high confidence with strong entailment."""
        soap = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(text="Work stress.", source_segment_ids=[1]),
                mood_affect=SOAPSentence(text="Anxious.", source_segment_ids=[2]),
            ),
            assessment=AssessmentNote(
                clinical_impression=SOAPSentence(
                    text="Anxiety disorder.", source_segment_ids=[1, 2]
                ),
            ),
        )
        _run_verification(soap, TRANSCRIPT_SEGMENTS, nli_score=0.93)

        claims = build_claims_from_soap(soap)
        assert all(s.confidence_level == "high" for s in claims.values())
        assert all(s.confidence_score == 0.93 for s in claims.values())


# Serialization round-trip with confidence


class TestSerializationRoundTrip:
    """Verify confidence fields survive serialization/deserialization."""

    def test_to_dict_from_dict_preserves_confidence(self) -> None:
        """SOAPNote.to_dict() -> SOAPNote.from_dict() preserves all confidence fields."""
        soap = _make_soap_note()
        _run_verification(soap, TRANSCRIPT_SEGMENTS, nli_score=0.85)

        # Serialize and deserialize
        data = soap.to_dict()
        restored = SOAPNote.from_dict(data)

        # Verify chief_complaint confidence survives
        original = soap.subjective.chief_complaint
        roundtripped = restored.subjective.chief_complaint
        assert roundtripped.confidence_score == original.confidence_score
        assert roundtripped.confidence_level == original.confidence_level
        assert roundtripped.possible_match_segment_ids == original.possible_match_segment_ids
        assert roundtripped.source_segment_ids == original.source_segment_ids

    def test_to_dict_from_dict_preserves_all_claims(self) -> None:
        """Every claim's confidence fields survive round-trip."""
        soap = _make_soap_note()
        _run_verification(soap, TRANSCRIPT_SEGMENTS, nli_score=0.85)

        data = soap.to_dict()
        restored = SOAPNote.from_dict(data)

        original_claims = build_claims_from_soap(soap)
        restored_claims = build_claims_from_soap(restored)

        assert set(original_claims.keys()) == set(restored_claims.keys())
        for key in original_claims:
            orig = original_claims[key]
            rest = restored_claims[key]
            assert rest.text == orig.text, f"{key}: text mismatch"
            assert rest.source_segment_ids == orig.source_segment_ids, f"{key}: ids mismatch"
            assert rest.confidence_score == pytest.approx(orig.confidence_score), (
                f"{key}: score mismatch"
            )
            assert rest.confidence_level == orig.confidence_level, f"{key}: level mismatch"
            assert rest.possible_match_segment_ids == orig.possible_match_segment_ids, (
                f"{key}: possible_matches mismatch"
            )

    def test_to_dict_from_dict_preserves_list_items(self) -> None:
        """List fields (symptoms, interventions) preserve confidence on round-trip."""
        soap = _make_soap_note()
        _run_verification(soap, TRANSCRIPT_SEGMENTS, nli_score=0.92)

        data = soap.to_dict()
        restored = SOAPNote.from_dict(data)

        assert restored.subjective.symptoms is not None
        assert len(restored.subjective.symptoms) == 2
        for i, symptom in enumerate(restored.subjective.symptoms):
            orig = soap.subjective.symptoms[i]  # type: ignore[index]
            assert symptom.confidence_score == pytest.approx(orig.confidence_score)
            assert symptom.confidence_level == orig.confidence_level

    def test_to_structured_model_includes_confidence(self) -> None:
        """to_structured_model() includes confidence in SOAPSentenceModel."""
        soap = _make_soap_note()
        _run_verification(soap, TRANSCRIPT_SEGMENTS, nli_score=0.85)

        structured = soap.to_structured_model()

        # Check chief_complaint
        cc = structured.subjective.chief_complaint
        assert cc.confidence_score == soap.subjective.chief_complaint.confidence_score
        assert cc.confidence_level == soap.subjective.chief_complaint.confidence_level
        assert (
            cc.possible_match_segment_ids
            == soap.subjective.chief_complaint.possible_match_segment_ids
        )

    def test_structured_model_api_response_shape(self) -> None:
        """Structured model serializes to JSON matching frontend SOAPSentence type."""
        soap = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(
                    text="Anxiety about work.",
                    source_segment_ids=[1, 2],
                    confidence_score=0.92,
                    confidence_level="high",
                    possible_match_segment_ids=[3],
                    signal_used="token_overlap",
                ),
            ),
        )

        structured = soap.to_structured_model()
        json_data = structured.model_dump()

        cc = json_data["subjective"]["chief_complaint"]
        assert cc["text"] == "Anxiety about work."
        assert cc["source_segment_ids"] == [1, 2]
        assert cc["confidence_score"] == 0.92
        assert cc["confidence_level"] == "high"
        assert cc["possible_match_segment_ids"] == [3]
        assert cc["signal_used"] == "token_overlap"

    def test_structured_model_unverified_defaults(self) -> None:
        """Unverified claims serialize with default confidence values."""
        soap = SOAPNote(
            objective=ObjectiveNote(
                appearance=SOAPSentence(text="Well-groomed."),
            ),
        )

        structured = soap.to_structured_model()
        json_data = structured.model_dump()

        appearance = json_data["objective"]["appearance"]
        assert appearance["confidence_score"] == 0.0
        assert appearance["confidence_level"] == "unverified"
        assert appearance["possible_match_segment_ids"] == []
        assert appearance["source_segment_ids"] == []
        assert appearance["signal_used"] == ""

    def test_signal_used_round_trip(self) -> None:
        """signal_used survives to_dict/from_dict round-trip."""
        soap = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(
                    text="Anxiety.",
                    source_segment_ids=[1],
                    confidence_score=0.85,
                    confidence_level="medium",
                    signal_used="token_overlap",
                ),
            ),
        )

        data = soap.to_dict()
        restored = SOAPNote.from_dict(data)
        assert restored.subjective.chief_complaint.signal_used == "token_overlap"

    def test_from_dict_handles_legacy_without_confidence(self) -> None:
        """Legacy dicts without confidence fields get safe defaults."""
        legacy_data = {
            "subjective": {
                "chief_complaint": {
                    "text": "Anxiety.",
                    "source_segment_ids": [0, 1],
                },
            },
        }
        soap = SOAPNote.from_dict(legacy_data)
        cc = soap.subjective.chief_complaint
        assert cc.text == "Anxiety."
        assert cc.source_segment_ids == [0, 1]
        assert cc.confidence_score == 0.0
        assert cc.confidence_level == "unverified"
        assert cc.possible_match_segment_ids == []
        assert cc.signal_used == ""

    def test_from_dict_handles_plain_string_sections(self) -> None:
        """Legacy flat string sections get safe defaults."""
        legacy_data = {
            "subjective": "Patient feels anxious.",
            "objective": "Calm during session.",
        }
        soap = SOAPNote.from_dict(legacy_data)
        assert soap.subjective.client_narrative.text == "Patient feels anxious."
        assert soap.subjective.client_narrative.confidence_score == 0.0
        assert soap.subjective.client_narrative.confidence_level == "unverified"
