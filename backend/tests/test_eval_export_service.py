# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for eval export service."""

import uuid
from datetime import UTC, datetime
from unittest.mock import Mock

from app.models.session import (
    AssessmentNote,
    ExportStatus,
    ObjectiveNote,
    PlanNote,
    SOAPNote,
    SOAPSentence,
    SubjectiveNote,
    TherapySession,
    Transcript,
)
from app.services.entity_naturalizer import RedactionResult
from app.services.eval_export_service import EvalExportService
from app.services.pii_redaction_service import PIIRedactionService
from app.settings import Settings


class TestShouldQueueForExport:
    """Test should_queue_for_export decision logic."""

    def setup_method(self):
        """Set up test fixtures."""
        self.settings = Settings(
            low_rating_threshold=2,
            high_rating_threshold=4,
            high_rating_sample_rate=0.10,
        )
        self.pii_service = Mock(spec=PIIRedactionService)
        self.service = EvalExportService(self.pii_service, self.settings)

    def test_always_queue_low_rating_1(self):
        """Test that rating 1 is always queued."""
        decision = self.service.should_queue_for_export(quality_rating=1)
        assert decision.should_queue is True
        assert "Low rating" in decision.reason

    def test_always_queue_low_rating_2(self):
        """Test that rating 2 is always queued (at threshold)."""
        decision = self.service.should_queue_for_export(quality_rating=2)
        assert decision.should_queue is True
        assert "Low rating" in decision.reason

    def test_never_queue_mid_range(self):
        """Test that rating 3 is never queued."""
        decision = self.service.should_queue_for_export(quality_rating=3)
        assert decision.should_queue is False
        assert "Mid-range rating" in decision.reason

    def test_high_rating_sampled_4(self):
        """Test that rating 4 is sampled (at threshold)."""
        # Run multiple times to test probabilistic behavior
        results = [self.service.should_queue_for_export(quality_rating=4) for _ in range(100)]
        queued_count = sum(1 for r in results if r.should_queue)

        # With 10% sample rate, expect around 10 out of 100 (allow some variance)
        assert 0 < queued_count < 100, "Should sample some but not all"

    def test_sample_rate_zero(self):
        """Test that sample rate of 0.0 never queues high ratings."""
        settings = Settings(
            low_rating_threshold=2,
            high_rating_threshold=4,
            high_rating_sample_rate=0.0,
        )
        service = EvalExportService(self.pii_service, settings)

        # Run multiple times to ensure no false positives
        for _ in range(10):
            decision = service.should_queue_for_export(quality_rating=5)
            assert decision.should_queue is False

    def test_sample_rate_one(self):
        """Test that sample rate of 1.0 always queues high ratings."""
        settings = Settings(
            low_rating_threshold=2,
            high_rating_threshold=4,
            high_rating_sample_rate=1.0,
        )
        service = EvalExportService(self.pii_service, settings)

        # Run multiple times to ensure consistency
        for _ in range(10):
            decision = service.should_queue_for_export(quality_rating=5)
            assert decision.should_queue is True


class TestQueueSessionForExport:
    """Test queue_session_for_export functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.settings = Settings(
            low_rating_threshold=2,
            high_rating_threshold=4,
            high_rating_sample_rate=0.10,
        )
        self.pii_service = Mock(spec=PIIRedactionService)
        self.service = EvalExportService(self.pii_service, self.settings)

        # Create a test session
        self.session = TherapySession(
            id=str(uuid.uuid4()),
            user_id="user123",
            patient_id="patient456",
            session_date="2026-01-15T10:00:00Z",
            session_number=1,
            status="finalized",
            transcript=Transcript(format="txt", content="Patient John Doe discussed anxiety."),
            created_at=datetime.now(UTC).isoformat(),
            soap_note=SOAPNote.from_dict(
                {
                    "subjective": "Patient John Doe reports anxiety.",
                    "objective": "Patient appears nervous.",
                    "assessment": "Generalized anxiety disorder.",
                    "plan": "Continue therapy sessions.",
                }
            ),
            quality_rating=1,
        )

    def test_transcript_redaction(self):
        """Test that transcript is redacted."""
        # Mock the PII service to return redacted results
        self.pii_service.redact.return_value = RedactionResult(
            redacted_text="Patient <PERSON_1> discussed anxiety.",
            naturalized_text="Patient Jane Smith discussed anxiety.",
            entities=[],
            entity_count=1,
        )

        result = self.service.queue_session_for_export(self.session)

        # Verify PII service was called with transcript
        self.pii_service.redact.assert_called()
        assert result.redacted_transcript == "Patient <PERSON_1> discussed anxiety."
        assert result.naturalized_transcript == "Patient Jane Smith discussed anxiety."

    def test_export_status_set_to_pending_review(self):
        """Test that export_status is set to PENDING_REVIEW."""
        self.pii_service.redact.return_value = RedactionResult(
            redacted_text="Redacted",
            naturalized_text="Naturalized",
            entities=[],
            entity_count=0,
        )

        result = self.service.queue_session_for_export(self.session)

        assert result.export_status == ExportStatus.PENDING_REVIEW.value

    def test_export_queued_at_timestamp_set(self):
        """Test that export_queued_at timestamp is set."""
        self.pii_service.redact.return_value = RedactionResult(
            redacted_text="Redacted",
            naturalized_text="Naturalized",
            entities=[],
            entity_count=0,
        )

        before = datetime.now(UTC)
        result = self.service.queue_session_for_export(self.session)
        after = datetime.now(UTC)

        assert result.export_queued_at is not None
        queued_at = datetime.fromisoformat(result.export_queued_at.replace("Z", "+00:00"))
        assert before <= queued_at <= after

    def test_error_handling_sets_skipped_status(self):
        """Test that redaction failure sets export_status to SKIPPED."""
        # Mock PII service to raise an exception
        self.pii_service.redact.side_effect = Exception("Redaction failed")

        result = self.service.queue_session_for_export(self.session)

        # Should not raise exception
        assert result.export_status == ExportStatus.SKIPPED.value
        assert result.export_queued_at is not None


class TestRedactSoapNoteStructured:
    """Test _redact_soap_note preserves SOAPSentence structure and source_segment_ids."""

    def setup_method(self) -> None:
        self.settings = Settings(
            low_rating_threshold=2,
            high_rating_threshold=4,
            high_rating_sample_rate=0.10,
        )
        self.pii_service = Mock(spec=PIIRedactionService)
        self.service = EvalExportService(self.pii_service, self.settings)

    def _mock_redact(self, text: str, _session_id: str) -> RedactionResult:
        """Replace 'John Doe' with placeholder/naturalized versions."""
        redacted = text.replace("John Doe", "<PERSON_1>")
        naturalized = text.replace("John Doe", "Jane Smith")
        return RedactionResult(
            redacted_text=redacted,
            naturalized_text=naturalized,
            entities=[],
            entity_count=1 if "John Doe" in text else 0,
        )

    def test_source_segment_ids_preserved(self) -> None:
        """source_segment_ids (integers) pass through unchanged after redaction."""
        self.pii_service.redact.side_effect = self._mock_redact

        soap = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(
                    text="John Doe reports anxiety.", source_segment_ids=[0, 1]
                ),
                mood_affect=SOAPSentence(text="Anxious.", source_segment_ids=[2]),
            ),
            assessment=AssessmentNote(
                clinical_impression=SOAPSentence(text="GAD.", source_segment_ids=[5, 6]),
            ),
        )

        redacted, naturalized = self.service._redact_soap_note(soap, "sess-1")

        assert redacted.subjective.chief_complaint.source_segment_ids == [0, 1]
        assert redacted.subjective.mood_affect.source_segment_ids == [2]
        assert redacted.assessment.clinical_impression.source_segment_ids == [5, 6]
        assert naturalized.subjective.chief_complaint.source_segment_ids == [0, 1]
        assert naturalized.assessment.clinical_impression.source_segment_ids == [5, 6]

    def test_text_fields_are_redacted(self) -> None:
        """SOAPSentence.text fields containing PII are properly redacted."""
        self.pii_service.redact.side_effect = self._mock_redact

        soap = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(
                    text="John Doe reports anxiety.", source_segment_ids=[0]
                ),
            ),
            objective=ObjectiveNote(
                behavior=SOAPSentence(text="John Doe was cooperative."),
            ),
        )

        redacted, naturalized = self.service._redact_soap_note(soap, "sess-1")

        assert redacted.subjective.chief_complaint.text == "<PERSON_1> reports anxiety."
        assert naturalized.subjective.chief_complaint.text == "Jane Smith reports anxiety."
        assert redacted.objective.behavior.text == "<PERSON_1> was cooperative."
        assert naturalized.objective.behavior.text == "Jane Smith was cooperative."

    def test_list_fields_redacted_with_ids_preserved(self) -> None:
        """List fields (symptoms, interventions, etc.) are redacted per-element."""
        self.pii_service.redact.side_effect = self._mock_redact

        soap = SOAPNote(
            subjective=SubjectiveNote(
                symptoms=[
                    SOAPSentence(text="John Doe has insomnia", source_segment_ids=[3]),
                    SOAPSentence(text="Racing thoughts", source_segment_ids=[4]),
                ],
            ),
            plan=PlanNote(
                interventions_used=[
                    SOAPSentence(text="CBT for John Doe", source_segment_ids=[8]),
                ],
            ),
        )

        redacted, naturalized = self.service._redact_soap_note(soap, "sess-1")

        assert redacted.subjective.symptoms is not None
        assert len(redacted.subjective.symptoms) == 2
        assert redacted.subjective.symptoms[0].text == "<PERSON_1> has insomnia"
        assert redacted.subjective.symptoms[0].source_segment_ids == [3]
        assert redacted.subjective.symptoms[1].text == "Racing thoughts"
        assert redacted.subjective.symptoms[1].source_segment_ids == [4]

        assert naturalized.subjective.symptoms is not None
        assert naturalized.subjective.symptoms[0].text == "Jane Smith has insomnia"

        assert redacted.plan.interventions_used is not None
        assert redacted.plan.interventions_used[0].text == "CBT for <PERSON_1>"
        assert redacted.plan.interventions_used[0].source_segment_ids == [8]

    def test_none_list_fields_stay_none(self) -> None:
        """None list fields remain None after redaction."""
        self.pii_service.redact.side_effect = self._mock_redact

        soap = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(text="Anxiety."),
                symptoms=None,
            ),
            plan=PlanNote(
                interventions_used=None,
                homework_assignments=None,
                next_steps=None,
            ),
        )

        redacted, naturalized = self.service._redact_soap_note(soap, "sess-1")

        assert redacted.subjective.symptoms is None
        assert redacted.plan.interventions_used is None
        assert redacted.plan.homework_assignments is None
        assert naturalized.subjective.symptoms is None
        assert naturalized.plan.interventions_used is None

    def test_empty_text_fields_skip_redaction(self) -> None:
        """Empty SOAPSentence.text fields are not sent to PII service."""
        call_texts: list[str] = []

        def _tracking_redact(text: str, session_id: str) -> RedactionResult:
            call_texts.append(text)
            return RedactionResult(
                redacted_text=text,
                naturalized_text=text,
                entities=[],
                entity_count=0,
            )

        self.pii_service.redact.side_effect = _tracking_redact

        soap = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(text="Anxiety.", source_segment_ids=[0]),
                mood_affect=SOAPSentence(text="", source_segment_ids=[]),
            ),
        )

        redacted, _ = self.service._redact_soap_note(soap, "sess-1")

        # Only non-empty text was sent to PII service
        assert "" not in call_texts
        assert "Anxiety." in call_texts
        # Empty field preserved with empty text
        assert redacted.subjective.mood_affect.text == ""

    def test_redacted_soap_note_is_valid_structure(self) -> None:
        """Redacted SOAP note can produce narrative and structured model."""
        self.pii_service.redact.side_effect = self._mock_redact

        soap = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(
                    text="John Doe reports anxiety.", source_segment_ids=[0, 1]
                ),
                mood_affect=SOAPSentence(text="Low mood.", source_segment_ids=[2]),
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

        redacted, _naturalized = self.service._redact_soap_note(soap, "sess-1")

        # Narrative still works
        narrative = redacted.to_narrative()
        assert "<PERSON_1> reports anxiety." in narrative["subjective"]

        # Structured model still works
        structured = redacted.to_structured_model()
        assert structured.subjective.chief_complaint.text == "<PERSON_1> reports anxiety."
        assert structured.subjective.chief_complaint.source_segment_ids == [0, 1]

    def test_all_sections_redacted(self) -> None:
        """Every section's fields are individually redacted."""
        self.pii_service.redact.side_effect = self._mock_redact

        soap = SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=SOAPSentence(text="John Doe complains."),
                client_narrative=SOAPSentence(text="John Doe says he is sad."),
            ),
            objective=ObjectiveNote(
                appearance=SOAPSentence(text="John Doe well-groomed."),
                affect_observed=SOAPSentence(text="Flat."),
            ),
            assessment=AssessmentNote(
                clinical_impression=SOAPSentence(text="John Doe has GAD."),
                functioning_level=SOAPSentence(text="Moderate."),
            ),
            plan=PlanNote(
                next_session=SOAPSentence(text="See John Doe in a week."),
                homework_assignments=[
                    SOAPSentence(text="John Doe should journal."),
                ],
            ),
        )

        redacted, _ = self.service._redact_soap_note(soap, "sess-1")

        assert "John Doe" not in redacted.subjective.chief_complaint.text
        assert "John Doe" not in redacted.subjective.client_narrative.text
        assert "John Doe" not in redacted.objective.appearance.text
        assert "John Doe" not in redacted.assessment.clinical_impression.text
        assert "John Doe" not in redacted.plan.next_session.text
        assert redacted.plan.homework_assignments is not None
        assert "John Doe" not in redacted.plan.homework_assignments[0].text
