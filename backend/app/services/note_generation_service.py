# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note-type generation service.

One implementation, one code path: every registered note type
(``"soap"``, ``"narrative"``, future DAP/BIRP/…) flows through
:meth:`RegistryNoteGenerationService._generate_via_registry`, which uses
the :class:`StructuredLLMGateway` to issue a single Gemini call whose
response is constrained to a JSON schema derived from the registry
shape.

A definition may opt out of the auto-built prompt by setting
:attr:`NoteTypeDefinition.prompt_builder` — SOAP does this to preserve
the hand-tuned clinical prompt migrated from the legacy plugin.
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

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
from ..notes.prompts.soap import SOAP_SYSTEM_PROMPT
from ..settings import get_settings
from .source_attribution_service import (
    build_attribution_prompt,
    build_claims_from_soap,
    format_transcript_with_segment_ids,
    parse_attribution_response,
)
from .structured_llm_gateway import (
    StructuredCompletion,
    StructuredLLMGateway,
    StructuredOutputTruncatedError,
    get_default_structured_llm_gateway,
)

logger = logging.getLogger(__name__)


SOAP_KEY = "soap"
_DEFAULT_GENERATION_PROMPT_SYSTEM = (
    "You are a clinical documentation assistant. Populate the requested "
    "note structure from the supplied therapy-session transcript. Use "
    "neutral, clinically-appropriate language. If a field cannot be "
    "inferred from the transcript, return an empty string (or empty list "
    "for list-shaped fields)."
)
_SOAP_ATTRIBUTION_SCHEMA: dict[str, Any] = {"type": "object"}
"""Permissive schema for Call-2. The response is a map of arbitrary
claim numbers → arrays of segment ids; the registered SDK schema isn't
expressive enough to constrain that shape, so we accept any object and
let :func:`parse_attribution_response` validate."""


@dataclass
class GeneratedNote:
    """Result returned by :class:`NoteGenerationService`.

    ``content`` is the registry-shaped dict ``{section_key: {field_key: value}}``
    persisted to ``NoteRow.content``. For SOAP, the :class:`SOAPNote`
    dataclass (with per-sentence source attribution) is additionally
    exposed on ``soap_note`` so downstream code that still depends on
    that shape works unchanged.
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


class RegistryNoteGenerationService(NoteGenerationService):
    """Real implementation: registry-driven prompts via the structured gateway.

    SOAP and every other registered note type share the same pipeline:

    1. Build the user prompt — either from the definition's
       ``prompt_builder`` (SOAP today) or auto-synthesized from each
       field's ``ai_hint``.
    2. Build a JSON response schema mirroring the registry shape.
    3. Call :class:`StructuredLLMGateway`, get a parsed dict back.
    4. Coerce the dict into the registry shape (filling missing fields).
    5. For SOAP only: wrap into :class:`SOAPNote` and run the Call-2
       source-attribution pass that links each generated sentence back
       to transcript segment ids.
    """

    def __init__(
        self,
        therapist_name: str | None = None,
        registry: NoteTypeRegistry | None = None,
        llm_gateway: StructuredLLMGateway | None = None,
        model: str | None = None,
    ) -> None:
        self.therapist_name = therapist_name or "Therapist"
        self.registry = registry or get_default_registry()
        self._llm_gateway = llm_gateway or get_default_structured_llm_gateway()
        self._model = model

    def _resolve_model(self) -> str:
        if self._model is not None:
            return self._model
        return get_settings().ai_model

    def generate_note(
        self,
        note_type: str,
        transcript: Transcript,
        patient: Patient,
        session_date: datetime,
    ) -> GeneratedNote:
        definition = self.registry.get(note_type)
        content = self._generate_via_registry(definition, transcript, patient, session_date)
        if note_type == SOAP_KEY:
            soap_note = _coerce_content_to_soap_note(content)
            self._run_source_attribution(soap_note, transcript.content)
            return GeneratedNote(
                note_type=SOAP_KEY,
                content=soap_note.to_dict(),
                soap_note=soap_note,
            )
        return GeneratedNote(note_type=note_type, content=content)

    def _generate_via_registry(
        self,
        definition: NoteTypeDefinition,
        transcript: Transcript,
        patient: Patient,
        session_date: datetime,
    ) -> dict[str, Any]:
        if definition.prompt_builder is not None:
            user_prompt = definition.prompt_builder(definition, transcript, patient, session_date)
            system_prompt = (
                SOAP_SYSTEM_PROMPT
                if definition.key == SOAP_KEY
                else _DEFAULT_GENERATION_PROMPT_SYSTEM
            )
        else:
            user_prompt = _build_registry_user_prompt(definition, transcript, patient, session_date)
            system_prompt = _DEFAULT_GENERATION_PROMPT_SYSTEM

        schema = _build_registry_response_schema(definition)
        completion = self._complete_structured_with_retry(
            note_key=definition.key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=schema,
        )

        return _coerce_registry_response(definition, completion.data)

    def _complete_structured_with_retry(
        self,
        *,
        note_key: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        temperature: float | None = None,
    ) -> StructuredCompletion:
        """Run a structured note completion, retrying once if truncated.

        The output budget comes from ``note_max_output_tokens`` (env-tunable,
        generous by default). Thinking models spend part of that budget on
        reasoning, so a real, full-length transcript can still truncate the
        JSON tail; when the gateway reports the response was cut at the token
        cap we retry once at twice the budget before giving up. Any other
        failure (or a second truncation) raises ``ValueError`` so the caller's
        SOAP-generation-failed path runs — preserving the existing log line.
        """
        settings = get_settings()
        base_budget = settings.note_max_output_tokens
        # A clinical note is faithful extraction, not creative writing — default
        # to the (deterministic) configured note temperature unless a caller
        # overrides it explicitly.
        temp = temperature if temperature is not None else settings.note_generation_temperature
        budgets = (base_budget, base_budget * 2)
        last_truncation: StructuredOutputTruncatedError | None = None
        for budget in budgets:
            try:
                return self._llm_gateway.complete_structured(
                    model=self._resolve_model(),
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_schema=response_schema,
                    max_output_tokens=budget,
                    temperature=temp,
                    thinking_budget=settings.note_thinking_budget,
                )
            except StructuredOutputTruncatedError as exc:
                last_truncation = exc
                logger.warning(
                    "Structured note output truncated for note_type=%s at "
                    "max_output_tokens=%d (%s)",
                    note_key,
                    budget,
                    "retrying at 2x" if budget == base_budget else "giving up after retry",
                )
                continue
            except Exception as exc:
                logger.exception("LLM generation failed for note_type=%s", note_key)
                raise ValueError(f"Note generation failed: {exc}") from exc

        logger.error(
            "LLM generation failed for note_type=%s: output still truncated "
            "after retry at %d tokens",
            note_key,
            budgets[-1],
        )
        raise ValueError(f"Note generation failed: {last_truncation}") from last_truncation

    def _run_source_attribution(self, soap_note: SOAPNote, transcript_content: str) -> None:
        """Run Call-2: ask the model which transcript segments support each claim.

        Modifies ``soap_note`` in-place by populating ``source_segment_ids``
        on each :class:`SOAPSentence`. Failures are logged but do not raise
        — the SOAP note remains valid (and persistable) without source
        links.
        """
        try:
            indexed_transcript = format_transcript_with_segment_ids(transcript_content)
            segment_count = len(indexed_transcript.strip().splitlines())
            claims = build_claims_from_soap(soap_note)
            if not claims:
                return

            prompt = build_attribution_prompt(claims, indexed_transcript)
            settings = get_settings()
            completion = self._llm_gateway.complete_structured(
                model=self._resolve_model(),
                system_prompt=(
                    "You are an evidence-attribution assistant. Map each "
                    "claim number to the transcript segment ids (the "
                    "numbers after S in [Sn]) that support it. Return "
                    "ONLY a JSON object."
                ),
                user_prompt=prompt,
                response_schema=_SOAP_ATTRIBUTION_SCHEMA,
                # The output budget is shared between reasoning and output on a
                # thinking model. On a long indexed transcript the reasoning
                # alone could exhaust a small budget and truncate with zero
                # output (the mapping never emits). So we cap thinking
                # explicitly and give the call its own generous output budget,
                # sized so reasoning + the (small) mapping always fit. This
                # call is non-fatal — truncation just drops source links — but
                # we'd rather keep grounding working on real transcripts.
                max_output_tokens=settings.note_source_attribution_max_output_tokens,
                thinking_budget=settings.note_source_attribution_thinking_budget,
                temperature=0.0,
            )
            parse_attribution_response(
                json.dumps(completion.data),
                claims,
                max_segment_id=segment_count - 1,
            )
            logger.info("Source attribution completed: %d claims attributed", len(claims))

            if os.getenv("ENABLE_EMBEDDING_VERIFICATION", "").lower() == "true":
                _run_embedding_verification(claims, transcript_content)
        except Exception:
            logger.warning(
                "Source attribution (Call 2) failed — SOAP note saved without source links",
                exc_info=True,
            )


def _run_embedding_verification(claims: dict[str, SOAPSentence], transcript_content: str) -> None:
    """Re-rank Call-2 attributions with embedding + NLI signals.

    Off by default — opt-in via ``ENABLE_EMBEDDING_VERIFICATION=true``.
    Kept isolated so a missing optional dep / model file fails this
    block alone without taking down the rest of source attribution.
    """
    try:
        import re as _re

        from ..settings import get_settings as _get_settings
        from .embedding_service import GoogleEmbeddingService
        from .nli_service import DeBERTaNLIService
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
        from .source_verification_service import SourceVerificationService

        segments = [
            _re.sub(r"^\[\d{2}:\d{2}\]\s*\w+:\s*", "", line.strip())
            for line in transcript_content.strip().splitlines()
            if line.strip()
        ]
        claim_texts = {key: claim.text for key, claim in claims.items() if claim.text}
        attribution_map = {key: claim.source_segment_ids for key, claim in claims.items()}

        settings = _get_settings()
        primary = [TokenOverlapSignal(), EmbeddingSimilaritySignal(), HedgingSignal()]
        if MINICHECK_AVAILABLE:
            primary.append(MiniCheckSignal(model_path=settings.minicheck_model_path))
        verification_service = SourceVerificationService(
            embedding_service=GoogleEmbeddingService(),
            nli_service=DeBERTaNLIService(model_name=settings.nli_model_path),
            primary_signals=primary,
            safety_signals=[
                NegationSignal(),
                EntityConsistencySignal(),
                TemporalConsistencySignal(),
            ],
        )
        results = verification_service.verify_attributions(claim_texts, segments, attribution_map)
        for result in results:
            if result.claim_key in claims:
                claim = claims[result.claim_key]
                claim.confidence_score = result.confidence_score
                claim.confidence_level = result.confidence_level
                claim.possible_match_segment_ids = result.possible_match_segment_ids
                claim.signal_used = result.signal_used
        logger.info("Source verification completed: %d claims verified", len(results))
    except Exception:
        logger.warning("Source attribution verification failed", exc_info=True)


def _coerce_content_to_soap_note(content: dict[str, Any]) -> SOAPNote:
    """Convert registry-shaped SOAP dict to :class:`SOAPNote` dataclasses.

    The registry shape (``{section: {field: value}}``) matches the SOAP
    JSON the legacy plugin produced; this is the same field-wrapping
    code, lifted unchanged from
    ``_generate_soap_via_plugin._convert_json_to_soap_note``.
    """
    s = content.get("subjective") or {}
    o = content.get("objective") or {}
    a = content.get("assessment") or {}
    p = content.get("plan") or {}

    def _wrap(text: str | None) -> SOAPSentence:
        return SOAPSentence(text=text or "")

    def _wrap_list(items: list[str] | None) -> list[SOAPSentence] | None:
        if items is None:
            return None
        return [SOAPSentence(text=item) for item in items]

    return SOAPNote(
        subjective=SubjectiveNote(
            chief_complaint=_wrap(s.get("chief_complaint")),
            mood_affect=_wrap(s.get("mood_affect")),
            symptoms=_wrap_list(s.get("symptoms")),
            client_narrative=_wrap(s.get("client_narrative")),
        ),
        objective=ObjectiveNote(
            appearance=_wrap(o.get("appearance")),
            behavior=_wrap(o.get("behavior")),
            speech=_wrap(o.get("speech")),
            thought_process=_wrap(o.get("thought_process")),
            affect_observed=_wrap(o.get("affect_observed")),
        ),
        assessment=AssessmentNote(
            clinical_impression=_wrap(a.get("clinical_impression")),
            progress=_wrap(a.get("progress")),
            risk_assessment=_wrap(a.get("risk_assessment")),
            functioning_level=_wrap(a.get("functioning_level")),
        ),
        plan=PlanNote(
            interventions_used=_wrap_list(p.get("interventions_used")),
            homework_assignments=_wrap_list(p.get("homework_assignments")),
            next_steps=_wrap_list(p.get("next_steps")),
            next_session=_wrap(p.get("next_session")),
        ),
    )


class MockNoteGenerationService(NoteGenerationService):
    """Mock implementation for testing without LLM credentials.

    Returns deterministic content for every registered note type. SOAP
    reuses the pre-change mock so existing goldens keep passing.
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


def _build_registry_user_prompt(
    definition: NoteTypeDefinition,
    transcript: Transcript,
    patient: Patient,
    session_date: datetime,
) -> str:
    """Compose a prompt describing the registry shape and each field's ``ai_hint``.

    Used for any definition without an explicit ``prompt_builder``.
    """
    lines: list[str] = [
        f"Produce a {definition.label} note.",
        "",
        definition.description,
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
    lines.extend(["", "Transcript:", transcript.content])
    return "\n".join(lines)


def _build_registry_response_schema(definition: NoteTypeDefinition) -> dict[str, Any]:
    """JSON schema dict mirroring the registry shape."""
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
    """Coerce the LLM response into the registry shape, filling missing fields."""
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


__all__ = [
    "SOAP_KEY",
    "GeneratedNote",
    "MockNoteGenerationService",
    "NoteGenerationService",
    "RegistryNoteGenerationService",
]
