# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for source attribution wiring into the SOAP generation pipeline."""

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ["ENVIRONMENT"] = "development"

from app.models import SOAPNote, SOAPSentence
from app.models.session import (
    AssessmentNote,
    ObjectiveNote,
    PlanNote,
    SubjectiveNote,
)
from app.services.embedding_service import GoogleEmbeddingService, MockEmbeddingService
from app.services.nli_service import DeBERTaNLIService, MockNLIService
from app.services.soap_generation_service import MeetingTranscriptionSOAPService
from app.services.source_verification_service import (
    SourceVerificationService,
    VerificationResult,
)

from backend.plugins.mental_health.mental_health_plugin import (  # type: ignore[import-untyped]
    MentalHealthPlugin,
)


def _make_soap_note() -> SOAPNote:
    """Create a minimal SOAPNote for testing attribution."""
    return SOAPNote(
        subjective=SubjectiveNote(
            chief_complaint=SOAPSentence(text="Anxiety about work."),
            mood_affect=SOAPSentence(text="Anxious."),
        ),
        objective=ObjectiveNote(
            behavior=SOAPSentence(text="Cooperative."),
        ),
        assessment=AssessmentNote(
            clinical_impression=SOAPSentence(text="GAD."),
            risk_assessment=SOAPSentence(text="Low risk."),
        ),
        plan=PlanNote(
            next_steps=[SOAPSentence(text="Follow up in one week.")],
        ),
    )


SAMPLE_TRANSCRIPT = (
    "[00:01] Therapist: How have you been feeling?\n"
    "[00:06] Client: Work has been really stressful.\n"
    "[00:15] Client: I feel anxious most days.\n"
    "[00:25] Therapist: That sounds difficult.\n"
)


class TestRunSourceAttribution:
    """Test _run_source_attribution method."""

    @patch("meeting_transcription.utils.llm_client.LLMClient")
    def test_attribution_populates_source_ids(self, mock_llm_cls: MagicMock) -> None:
        """Successful Call 2 populates source_segment_ids on SOAPSentence objects."""
        mock_client = MagicMock()
        mock_llm_cls.return_value = mock_client
        # Return a valid JSON mapping claim numbers to segment indices
        mock_client.call.return_value = (
            '{"1": [1, 2], "2": [2], "3": [0], "4": [1], "5": [2], "6": [3]}'
        )

        soap_note = _make_soap_note()
        MeetingTranscriptionSOAPService._run_source_attribution(soap_note, SAMPLE_TRANSCRIPT)

        # Call was made with temperature=0.0
        mock_client.call.assert_called_once()
        call_kwargs = mock_client.call.call_args
        assert call_kwargs.kwargs["temperature"] == 0.0

        # Source IDs were populated
        assert soap_note.subjective.chief_complaint.source_segment_ids == [1, 2]
        assert soap_note.subjective.mood_affect.source_segment_ids == [2]

    @patch("meeting_transcription.utils.llm_client.LLMClient")
    def test_attribution_prompt_contains_segment_ids(self, mock_llm_cls: MagicMock) -> None:
        """The prompt sent to LLM includes [Sn] segment markers."""
        mock_client = MagicMock()
        mock_llm_cls.return_value = mock_client
        mock_client.call.return_value = "{}"

        soap_note = _make_soap_note()
        MeetingTranscriptionSOAPService._run_source_attribution(soap_note, SAMPLE_TRANSCRIPT)

        prompt = mock_client.call.call_args.kwargs["prompt"]
        assert "[S0]" in prompt
        assert "[S1]" in prompt
        assert "[S2]" in prompt
        assert "[S3]" in prompt

    @patch("meeting_transcription.utils.llm_client.LLMClient")
    def test_attribution_prompt_contains_claims(self, mock_llm_cls: MagicMock) -> None:
        """The prompt sent to LLM includes SOAP claims text."""
        mock_client = MagicMock()
        mock_llm_cls.return_value = mock_client
        mock_client.call.return_value = "{}"

        soap_note = _make_soap_note()
        MeetingTranscriptionSOAPService._run_source_attribution(soap_note, SAMPLE_TRANSCRIPT)

        prompt = mock_client.call.call_args.kwargs["prompt"]
        assert "Anxiety about work." in prompt
        assert "GAD." in prompt
        assert "Follow up in one week." in prompt

    @patch("meeting_transcription.utils.llm_client.LLMClient")
    def test_attribution_failure_does_not_crash(self, mock_llm_cls: MagicMock) -> None:
        """If Call 2 fails, SOAP note is still valid with empty source_segment_ids."""
        mock_llm_cls.side_effect = RuntimeError("LLM unavailable")

        soap_note = _make_soap_note()
        # Should not raise
        MeetingTranscriptionSOAPService._run_source_attribution(soap_note, SAMPLE_TRANSCRIPT)

        # Source IDs remain empty (default)
        assert soap_note.subjective.chief_complaint.source_segment_ids == []
        assert soap_note.assessment.clinical_impression.source_segment_ids == []

    @patch("meeting_transcription.utils.llm_client.LLMClient")
    def test_attribution_invalid_json_does_not_crash(self, mock_llm_cls: MagicMock) -> None:
        """If LLM returns invalid JSON, SOAP note keeps empty source_segment_ids."""
        mock_client = MagicMock()
        mock_llm_cls.return_value = mock_client
        mock_client.call.return_value = "This is not JSON at all"

        soap_note = _make_soap_note()
        MeetingTranscriptionSOAPService._run_source_attribution(soap_note, SAMPLE_TRANSCRIPT)

        assert soap_note.subjective.chief_complaint.source_segment_ids == []

    @patch("meeting_transcription.utils.llm_client.LLMClient")
    def test_attribution_empty_soap_note_skips_call(self, mock_llm_cls: MagicMock) -> None:
        """If SOAP note has no non-empty claims, Call 2 is skipped entirely."""
        mock_client = MagicMock()
        mock_llm_cls.return_value = mock_client

        empty_note = SOAPNote()
        MeetingTranscriptionSOAPService._run_source_attribution(empty_note, SAMPLE_TRANSCRIPT)

        # LLM was never called
        mock_client.call.assert_not_called()

    @patch("meeting_transcription.utils.llm_client.LLMClient")
    def test_attribution_filters_out_of_bounds_segment_ids(self, mock_llm_cls: MagicMock) -> None:
        """LLM-hallucinated segment IDs beyond transcript length are filtered out."""
        mock_client = MagicMock()
        mock_llm_cls.return_value = mock_client
        # SAMPLE_TRANSCRIPT has 4 lines -> segments S0-S3, max_segment_id=3
        # Return IDs that include out-of-bounds values
        mock_client.call.return_value = '{"1": [0, 1, 18], "2": [2, 99]}'

        soap_note = _make_soap_note()
        MeetingTranscriptionSOAPService._run_source_attribution(soap_note, SAMPLE_TRANSCRIPT)

        assert soap_note.subjective.chief_complaint.source_segment_ids == [0, 1]
        assert soap_note.subjective.mood_affect.source_segment_ids == [2]

    @patch("meeting_transcription.utils.llm_client.LLMClient")
    def test_attribution_handles_string_segment_ids_from_llm(self, mock_llm_cls: MagicMock) -> None:
        """LLM sometimes returns segment IDs as strings instead of ints."""
        mock_client = MagicMock()
        mock_llm_cls.return_value = mock_client
        mock_client.call.return_value = '{"1": ["0", "1"], "2": ["2"]}'

        soap_note = _make_soap_note()
        MeetingTranscriptionSOAPService._run_source_attribution(soap_note, SAMPLE_TRANSCRIPT)

        assert soap_note.subjective.chief_complaint.source_segment_ids == [0, 1]
        assert soap_note.subjective.mood_affect.source_segment_ids == [2]


class TestMentalHealthPluginSegmentIds:
    """Test that the mental health plugin formats transcript with [Sn] markers."""

    def test_plugin_format_includes_segment_ids(self) -> None:
        """_format_transcript_for_prompt adds [Sn] segment indices."""
        plugin = MentalHealthPlugin()
        chunked_data = {
            "chunks": [
                {
                    "segments": [
                        {
                            "participant": {"name": "Therapist"},
                            "text": "How are you?",
                            "start_timestamp": {"relative": 5.0},
                        },
                        {
                            "participant": {"name": "Client"},
                            "text": "Better this week.",
                            "start_timestamp": {"relative": 65.0},
                        },
                    ],
                }
            ],
        }

        result = plugin._format_transcript_for_prompt(chunked_data)
        assert result == (
            "[S0] [00:05] Therapist: How are you?\n" "[S1] [01:05] Client: Better this week."
        )

    def test_plugin_format_multiple_chunks(self) -> None:
        """Segment indices are contiguous across chunks."""
        plugin = MentalHealthPlugin()
        chunked_data = {
            "chunks": [
                {
                    "segments": [
                        {
                            "participant": {"name": "A"},
                            "text": "First.",
                            "start_timestamp": {"relative": 0.0},
                        },
                    ],
                },
                {
                    "segments": [
                        {
                            "participant": {"name": "B"},
                            "text": "Second.",
                            "start_timestamp": {"relative": 120.0},
                        },
                    ],
                },
            ],
        }

        result = plugin._format_transcript_for_prompt(chunked_data)
        lines = result.splitlines()
        assert lines[0].startswith("[S0]")
        assert lines[1].startswith("[S1]")


class TestFullPipelineFlow:
    """Integration test: mock both LLM calls and verify end-to-end flow."""

    @patch("meeting_transcription.utils.llm_client.LLMClient")
    def test_convert_then_attribute(self, mock_llm_cls: MagicMock) -> None:
        """After _convert_json_to_soap_note, _run_source_attribution populates source IDs."""
        mock_client = MagicMock()
        mock_llm_cls.return_value = mock_client
        # Attribution response: claim 1 (chief_complaint) -> segments [0, 1]
        mock_client.call.return_value = '{"1": [0, 1], "2": [2]}'

        soap_json = {
            "subjective": {
                "chief_complaint": "Work stress causing anxiety.",
                "mood_affect": "Anxious but coping.",
            },
            "objective": {},
            "assessment": {},
            "plan": {},
        }

        soap_note = MeetingTranscriptionSOAPService._convert_json_to_soap_note(soap_json)

        # Before attribution: no sources
        assert soap_note.subjective.chief_complaint.source_segment_ids == []

        MeetingTranscriptionSOAPService._run_source_attribution(soap_note, SAMPLE_TRANSCRIPT)

        # After attribution: sources populated
        assert soap_note.subjective.chief_complaint.source_segment_ids == [0, 1]
        assert soap_note.subjective.mood_affect.source_segment_ids == [2]

        # Narrative still works correctly
        narrative = soap_note.to_narrative()
        assert "Work stress causing anxiety." in narrative["subjective"]

        # Structured model includes sources
        structured = soap_note.to_structured_model()
        assert structured.subjective.chief_complaint.source_segment_ids == [0, 1]


class TestEmbeddingVerificationWiring:
    """Test inline embedding verification wiring in _run_source_attribution."""

    @patch.dict(os.environ, {"ENABLE_EMBEDDING_VERIFICATION": "true"})
    @patch("meeting_transcription.utils.llm_client.LLMClient")
    def test_verification_runs_when_flag_enabled(self, mock_llm_cls: MagicMock) -> None:
        """When ENABLE_EMBEDDING_VERIFICATION=true, verification runs after attribution."""
        mock_client = MagicMock()
        mock_llm_cls.return_value = mock_client
        mock_client.call.return_value = '{"1": [1], "2": [2]}'

        mock_service = MagicMock()
        mock_service.verify_attributions.return_value = [
            VerificationResult(
                claim_key="subjective.chief_complaint",
                original_segment_ids=[1],
                confidence_score=0.92,
                confidence_level="high",
                possible_match_segment_ids=[3],
            ),
            VerificationResult(
                claim_key="subjective.mood_affect",
                original_segment_ids=[2],
                confidence_score=0.65,
                confidence_level="medium",
                possible_match_segment_ids=[],
            ),
        ]

        soap_note = _make_soap_note()

        with (
            patch.object(SourceVerificationService, "__init__", return_value=None),
            patch.object(
                SourceVerificationService,
                "verify_attributions",
                mock_service.verify_attributions,
            ),
        ):
            MeetingTranscriptionSOAPService._run_source_attribution(soap_note, SAMPLE_TRANSCRIPT)

        # Verification service was called
        mock_service.verify_attributions.assert_called_once()

        # Confidence fields were populated
        assert soap_note.subjective.chief_complaint.confidence_score == 0.92
        assert soap_note.subjective.chief_complaint.confidence_level == "high"
        assert soap_note.subjective.chief_complaint.possible_match_segment_ids == [3]
        assert soap_note.subjective.mood_affect.confidence_score == 0.65
        assert soap_note.subjective.mood_affect.confidence_level == "medium"

    @patch.dict(os.environ, {}, clear=False)
    @patch("meeting_transcription.utils.llm_client.LLMClient")
    def test_verification_skipped_when_flag_off(self, mock_llm_cls: MagicMock) -> None:
        """When ENABLE_EMBEDDING_VERIFICATION is not set, verification is skipped."""
        # Ensure the flag is not set
        os.environ.pop("ENABLE_EMBEDDING_VERIFICATION", None)

        mock_client = MagicMock()
        mock_llm_cls.return_value = mock_client
        mock_client.call.return_value = '{"1": [1], "2": [2]}'

        soap_note = _make_soap_note()
        MeetingTranscriptionSOAPService._run_source_attribution(soap_note, SAMPLE_TRANSCRIPT)

        # Attribution works
        assert soap_note.subjective.chief_complaint.source_segment_ids == [1]
        # But confidence fields remain at defaults (no verification ran)
        assert soap_note.subjective.chief_complaint.confidence_score == 0.0
        assert soap_note.subjective.chief_complaint.confidence_level == "unverified"

    @patch.dict(os.environ, {"ENABLE_EMBEDDING_VERIFICATION": "true"})
    @patch("meeting_transcription.utils.llm_client.LLMClient")
    def test_verification_failure_does_not_crash_pipeline(self, mock_llm_cls: MagicMock) -> None:
        """If verification fails, attribution still succeeds with defaults."""
        mock_client = MagicMock()
        mock_llm_cls.return_value = mock_client
        mock_client.call.return_value = '{"1": [1], "2": [2]}'

        soap_note = _make_soap_note()
        # Make verification crash
        with patch.object(
            SourceVerificationService,
            "__init__",
            side_effect=RuntimeError("NLI model unavailable"),
        ):
            # Should not raise
            MeetingTranscriptionSOAPService._run_source_attribution(soap_note, SAMPLE_TRANSCRIPT)

        # Attribution still worked
        assert soap_note.subjective.chief_complaint.source_segment_ids == [1]
        # Confidence fields remain at defaults
        assert soap_note.subjective.chief_complaint.confidence_score == 0.0
        assert soap_note.subjective.chief_complaint.confidence_level == "unverified"

    @pytest.mark.spacy
    @patch.dict(os.environ, {"ENABLE_EMBEDDING_VERIFICATION": "true"})
    @patch("meeting_transcription.utils.llm_client.LLMClient")
    def test_verification_with_real_mock_services(self, mock_llm_cls: MagicMock) -> None:
        """Integration test: run verification with MockEmbeddingService + MockNLIService.

        With hybrid signal pipeline wired in, verification uses the signal chain
        (token_overlap, embedding_sim, hedging, minicheck) instead of NLI-only.
        MockEmbeddingService returns deterministic embeddings, so we verify that
        the pipeline runs end-to-end and populates signal_used.
        """
        mock_client = MagicMock()
        mock_llm_cls.return_value = mock_client
        mock_client.call.return_value = (
            '{"1": [1, 2], "2": [2], "3": [0], "4": [1], "5": [2], "6": [3]}'
        )

        soap_note = _make_soap_note()

        # Patch the actual classes so local imports in the service get mocks
        with (
            patch.object(
                GoogleEmbeddingService,
                "__init__",
                MockEmbeddingService.__init__,
            ),
            patch.object(
                GoogleEmbeddingService,
                "embed_texts",
                MockEmbeddingService().embed_texts,
            ),
            patch.object(
                DeBERTaNLIService,
                "__init__",
                lambda self, **_kwargs: MockNLIService.__init__(
                    self, default_label="entailment", default_score=0.92
                ),
            ),
            patch.object(
                DeBERTaNLIService,
                "classify_batch",
                MockNLIService(default_label="entailment", default_score=0.92).classify_batch,
            ),
            patch.object(
                DeBERTaNLIService,
                "classify",
                MockNLIService(default_label="entailment", default_score=0.92).classify,
            ),
        ):
            MeetingTranscriptionSOAPService._run_source_attribution(soap_note, SAMPLE_TRANSCRIPT)

        # Attribution populated
        assert soap_note.subjective.chief_complaint.source_segment_ids == [1, 2]
        # Verification ran: signal_used is populated (hybrid pipeline)
        assert soap_note.subjective.chief_complaint.signal_used != ""
