# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""SOAP note generation service using meeting-transcription pipeline."""

import json
import logging
import os
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from meeting_transcription.pipeline import combine_transcript_words
from meeting_transcription.pipeline.parse_text_transcript import (
    parse_text_to_combined_format,
)

from ..models import (
    AssessmentNote,
    ObjectiveNote,
    Patient,
    PlanNote,
    SOAPNote,
    SOAPSentence,
    SubjectiveNote,
    Transcript,
)
from .source_attribution_service import (
    build_attribution_prompt,
    build_claims_from_soap,
    format_transcript_with_segment_ids,
    parse_attribution_response,
)

logger = logging.getLogger(__name__)


class SOAPGenerationService(ABC):
    """Abstract interface for SOAP note generation."""

    @abstractmethod
    def generate_soap_note(
        self, transcript: Transcript, patient: Patient, session_date: datetime
    ) -> SOAPNote:
        """
        Generate SOAP note from transcript.

        Args:
            transcript: Session transcript
            patient: Patient information (for context)
            session_date: Date of session

        Returns:
            Generated SOAP note

        Raises:
            ValueError: If generation fails
        """
        pass


class MeetingTranscriptionSOAPService(SOAPGenerationService):
    """
    Real implementation using meeting-transcription pipeline with MentalHealthPlugin.

    Requires LLM configuration via environment variables:
    - AI_MODEL: e.g., "anthropic:claude-sonnet-4-5"
    - ANTHROPIC_API_KEY: API key for Anthropic
    Or Google Vertex AI:
    - GOOGLE_CLOUD_PROJECT
    - GOOGLE_REGION
    """

    def __init__(self, therapist_name: str | None = None) -> None:
        """
        Initialize service.

        Args:
            therapist_name: Default therapist name (defaults to "Therapist")
        """
        self.therapist_name = therapist_name or "Therapist"

    def generate_soap_note(
        self, transcript: Transcript, patient: Patient, session_date: datetime
    ) -> SOAPNote:
        """Generate SOAP note using meeting-transcription pipeline."""
        from backend.plugins.mental_health.plugin import (  # type: ignore[import-untyped,import-not-found]
            get_plugin,
        )

        # Load and configure plugin
        plugin = get_plugin()
        plugin.configure(
            {
                "include_verbatim_quotes": True,
                "risk_assessment_required": True,
                "hipaa_compliant_mode": True,
            }
        )

        # Metadata for SOAP generation
        metadata = {
            "client_name": "the client",
            "session_date": session_date.isoformat().split("T", maxsplit=1)[0],  # Extract date part
            "session_number": "1",  # Will be set correctly in route
            "therapist_name": self.therapist_name,
        }
        if patient.diagnosis:
            metadata["diagnosis"] = patient.diagnosis

        # Use temporary directory for pipeline processing
        # Note: meeting-transcription handles LLM model selection via AI_MODEL env var
        with tempfile.TemporaryDirectory() as tmpdir:
            # Step 1: Parse transcript to segments (auto-detect format)
            if transcript.format in ("txt", "vtt", "google_meet"):
                segments = parse_text_to_combined_format(transcript.content)
            elif transcript.format == "json":
                segments = json.loads(transcript.content)
                if not isinstance(segments, list):
                    raise ValueError("JSON transcript must be a list of segments")
            else:
                raise ValueError(f"Unsupported transcript format: {transcript.format}")

            # Step 2: Save as raw transcript JSON
            raw_path = os.path.join(tmpdir, "transcript_raw.json")  # noqa: PTH118
            with open(raw_path, "w") as f:  # noqa: PTH123
                json.dump(segments, f, indent=2)

            # Step 3: Run combine_transcript_words preprocessing
            combined_path = os.path.join(tmpdir, "transcript_combined.json")  # noqa: PTH118
            combine_transcript_words.combine_transcript_words(raw_path, combined_path)  # type: ignore[no-untyped-call]

            # Step 4: Run plugin pipeline
            try:
                logger.debug("Starting SOAP pipeline processing")

                outputs = plugin.process_transcript(
                    combined_transcript_path=combined_path,
                    output_dir=tmpdir,
                    metadata=metadata,
                )

                logger.debug("SOAP pipeline completed successfully")
            except Exception as e:
                logger.exception("SOAP pipeline processing failed")
                raise ValueError(f"Pipeline processing failed: {e}") from e

            # Step 5: Read SOAP note output
            soap_note_path = outputs.get("soap_note_json")
            if not soap_note_path or not os.path.exists(soap_note_path):  # noqa: PTH110
                raise ValueError("Pipeline did not generate soap_note.json")

            with open(soap_note_path) as f:  # noqa: PTH123
                soap_data = json.load(f)

            # Convert to SOAPNote dataclass (Call 1 result)
            soap_note = self._convert_json_to_soap_note(soap_data)

            # Call 2: Source attribution — link claims to transcript segments
            self._run_source_attribution(soap_note, transcript.content)

            return soap_note

    @staticmethod
    def _run_source_attribution(soap_note: SOAPNote, transcript_content: str) -> None:
        """Run LLM Call 2 to attribute SOAP claims to transcript segments.

        Modifies soap_note in-place by populating source_segment_ids on each SOAPSentence.
        Failures are logged but do not raise — the SOAP note remains valid without sources.
        """
        try:
            from meeting_transcription.utils.llm_client import LLMClient

            indexed_transcript = format_transcript_with_segment_ids(transcript_content)
            segment_count = len(indexed_transcript.strip().splitlines())
            claims = build_claims_from_soap(soap_note)
            if not claims:
                return

            prompt = build_attribution_prompt(claims, indexed_transcript)

            llm_client = LLMClient()
            response_text = llm_client.call(
                prompt=prompt,
                max_tokens=2000,
                temperature=0.0,
            )

            parse_attribution_response(response_text, claims, max_segment_id=segment_count - 1)
            logger.info("Source attribution completed: %d claims attributed", len(claims))

            # Run embedding-based verification if enabled
            if os.getenv("ENABLE_EMBEDDING_VERIFICATION", "").lower() == "true":
                try:
                    import re as _re

                    from .embedding_service import GoogleEmbeddingService
                    from .nli_service import DeBERTaNLIService
                    from .source_verification_service import SourceVerificationService

                    segments = [
                        _re.sub(r"^\[\d{2}:\d{2}\]\s*\w+:\s*", "", line.strip())
                        for line in transcript_content.strip().splitlines()
                        if line.strip()
                    ]
                    claim_texts = {key: claim.text for key, claim in claims.items() if claim.text}
                    attribution_map = {
                        key: claim.source_segment_ids for key, claim in claims.items()
                    }

                    from ..settings import get_settings
                    from .signals import (
                        MINICHECK_AVAILABLE,
                        EmbeddingSimilaritySignal,
                        EntityConsistencySignal,
                        HedgingSignal,
                        MiniCheckSignal,
                        NegationSignal,
                        TemporalConsistencySignal,
                        TokenOverlapSignal,
                    )

                    _settings = get_settings()
                    primary = [
                        TokenOverlapSignal(),
                        EmbeddingSimilaritySignal(),
                        HedgingSignal(),
                    ]
                    if MINICHECK_AVAILABLE:
                        primary.append(
                            MiniCheckSignal(
                                model_path=_settings.minicheck_model_path,
                            )
                        )
                    verification_service = SourceVerificationService(
                        embedding_service=GoogleEmbeddingService(),
                        nli_service=DeBERTaNLIService(
                            model_name=_settings.nli_model_path,
                        ),
                        primary_signals=primary,
                        safety_signals=[
                            NegationSignal(),
                            EntityConsistencySignal(),
                            TemporalConsistencySignal(),
                        ],
                    )
                    results = verification_service.verify_attributions(
                        claim_texts, segments, attribution_map
                    )

                    for result in results:
                        if result.claim_key in claims:
                            claim = claims[result.claim_key]
                            claim.confidence_score = result.confidence_score
                            claim.confidence_level = result.confidence_level
                            claim.possible_match_segment_ids = result.possible_match_segment_ids
                            claim.signal_used = result.signal_used
                    logger.info("Source verification completed: %d claims verified", len(results))
                except Exception:
                    logger.warning(
                        "Source attribution verification failed",
                        exc_info=True,
                    )
        except Exception:
            logger.warning(
                "Source attribution (Call 2) failed — SOAP note saved without source links",
                exc_info=True,
            )

    @staticmethod
    def _convert_json_to_soap_note(soap_data: dict[str, Any]) -> SOAPNote:
        """Convert SOAP JSON from LLM to structured SOAPNote dataclass.

        Maps LLM JSON fields to structured dataclasses with SOAPSentence wrappers.
        Narrative formatting is handled by SOAPNote.to_narrative().
        """
        raw_s = soap_data.get("subjective", {})
        raw_o = soap_data.get("objective", {})
        raw_a = soap_data.get("assessment", {})
        raw_p = soap_data.get("plan", {})

        def _wrap(text: str | None) -> SOAPSentence:
            return SOAPSentence(text=text or "")

        def _wrap_list(items: list[str] | None) -> list[SOAPSentence] | None:
            if items is None:
                return None
            return [SOAPSentence(text=item) for item in items]

        return SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=_wrap(raw_s.get("chief_complaint")),
                mood_affect=_wrap(raw_s.get("mood_affect")),
                symptoms=_wrap_list(raw_s.get("symptoms")),
                client_narrative=_wrap(raw_s.get("client_narrative")),
            ),
            objective=ObjectiveNote(
                appearance=_wrap(raw_o.get("appearance")),
                behavior=_wrap(raw_o.get("behavior")),
                speech=_wrap(raw_o.get("speech")),
                thought_process=_wrap(raw_o.get("thought_process")),
                affect_observed=_wrap(raw_o.get("affect_observed")),
            ),
            assessment=AssessmentNote(
                clinical_impression=_wrap(raw_a.get("clinical_impression")),
                progress=_wrap(raw_a.get("progress")),
                risk_assessment=_wrap(raw_a.get("risk_assessment")),
                functioning_level=_wrap(raw_a.get("functioning_level")),
            ),
            plan=PlanNote(
                interventions_used=_wrap_list(raw_p.get("interventions_used")),
                homework_assignments=_wrap_list(raw_p.get("homework_assignments")),
                next_steps=_wrap_list(raw_p.get("next_steps")),
                next_session=_wrap(raw_p.get("next_session")),
            ),
        )


class MockSOAPGenerationService(SOAPGenerationService):
    """
    Mock implementation for testing without LLM credentials.

    Returns realistic, deterministic SOAP notes based on patient diagnosis.
    """

    def generate_soap_note(
        self,
        transcript: Transcript,  # noqa: ARG002
        patient: Patient,
        session_date: datetime,  # noqa: ARG002
    ) -> SOAPNote:
        """Generate mock SOAP note with realistic structured content."""
        diagnosis = patient.diagnosis or "General mental health concerns"

        def _s(text: str, ids: list[int] | None = None) -> SOAPSentence:
            return SOAPSentence(text=text, source_segment_ids=ids or [])

        return SOAPNote(
            subjective=SubjectiveNote(
                chief_complaint=_s(
                    f"Client reports ongoing concerns related to {diagnosis}.", [0, 1]
                ),
                mood_affect=_s(
                    "Anxious but hopeful; reports mood improvement since last session.", [2]
                ),
                symptoms=[
                    _s("Difficulty sleeping", [3]),
                    _s("Racing thoughts", [4]),
                    _s("Mild irritability", [5]),
                ],
                client_narrative=_s(
                    "Describes experiencing varying levels of symptoms since "
                    "last session. Reports some progress in using coping strategies "
                    "discussed previously.",
                    [1, 3, 4, 5],
                ),
            ),
            objective=ObjectiveNote(
                appearance=_s("Well-groomed and appropriately dressed."),
                behavior=_s("Cooperative and engaged throughout session. Made good eye contact."),
                speech=_s("Clear and coherent, normal rate and volume."),
                thought_process=_s("Linear and goal-directed."),
                affect_observed=_s(
                    "Congruent with mood. Demonstrated insight into presenting concerns."
                ),
            ),
            assessment=AssessmentNote(
                clinical_impression=_s(
                    f"Client continues to work on managing {diagnosis}. "
                    "Shows engagement in treatment process and willingness to utilize "
                    "therapeutic interventions.",
                    [0, 1, 6],
                ),
                progress=_s(
                    "Progress is evident in increased awareness and application of coping skills.",
                    [6],
                ),
                risk_assessment=_s(
                    "No acute safety concerns noted at this time. "
                    "Denies suicidal or homicidal ideation.",
                    [7],
                ),
                functioning_level=_s(
                    "Moderate — able to maintain daily responsibilities with "
                    "some difficulty during high-stress periods.",
                    [3, 5],
                ),
            ),
            plan=PlanNote(
                interventions_used=[
                    _s("CBT cognitive restructuring", [8]),
                    _s("Mindfulness-based stress reduction", [9]),
                ],
                homework_assignments=[
                    _s("Practice mindfulness exercises daily", [9]),
                    _s("Complete thought record worksheet", [10]),
                ],
                next_steps=[
                    _s("Review progress and adjust treatment plan as needed"),
                    _s("Introduce exposure hierarchy if anxiety symptoms persist"),
                ],
                next_session=_s("Schedule follow-up session in one week.", [11]),
            ),
        )
