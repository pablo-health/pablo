# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note-type generation service.

Dispatches on a registry key (``"soap"``, ``"narrative"``, …) so the
backend can produce any note format registered in
:mod:`app.notes.registry`. The service composes its prompt from each
section/field's ``ai_hint`` for schema-driven types; SOAP continues to
use the existing ``MentalHealthPlugin`` pipeline so its output stays
byte-identical to the pre-generalisation golden fixtures.
"""

import json
import logging
import os
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

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
from ..notes import NoteTypeDefinition, NoteTypeRegistry, get_default_registry
from .source_attribution_service import (
    build_attribution_prompt,
    build_claims_from_soap,
    format_transcript_with_segment_ids,
    parse_attribution_response,
)

logger = logging.getLogger(__name__)


SOAP_KEY = "soap"


class _LLMClientLike(Protocol):
    """Narrow surface of :class:`meeting_transcription.utils.llm_client.LLMClient`.

    Tests inject a fake via ``MeetingTranscriptionNoteService(llm_client_factory=...)``
    so they do not have to import the real (and in CI, 3.14-incompatible) module.
    """

    def call_structured(
        self,
        prompt: str,
        response_schema: dict[str, Any],
        max_tokens: int = ...,
        temperature: float = ...,
    ) -> dict[str, Any]: ...


def _default_llm_client_factory() -> _LLMClientLike:
    from meeting_transcription.utils.llm_client import LLMClient

    return LLMClient()


@dataclass
class GeneratedNote:
    """Result returned by :class:`NoteGenerationService`.

    ``content`` is the registry-shaped dict ``{section_key: {field_key: value}}``
    that persists to the ``note_content`` JSONB column. For SOAP, the
    structured :class:`SOAPNote` dataclass (with per-sentence source
    attribution) is additionally exposed on ``soap_note`` — downstream
    code that still depends on SOAPNote continues to work unchanged.
    """

    note_type: str
    content: dict[str, Any] = field(default_factory=dict)
    soap_note: SOAPNote | None = None


class NoteGenerationService(ABC):
    """Abstract interface for note generation across all note types."""

    @abstractmethod
    def generate_note(
        self,
        note_type: str,
        transcript: Transcript,
        patient: Patient,
        session_date: datetime,
    ) -> GeneratedNote:
        """Generate a note of ``note_type`` from ``transcript``.

        Raises:
            KeyError: If ``note_type`` is not registered.
            ValueError: If generation fails.
        """


class MeetingTranscriptionNoteService(NoteGenerationService):
    """Real implementation backed by the meeting-transcription pipeline.

    SOAP routes through ``MentalHealthPlugin`` (behavior-preserving). All
    other note types drive off the registry: prompt is composed from each
    section/field's ``ai_hint`` and the LLM returns structured JSON shaped
    to the registry.
    """

    def __init__(
        self,
        therapist_name: str | None = None,
        registry: NoteTypeRegistry | None = None,
        llm_client_factory: Callable[[], _LLMClientLike] | None = None,
    ) -> None:
        self.therapist_name = therapist_name or "Therapist"
        self.registry = registry or get_default_registry()
        self._llm_client_factory = llm_client_factory or _default_llm_client_factory

    def generate_note(
        self,
        note_type: str,
        transcript: Transcript,
        patient: Patient,
        session_date: datetime,
    ) -> GeneratedNote:
        definition = self.registry.get(note_type)
        if note_type == SOAP_KEY:
            soap_note = self._generate_soap_via_plugin(transcript, patient, session_date)
            return GeneratedNote(
                note_type=SOAP_KEY,
                content=soap_note.to_dict(),
                soap_note=soap_note,
            )
        content = self._generate_via_registry(definition, transcript, patient, session_date)
        return GeneratedNote(note_type=note_type, content=content)

    def _generate_soap_via_plugin(
        self, transcript: Transcript, patient: Patient, session_date: datetime
    ) -> SOAPNote:
        """Generate a SOAP note via the mental-health pipeline plus source attribution."""
        from backend.plugins.mental_health.plugin import (  # type: ignore[import-untyped,import-not-found]
            get_plugin,
        )

        plugin = get_plugin()
        plugin.configure(
            {
                "include_verbatim_quotes": True,
                "risk_assessment_required": True,
                "hipaa_compliant_mode": True,
            }
        )

        metadata = {
            "client_name": "the client",
            "session_date": session_date.isoformat().split("T", maxsplit=1)[0],
            "session_number": "1",
            "therapist_name": self.therapist_name,
        }
        if patient.diagnosis:
            metadata["diagnosis"] = patient.diagnosis

        with tempfile.TemporaryDirectory() as tmpdir:
            if transcript.format in ("txt", "vtt", "google_meet"):
                segments = parse_text_to_combined_format(transcript.content)
            elif transcript.format == "json":
                segments = json.loads(transcript.content)
                if not isinstance(segments, list):
                    raise ValueError("JSON transcript must be a list of segments")
            else:
                raise ValueError(f"Unsupported transcript format: {transcript.format}")

            raw_path = os.path.join(tmpdir, "transcript_raw.json")  # noqa: PTH118
            with open(raw_path, "w") as f:  # noqa: PTH123
                json.dump(segments, f, indent=2)

            combined_path = os.path.join(tmpdir, "transcript_combined.json")  # noqa: PTH118
            combine_transcript_words.combine_transcript_words(raw_path, combined_path)  # type: ignore[no-untyped-call]

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

            soap_note_path = outputs.get("soap_note_json")
            if not soap_note_path or not os.path.exists(soap_note_path):  # noqa: PTH110
                raise ValueError("Pipeline did not generate soap_note.json")

            with open(soap_note_path) as f:  # noqa: PTH123
                soap_data = json.load(f)

            soap_note = self._convert_json_to_soap_note(soap_data)
            self._run_source_attribution(soap_note, transcript.content)
            return soap_note

    def _generate_via_registry(
        self,
        definition: NoteTypeDefinition,
        transcript: Transcript,
        patient: Patient,
        session_date: datetime,
    ) -> dict[str, Any]:
        """Generate registry-driven content for any non-SOAP note type.

        Prompt is composed from each section/field's ``ai_hint``; the LLM
        is asked to return JSON matching the registry shape.
        """
        prompt = _build_registry_prompt(definition, transcript, patient, session_date)
        schema = _build_registry_response_schema(definition)

        try:
            llm_client = self._llm_client_factory()
            response = llm_client.call_structured(
                prompt=prompt,
                response_schema=schema,
                max_tokens=4096,
                temperature=0.2,
            )
        except Exception as e:
            logger.exception("LLM generation failed for note_type=%s", definition.key)
            raise ValueError(f"Note generation failed: {e}") from e

        return _coerce_registry_response(definition, response)

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


class MockNoteGenerationService(NoteGenerationService):
    """Mock implementation for testing without LLM credentials.

    Returns deterministic content for every registered note type. SOAP
    reuses the pre-change mock SOAP note so existing goldens keep passing.
    """

    def __init__(self, registry: NoteTypeRegistry | None = None) -> None:
        self.registry = registry or get_default_registry()

    def generate_note(
        self,
        note_type: str,
        transcript: Transcript,  # noqa: ARG002  # deterministic mock ignores transcript
        patient: Patient,
        session_date: datetime,  # noqa: ARG002  # deterministic mock ignores date
    ) -> GeneratedNote:
        definition = self.registry.get(note_type)
        if note_type == SOAP_KEY:
            soap_note = _mock_soap_note(patient)
            return GeneratedNote(
                note_type=SOAP_KEY,
                content=soap_note.to_dict(),
                soap_note=soap_note,
            )
        content = _mock_registry_content(definition, patient)
        return GeneratedNote(note_type=note_type, content=content)


def _mock_soap_note(patient: Patient) -> SOAPNote:
    """Deterministic SOAP note used by :class:`MockNoteGenerationService`."""
    diagnosis = patient.diagnosis or "General mental health concerns"

    def _s(text: str, ids: list[int] | None = None) -> SOAPSentence:
        return SOAPSentence(text=text, source_segment_ids=ids or [])

    return SOAPNote(
        subjective=SubjectiveNote(
            chief_complaint=_s(f"Client reports ongoing concerns related to {diagnosis}.", [0, 1]),
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


def _mock_registry_content(definition: NoteTypeDefinition, patient: Patient) -> dict[str, Any]:
    """Deterministic registry-shaped content for non-SOAP note types."""
    diagnosis = patient.diagnosis or "general concerns"
    content: dict[str, Any] = {}
    for section in definition.sections:
        section_content: dict[str, Any] = {}
        for f in section.fields:
            if f.kind == "list":
                section_content[f.key] = [
                    f"Mock {f.label} item A ({diagnosis}).",
                    f"Mock {f.label} item B.",
                ]
            else:
                section_content[f.key] = (
                    f"Mock {section.label} / {f.label} content for session ({diagnosis})."
                )
        content[section.key] = section_content
    return content


# --- Registry-driven prompt + schema composition ---


def _build_registry_prompt(
    definition: NoteTypeDefinition,
    transcript: Transcript,
    patient: Patient,
    session_date: datetime,
) -> str:
    """Compose a prompt describing the registry shape and each field's ``ai_hint``."""
    lines: list[str] = [
        f"You are a clinical documentation assistant producing a {definition.label} note.",
        "",
        definition.description,
        "",
        "Populate each field below using the provided session transcript. Use neutral, "
        "clinically-appropriate language. If a field cannot be inferred from the "
        "transcript, return an empty string (or empty list for list fields).",
        "",
        "Fields:",
    ]
    for section in definition.sections:
        lines.append(f"- Section '{section.key}' ({section.label}):")
        for f in section.fields:
            hint = f.ai_hint or f.label
            kind_label = {
                "text": "free-form string",
                "list": "list of short strings",
                "structured": "nested object",
            }[f.kind]
            lines.append(f"    * {f.key} ({kind_label}) — {hint}")
    lines.extend(
        [
            "",
            f"Session date: {session_date.isoformat().split('T', 1)[0]}",
        ]
    )
    if patient.diagnosis:
        lines.append(f"Working diagnosis: {patient.diagnosis}")
    lines.extend(
        [
            "",
            "Transcript:",
            transcript.content,
        ]
    )
    return "\n".join(lines)


def _build_registry_response_schema(definition: NoteTypeDefinition) -> dict[str, Any]:
    """Build a JSON schema stub matching the registry shape."""
    sections: dict[str, Any] = {}
    for section in definition.sections:
        fields: dict[str, Any] = {}
        for f in section.fields:
            if f.kind == "list":
                fields[f.key] = {"type": "array", "items": {"type": "string"}}
            elif f.kind == "structured":
                fields[f.key] = {"type": "object"}
            else:
                fields[f.key] = {"type": "string"}
        sections[section.key] = {"type": "object", "properties": fields}
    return {"type": "object", "properties": sections}


def _coerce_registry_response(
    definition: NoteTypeDefinition, response: dict[str, Any]
) -> dict[str, Any]:
    """Coerce the LLM response into the registry shape, filling in missing fields."""
    content: dict[str, Any] = {}
    for section in definition.sections:
        raw_section = response.get(section.key, {}) or {}
        if not isinstance(raw_section, dict):
            raw_section = {}
        section_content: dict[str, Any] = {}
        for f in section.fields:
            raw_value = raw_section.get(f.key)
            if f.kind == "list":
                if isinstance(raw_value, list):
                    section_content[f.key] = [str(item).strip() for item in raw_value if item]
                else:
                    section_content[f.key] = []
            elif f.kind == "structured":
                section_content[f.key] = raw_value if isinstance(raw_value, dict) else {}
            else:
                section_content[f.key] = str(raw_value).strip() if raw_value else ""
        content[section.key] = section_content
    return content
