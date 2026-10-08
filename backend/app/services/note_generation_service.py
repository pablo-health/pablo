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

import dataclasses
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
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
from ..notes.chart_context import ChartContext, render_chart_block
from ..notes.client_present import (
    DICTATED_HEADING,
    TimedSegment,
    segments_from_transcript,
    split_at_boundary,
    split_dictated,
)
from ..notes.diagnoses import DIAGNOSES_KIND_LABEL, DIAGNOSES_SCHEMA, coerce_diagnoses
from ..notes.practice_types import PromptBlocks, render_system_prompt, render_user_prompt
from ..notes.prompts.soap import SOAP_SYSTEM_PROMPT
from ..notes.visit_times import (
    PSYCHOTHERAPY_SECTION_KEY,
    PSYCHOTHERAPY_TIME_FIELD,
    client_present_turns,
    dictated_time_text,
)
from ..settings import get_settings
from .ai_features import AIFeature
from .hedged_structured_llm_gateway import generation_gateway
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
)
from .therapy_labels import (
    LABEL_SCHEMA,
    LABEL_SYSTEM_PROMPT,
    TIME_INSTRUCTIONS,
    TIME_KEY,
    TIME_SCHEMA,
    label_turns,
    propose,
    stated_time,
)

logger = logging.getLogger(__name__)

#: One structured call: ``(system_prompt, user_prompt, response_schema)`` to the reply.
CompleteStructured = Callable[[str, str, dict[str, Any]], dict[str, Any]]


class TransientNoteGenerationError(Exception):
    """LLM note generation failed for a transient, retryable reason.

    Distinguishes a provider rate-limit / resource-exhausted / temporary
    outage (worth retrying — the same transcript would likely succeed later)
    from a deterministic failure (bad prompt, invalid JSON) that never will.
    The caller uses this to let Cloud Tasks retry instead of marking the
    session permanently ``failed``.
    """


# Substrings that mark a transient provider failure anywhere in the error
# chain. The gateway flattens the provider exception into a RuntimeError but
# chains the original via ``from exc``, so the raw 429/RESOURCE_EXHAUSTED text
# survives on ``__cause__``.
_TRANSIENT_LLM_MARKERS = (
    "429",
    "RESOURCE_EXHAUSTED",
    "TOO MANY REQUESTS",
    "RATE LIMIT",
    "503",
    "UNAVAILABLE",
)


def _is_transient_llm_error(exc: BaseException) -> bool:
    """True if the error (or anything it chains) looks transient/retryable."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        code = getattr(cur, "code", None) or getattr(cur, "status_code", None)
        if code in (429, 503):
            return True
        if any(marker in str(cur).upper() for marker in _TRANSIENT_LLM_MARKERS):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


SOAP_KEY = "soap"
_DEFAULT_GENERATION_PROMPT_SYSTEM = (
    "You are a clinical documentation assistant. Populate the requested "
    "note structure from the supplied therapy-session transcript. Use "
    "neutral, clinically-appropriate language. If a field cannot be "
    "inferred from the transcript, return an empty string (or empty list "
    "for list-shaped fields)."
)
_SOAP_ATTRIBUTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "attributions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "integer"},
                    "segments": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["claim", "segments"],
            },
        }
    },
    "required": ["attributions"],
}
_ATTRIBUTION_SYSTEM_PROMPT = (
    "You are an evidence-attribution assistant. Map each "
    "claim number to the transcript segment ids (the "
    "numbers after S in [Sn]) that support it. Return "
    "ONLY a JSON object."
)
"""Structured schema for Call-2. A map of arbitrary claim-number keys →
segment-id arrays can't be constrained by the SDK's controlled-generation
schema, and a bare ``{"type": "object"}`` makes the model return ``{}`` on a
long transcript (silent all-fail). So the response is an explicit list of
``{claim, segments}`` objects — a shape the schema can pin down —
which :func:`parse_attribution_response` normalizes back into the map."""


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
    #: Version of a practice-defined type the content was generated from.
    note_type_version: int | None = None
    #: The psychotherapy time the clinician dictated and the proposed turn
    #: labels, for a type with a psychotherapy section (see
    #: :mod:`app.services.therapy_labels`).
    psychotherapy_proposal: dict[str, Any] | None = None


class RestrictedNoteGenerationError(ValueError):
    """A restricted note type was asked to be generated.

    A restricted note (a psychotherapy note) is written by hand. Raised
    before any prompt is built or any model is called, by every
    implementation, so no path that reaches generation can draft one.
    """


def _refuse_restricted(definition: NoteTypeDefinition) -> None:
    if definition.restricted:
        raise RestrictedNoteGenerationError(
            f"Note type {definition.key!r} is written by hand, not generated"
        )


class NoteGenerationService(ABC):
    """Abstract interface for note generation across all note types."""

    @abstractmethod
    def generate_note(
        self,
        note_type: str,
        transcript: Transcript,
        patient: Patient,
        session_date: datetime,
        inputs: Mapping[str, str] | None = None,
        definition: NoteTypeDefinition | None = None,
        client_present_end_seconds: float | None = None,
        chart: ChartContext | None = None,
        current_note: Mapping[str, Any] | None = None,
    ) -> GeneratedNote:
        """Generate a note of ``note_type`` from ``transcript``.

        ``inputs`` are the values supplied for the type's declared inputs.
        ``client_present_end_seconds`` is where the client left the recording
        (see :mod:`app.notes.client_present`); what the clinician said after
        it reaches the model as a separate addendum.
        ``definition`` is the type already resolved by a caller that is
        about to release its database connection: a practice type is read
        from the database, and resolving it here would reopen a connection
        and hold it across the model call. ``chart`` is the client's problem
        list and allergy record, read by the caller for the same reason;
        ``None`` drafts without them (a preview has no client).
        ``current_note`` is the note as the clinician has it, when this is a
        redraft: its facts are kept unless something newer changes them.

        Raises:
            KeyError: If ``note_type`` is not registered.
            ValueError: If generation fails.
        """

    def chart_proposal_completion(self) -> CompleteStructured | None:
        """The structured call a draft's chart proposals are asked through.

        The same model and provider as the draft (see
        :mod:`app.chart_proposals.drafting`). ``None`` proposes nothing.
        """
        return None


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
        self._llm_gateway = llm_gateway or generation_gateway(AIFeature.NOTE_GENERATION)
        self._model = model

    def _resolve_model(self) -> str:
        if self._model is not None:
            return self._model
        settings = get_settings()
        return settings.model_for(AIFeature.NOTE_GENERATION, settings.ai_model)

    def generate_note(
        self,
        note_type: str,
        transcript: Transcript,
        patient: Patient,
        session_date: datetime,
        inputs: Mapping[str, str] | None = None,
        definition: NoteTypeDefinition | None = None,
        client_present_end_seconds: float | None = None,
        chart: ChartContext | None = None,
        current_note: Mapping[str, Any] | None = None,
    ) -> GeneratedNote:
        definition = definition or self.registry.get(note_type)
        _refuse_restricted(definition)
        # The recording's own turns; anything dictated after it has no recording times.
        recording, dictated = split_dictated(transcript.content)
        segments = segments_from_transcript(Transcript(format=transcript.format, content=recording))
        asks_start = client_present_end_seconds != 0 and any(
            s.key == PSYCHOTHERAPY_SECTION_KEY for s in definition.sections
        )
        content, time_reply = self._generate_via_registry(
            definition,
            transcript,
            patient,
            session_date,
            inputs or {},
            client_present_end_seconds,
            segments=segments,
            dictated=dictated,
            asks_start=asks_start,
            chart=chart,
            current_note=current_note,
        )
        if note_type == SOAP_KEY:
            soap_note = _coerce_content_to_soap_note(content)
            self._run_source_attribution(soap_note, transcript.content)
            return GeneratedNote(
                note_type=SOAP_KEY,
                content=soap_note.to_dict(),
                soap_note=soap_note,
            )
        proposal = None
        if asks_start:
            said = stated_time(time_reply)
            # The field states the dictated time as rendered from its parts,
            # so what the clinician said is compared by parts, never re-read.
            content[PSYCHOTHERAPY_SECTION_KEY][PSYCHOTHERAPY_TIME_FIELD] = dictated_time_text(said)
            turns = client_present_turns(segments, client_present_end_seconds)
            labels, cue = label_turns(content, turns, self._complete_labels)
            proposal = propose(said, labels, cue)
        return GeneratedNote(
            note_type=note_type,
            content=content,
            note_type_version=definition.version,
            psychotherapy_proposal=proposal,
        )

    def _generate_via_registry(
        self,
        definition: NoteTypeDefinition,
        transcript: Transcript,
        patient: Patient,
        session_date: datetime,
        inputs: Mapping[str, str],
        client_present_end_seconds: float | None = None,
        *,
        segments: Sequence[TimedSegment] = (),
        dictated: str = "",
        asks_start: bool = False,
        chart: ChartContext | None = None,
        current_note: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """The drafted content, and the psychotherapy time the clinician stated."""
        full_definition = definition
        addendum = ""
        if client_present_end_seconds is not None and segments:
            split = split_at_boundary(segments, client_present_end_seconds)
            transcript = Transcript(format="txt", content=split.session_lines or _NO_CLIENT_PRESENT)
            # Dictated later, after the recording: addendum too.
            addendum = "\n\n".join(p for p in (split.addendum_lines, dictated) if p)
            if client_present_end_seconds == 0:
                definition = _without_psychotherapy(definition)

        # The clinician's word for the person seen, which a type's prompts may
        # place as {term}; the chart carries it from whoever reads the note.
        person = chart.person if chart is not None else ChartContext().person
        if definition.system_prompt is not None:
            system_prompt = render_system_prompt(definition.system_prompt, person)
        elif definition.key == SOAP_KEY:
            system_prompt = SOAP_SYSTEM_PROMPT
        else:
            system_prompt = _DEFAULT_GENERATION_PROMPT_SYSTEM

        # Allergies, medications and history go to the types that ask for them
        # (the prescriber's notes, which must state them) and not to the
        # built-in therapy formats, which have no place for them.
        chart_block = (
            render_chart_block(chart, full_chart=definition.full_chart)
            if chart is not None and definition.reads_chart
            else None
        )
        if definition.prompt_builder is not None:
            user_prompt = definition.prompt_builder(definition, transcript, patient, session_date)
            # A hand-tuned prompt has no place for the chart; it goes first.
            if chart_block:
                user_prompt = f"{chart_block}\n\n{user_prompt}"
        elif definition.user_template is not None or definition.inputs:
            user_prompt = render_user_prompt(
                definition,
                transcript,
                session_date,
                inputs,
                PromptBlocks(fields=_fields_block(definition), chart=chart_block, person=person),
            )
        else:
            user_prompt = _build_registry_user_prompt(
                definition, transcript, session_date, chart_block
            )
        if addendum:
            user_prompt = f"{user_prompt}\n\n{_addendum_block(addendum)}"
        if current_note:
            user_prompt = f"{user_prompt}\n\n{_current_note_block(current_note)}"

        schema = _build_registry_response_schema(definition)
        if asks_start:
            user_prompt = f"{user_prompt}\n\n{TIME_INSTRUCTIONS}"
            schema["properties"][TIME_KEY] = TIME_SCHEMA
        completion = self._complete_structured_with_retry(
            note_key=definition.key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=schema,
        )

        # Coerced against what was asked for, then against the whole type, so
        # a section left out of the request comes back present and empty.
        asked = _coerce_registry_response(definition, completion.data)
        stated = completion.data.get(TIME_KEY) if asks_start else None
        return _coerce_registry_response(full_definition, asked), stated

    def _complete_labels(self, prompt: str) -> dict[str, Any]:
        """One structured turn-labeling call; see :func:`.therapy_labels.label_turns`."""
        settings = get_settings()
        # The attribution call's budgets: both map turns, nearly mechanically.
        return self._llm_gateway.complete_structured(
            model=self._resolve_model(),
            system_prompt=LABEL_SYSTEM_PROMPT,
            user_prompt=prompt,
            response_schema=LABEL_SCHEMA,
            max_output_tokens=settings.note_source_attribution_max_output_tokens,
            thinking_budget=settings.note_source_attribution_thinking_budget,
            temperature=0.0,
        ).data

    def chart_proposal_completion(self) -> CompleteStructured:
        """A second call after the draft, budgeted like source attribution."""

        def complete(
            system_prompt: str, user_prompt: str, response_schema: dict[str, Any]
        ) -> dict[str, Any]:
            settings = get_settings()
            return self._llm_gateway.complete_structured(
                model=self._resolve_model(),
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_schema=response_schema,
                max_output_tokens=settings.note_source_attribution_max_output_tokens,
                thinking_budget=settings.note_source_attribution_thinking_budget,
                temperature=0.0,
            ).data

        return complete

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

        With ``ai_model_fallbacks`` configured, each of the (at most two)
        gateway calls below is itself a sequence: the primary, each fallback,
        the primary again, one at a time, inside 300 s (see
        :mod:`.hedged_structured_llm_gateway`). Truncation ends that sequence
        at once so the larger budget is tried here, and a transient failure
        that outlasts every leg still raises
        :class:`TransientNoteGenerationError` for the job queue's retry.
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
                if _is_transient_llm_error(exc):
                    # Retryable (e.g. provider 429). Surface it as such so the
                    # job can be retried instead of failing the session.
                    raise TransientNoteGenerationError(str(exc)) from exc
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
                system_prompt=_ATTRIBUTION_SYSTEM_PROMPT,
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
        except Exception:
            logger.warning(
                "Source attribution (Call 2) failed — SOAP note saved without source links",
                exc_info=True,
            )


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
        inputs: Mapping[str, str] | None = None,  # noqa: ARG002  # mock ignores inputs
        definition: NoteTypeDefinition | None = None,
        client_present_end_seconds: float | None = None,  # noqa: ARG002  # mock ignores it
        chart: ChartContext | None = None,  # noqa: ARG002  # mock ignores the chart
        current_note: Mapping[str, Any] | None = None,  # noqa: ARG002  # mock ignores it
    ) -> GeneratedNote:
        definition = definition or self.registry.get(note_type)
        _refuse_restricted(definition)
        if note_type == SOAP_KEY:
            soap_note = _mock_soap_note(patient)
            return GeneratedNote(
                note_type=SOAP_KEY,
                content=soap_note.to_dict(),
                soap_note=soap_note,
            )
        content = _mock_registry_content(definition, patient)
        return GeneratedNote(
            note_type=note_type, content=content, note_type_version=definition.version
        )


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
            elif f.kind == "diagnoses":
                section_content[f.key] = [{"label": diagnosis, "code": None, "status": None}]
            else:
                section_content[f.key] = (
                    f"Mock {section.label} / {f.label} content for session ({diagnosis})."
                )
        content[section.key] = section_content
    return content


# --- Registry-driven prompt + schema composition ---

_NO_CLIENT_PRESENT = "(The client was not present in this recording.)"

_ADDENDUM_INSTRUCTIONS = (
    "Clinician addendum: dictated by the clinician after the session; the "
    "client was not present. These are the clinician's own statements. Where "
    "the addendum states risk, mental status, a prescription monitoring "
    "check, consent, or a decision and its reasons, put it in the matching "
    'field quoted and marked as the clinician\'s, e.g. Clinician stated: "...". '
    "The addendum is not session time and is not something the client said. "
    'An item covered by neither the session nor the addendum is "Not stated."; '
    "never fill it in."
)


def _addendum_block(addendum_lines: str) -> str:
    return f"{_ADDENDUM_INSTRUCTIONS}\n\n{addendum_lines}"


# A redraft starts from the note the clinician already has. Regenerating it
# from the transcript alone rewrites every field and loses whatever the model
# happens not to repeat; a field-by-field merge can't tell a dictated line
# that adds to a field from one that corrects it. So the model gets the note
# and is told what may change.
_CURRENT_NOTE_INSTRUCTIONS = (
    "Current note: the clinician already has this note, below; this is a "
    "redraft of it, made because something changed since it was drafted. Two "
    "rules. 1. Keep every fact the note states, in its wording, unless the "
    "transcript, the addendum or the entered values now say otherwise; never "
    'drop one. 2. Every line under "' + DICTATED_HEADING + '" must be in the '
    "redraft: where the note does not already say it, add it to the field it "
    "belongs in, next to what that field already says, and change a statement "
    "only where such a line corrects it. A redraft identical to the note below "
    "is wrong whenever such a line is missing from it."
)


def _current_note_block(current_note: Mapping[str, Any]) -> str:
    note = json.dumps(current_note, indent=2, ensure_ascii=False)
    return f"{_CURRENT_NOTE_INSTRUCTIONS}\n\n{note}"


def _without_psychotherapy(definition: NoteTypeDefinition) -> NoteTypeDefinition:
    """The type minus its psychotherapy section: no client, no therapy time."""
    sections = tuple(s for s in definition.sections if s.key != PSYCHOTHERAPY_SECTION_KEY)
    return dataclasses.replace(definition, sections=sections)


def _build_registry_user_prompt(
    definition: NoteTypeDefinition,
    transcript: Transcript,
    session_date: datetime,
    chart_block: str | None,
) -> str:
    """Compose a prompt describing the registry shape and each field's ``ai_hint``.

    Used for any definition without an explicit ``prompt_builder``.
    """
    lines: list[str] = [
        f"Produce a {definition.label} note.",
        "",
        definition.description,
        "",
        _fields_block(definition),
    ]
    lines.extend(
        [
            "",
            f"Session date: {session_date.isoformat().split('T', 1)[0]}",
        ]
    )
    if chart_block:
        lines.extend(["", chart_block])
    lines.extend(["", "Transcript:", transcript.content])
    return "\n".join(lines)


def _fields_block(definition: NoteTypeDefinition) -> str:
    """The field enumeration every generated prompt carries."""
    lines = ["Fields:"]
    for section in definition.sections:
        lines.append(f"- Section '{section.key}' ({section.label}):")
        for f in section.fields:
            hint = f.ai_hint or f.label
            kind_label = {
                "text": "free-form string",
                "list": "list of short strings",
                "diagnoses": DIAGNOSES_KIND_LABEL,
                "structured": "nested object",
            }[f.kind]
            lines.append(f"    * {f.key} ({kind_label}) — {hint}")
    return "\n".join(lines)


def _build_registry_response_schema(definition: NoteTypeDefinition) -> dict[str, Any]:
    """JSON schema dict mirroring the registry shape."""
    sections: dict[str, Any] = {}
    for section in definition.sections:
        fields: dict[str, Any] = {}
        for f in section.fields:
            if f.kind == "list":
                fields[f.key] = {"type": "array", "items": {"type": "string"}}
            elif f.kind == "diagnoses":
                fields[f.key] = DIAGNOSES_SCHEMA
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
            elif f.kind == "diagnoses":
                section_content[f.key] = coerce_diagnoses(raw_value)
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
