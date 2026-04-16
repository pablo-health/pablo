# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""SOAP note domain models and helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import BaseModel, Field

# --- SOAPSentence: a single AI-generated claim with transcript provenance ---

CONFIDENCE_THRESHOLDS = {
    "verified": 0.97,
    "high": 0.90,
    "medium": 0.60,
    "low": 0.30,
}


@dataclass
class SOAPSentence:
    """A single AI-generated claim with transcript provenance."""

    text: str = ""
    source_segment_ids: list[int] = field(default_factory=list)
    confidence_score: float = 0.0
    confidence_level: str = "unverified"
    possible_match_segment_ids: list[int] = field(default_factory=list)
    signal_used: str = ""


# --- Structured SOAP sub-field dataclasses ---


@dataclass
class SubjectiveNote:
    """Structured sub-fields for the Subjective section."""

    chief_complaint: SOAPSentence = field(default_factory=SOAPSentence)
    mood_affect: SOAPSentence = field(default_factory=SOAPSentence)
    symptoms: list[SOAPSentence] | None = None
    client_narrative: SOAPSentence = field(default_factory=SOAPSentence)


@dataclass
class ObjectiveNote:
    """Structured sub-fields for the Objective section."""

    appearance: SOAPSentence = field(default_factory=SOAPSentence)
    behavior: SOAPSentence = field(default_factory=SOAPSentence)
    speech: SOAPSentence = field(default_factory=SOAPSentence)
    thought_process: SOAPSentence = field(default_factory=SOAPSentence)
    affect_observed: SOAPSentence = field(default_factory=SOAPSentence)


@dataclass
class AssessmentNote:
    """Structured sub-fields for the Assessment section."""

    clinical_impression: SOAPSentence = field(default_factory=SOAPSentence)
    progress: SOAPSentence = field(default_factory=SOAPSentence)
    risk_assessment: SOAPSentence = field(default_factory=SOAPSentence)
    functioning_level: SOAPSentence = field(default_factory=SOAPSentence)


@dataclass
class PlanNote:
    """Structured sub-fields for the Plan section."""

    interventions_used: list[SOAPSentence] | None = None
    homework_assignments: list[SOAPSentence] | None = None
    next_steps: list[SOAPSentence] | None = None
    next_session: SOAPSentence = field(default_factory=SOAPSentence)


# --- Pydantic models for structured sub-fields (API layer) ---


class SOAPSentenceModel(BaseModel):
    """Pydantic model for a single AI-generated claim with transcript provenance."""

    text: str = ""
    source_segment_ids: list[int] = []
    confidence_score: float = 0.0
    confidence_level: str = "unverified"
    possible_match_segment_ids: list[int] = []
    signal_used: str = ""


class SubjectiveNoteModel(BaseModel):
    """Pydantic model for Subjective sub-fields."""

    chief_complaint: SOAPSentenceModel = Field(default_factory=SOAPSentenceModel)
    mood_affect: SOAPSentenceModel = Field(default_factory=SOAPSentenceModel)
    symptoms: list[SOAPSentenceModel] | None = None
    client_narrative: SOAPSentenceModel = Field(default_factory=SOAPSentenceModel)


class ObjectiveNoteModel(BaseModel):
    """Pydantic model for Objective sub-fields."""

    appearance: SOAPSentenceModel = Field(default_factory=SOAPSentenceModel)
    behavior: SOAPSentenceModel = Field(default_factory=SOAPSentenceModel)
    speech: SOAPSentenceModel = Field(default_factory=SOAPSentenceModel)
    thought_process: SOAPSentenceModel = Field(default_factory=SOAPSentenceModel)
    affect_observed: SOAPSentenceModel = Field(default_factory=SOAPSentenceModel)


class AssessmentNoteModel(BaseModel):
    """Pydantic model for Assessment sub-fields."""

    clinical_impression: SOAPSentenceModel = Field(default_factory=SOAPSentenceModel)
    progress: SOAPSentenceModel = Field(default_factory=SOAPSentenceModel)
    risk_assessment: SOAPSentenceModel = Field(default_factory=SOAPSentenceModel)
    functioning_level: SOAPSentenceModel = Field(default_factory=SOAPSentenceModel)


class PlanNoteModel(BaseModel):
    """Pydantic model for Plan sub-fields."""

    interventions_used: list[SOAPSentenceModel] | None = None
    homework_assignments: list[SOAPSentenceModel] | None = None
    next_steps: list[SOAPSentenceModel] | None = None
    next_session: SOAPSentenceModel = Field(default_factory=SOAPSentenceModel)


class SOAPNoteModel(BaseModel):
    """Flat narrative SOAP note model for PDF/clipboard."""

    subjective: str
    objective: str
    assessment: str
    plan: str


class StructuredSOAPNoteModel(BaseModel):
    """Full structured SOAP note API model with source references."""

    subjective: SubjectiveNoteModel
    objective: ObjectiveNoteModel
    assessment: AssessmentNoteModel
    plan: PlanNoteModel
    narrative: SOAPNoteModel


# --- Narrative formatting helpers ---


def _format_field(label: str, value: SOAPSentence | str | None) -> str | None:
    """Format a single labeled field, returning None if value is empty."""
    text = value.text if isinstance(value, SOAPSentence) else value
    if not text or not text.strip():
        return None
    return f"**{label}:** {text.strip()}"


def _format_list_field(label: str, items: list[SOAPSentence] | list[str] | None) -> str | None:
    """Format a list field as bullet points, returning None if empty."""
    if not items:
        return None
    texts = [(item.text if isinstance(item, SOAPSentence) else item) for item in items]
    non_empty = [t.strip() for t in texts if t and t.strip()]
    if not non_empty:
        return None
    bullets = "\n".join(f"- {item}" for item in non_empty)
    return f"**{label}:**\n{bullets}"


def _join_parts(parts: list[str | None]) -> str:
    """Join non-None parts with double newlines."""
    return "\n\n".join(p for p in parts if p)


def _to_sentence(value: str | dict[str, Any] | None) -> SOAPSentence:
    """Convert a raw value to SOAPSentence.

    Handles: SOAPSentence dict, plain string, or None.
    """
    if value is None:
        return SOAPSentence()
    if isinstance(value, dict):
        return SOAPSentence(
            text=value.get("text", ""),
            source_segment_ids=value.get("source_segment_ids", []),
            confidence_score=value.get("confidence_score", 0.0),
            confidence_level=value.get("confidence_level", "unverified"),
            possible_match_segment_ids=value.get("possible_match_segment_ids", []),
            signal_used=value.get("signal_used", ""),
        )
    return SOAPSentence(text=value)


def _to_sentence_list(
    items: list[str | dict[str, Any]] | None,
) -> list[SOAPSentence] | None:
    """Convert a raw list to list[SOAPSentence].

    Handles: list of SOAPSentence dicts, list of plain strings, or None.
    """
    if items is None:
        return None
    return [_to_sentence(item) for item in items]


# --- SOAPNote dataclass ---


@dataclass
class SOAPNote:
    """Structured SOAP note — the sole persisted source of truth.

    Each section is a structured dataclass. Narrative strings are derived
    on read via `to_narrative()`.
    """

    subjective: SubjectiveNote = field(default_factory=SubjectiveNote)
    objective: ObjectiveNote = field(default_factory=ObjectiveNote)
    assessment: AssessmentNote = field(default_factory=AssessmentNote)
    plan: PlanNote = field(default_factory=PlanNote)

    def to_narrative(self) -> dict[str, str]:
        """Derive flat narrative strings for display/export."""
        s = self.subjective
        o = self.objective
        a = self.assessment
        p = self.plan

        return {
            "subjective": _join_parts(
                [
                    _format_field("Chief Complaint", s.chief_complaint),
                    _format_field("Mood/Affect", s.mood_affect),
                    _format_list_field("Symptoms", s.symptoms),
                    _format_field("Client Narrative", s.client_narrative),
                ]
            ),
            "objective": _join_parts(
                [
                    _format_field("Appearance", o.appearance),
                    _format_field("Behavior", o.behavior),
                    _format_field("Speech", o.speech),
                    _format_field("Thought Process", o.thought_process),
                    _format_field("Affect Observed", o.affect_observed),
                ]
            ),
            "assessment": _join_parts(
                [
                    _format_field("Clinical Impression", a.clinical_impression),
                    _format_field("Progress", a.progress),
                    _format_field("Risk Assessment", a.risk_assessment),
                    _format_field("Functioning Level", a.functioning_level),
                ]
            ),
            "plan": _join_parts(
                [
                    _format_list_field("Interventions Used", p.interventions_used),
                    _format_list_field("Homework Assignments", p.homework_assignments),
                    _format_list_field("Next Steps", p.next_steps),
                    _format_field("Next Session", p.next_session),
                ]
            ),
        }

    def to_narrative_model(self) -> SOAPNoteModel:
        """Return a SOAPNoteModel with narrative strings."""
        n = self.to_narrative()
        return SOAPNoteModel(
            subjective=n["subjective"],
            objective=n["objective"],
            assessment=n["assessment"],
            plan=n["plan"],
        )

    def to_structured_model(self) -> StructuredSOAPNoteModel:
        """Return a StructuredSOAPNoteModel with source references."""

        def _sentence_model(s: SOAPSentence) -> SOAPSentenceModel:
            return SOAPSentenceModel(
                text=s.text,
                source_segment_ids=s.source_segment_ids,
                confidence_score=s.confidence_score,
                confidence_level=s.confidence_level,
                possible_match_segment_ids=s.possible_match_segment_ids,
                signal_used=s.signal_used,
            )

        def _sentence_list_model(
            items: list[SOAPSentence] | None,
        ) -> list[SOAPSentenceModel] | None:
            if items is None:
                return None
            return [_sentence_model(s) for s in items]

        s, o, a, p = self.subjective, self.objective, self.assessment, self.plan
        return StructuredSOAPNoteModel(
            subjective=SubjectiveNoteModel(
                chief_complaint=_sentence_model(s.chief_complaint),
                mood_affect=_sentence_model(s.mood_affect),
                symptoms=_sentence_list_model(s.symptoms),
                client_narrative=_sentence_model(s.client_narrative),
            ),
            objective=ObjectiveNoteModel(
                appearance=_sentence_model(o.appearance),
                behavior=_sentence_model(o.behavior),
                speech=_sentence_model(o.speech),
                thought_process=_sentence_model(o.thought_process),
                affect_observed=_sentence_model(o.affect_observed),
            ),
            assessment=AssessmentNoteModel(
                clinical_impression=_sentence_model(a.clinical_impression),
                progress=_sentence_model(a.progress),
                risk_assessment=_sentence_model(a.risk_assessment),
                functioning_level=_sentence_model(a.functioning_level),
            ),
            plan=PlanNoteModel(
                interventions_used=_sentence_list_model(p.interventions_used),
                homework_assignments=_sentence_list_model(p.homework_assignments),
                next_steps=_sentence_list_model(p.next_steps),
                next_session=_sentence_model(p.next_session),
            ),
            narrative=self.to_narrative_model(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for persistence (structured format)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SOAPNote:
        """Create SOAPNote from dictionary with legacy compatibility.

        Handles three formats:
        1. SOAPSentence dicts (new): {"text": "...", "source_segment_ids": [...]}
        2. Plain string sub-fields (structured, no source linking)
        3. Plain string sections (legacy flat format)
        """
        raw_s = data.get("subjective", {})
        raw_o = data.get("objective", {})
        raw_a = data.get("assessment", {})
        raw_p = data.get("plan", {})

        # Legacy: section is a plain string
        if isinstance(raw_s, str):
            subjective = SubjectiveNote(client_narrative=_to_sentence(raw_s))
        else:
            subjective = SubjectiveNote(
                chief_complaint=_to_sentence(raw_s.get("chief_complaint", "")),
                mood_affect=_to_sentence(raw_s.get("mood_affect", "")),
                symptoms=_to_sentence_list(raw_s.get("symptoms")),
                client_narrative=_to_sentence(raw_s.get("client_narrative", "")),
            )

        if isinstance(raw_o, str):
            objective = ObjectiveNote(behavior=_to_sentence(raw_o))
        else:
            objective = ObjectiveNote(
                appearance=_to_sentence(raw_o.get("appearance", "")),
                behavior=_to_sentence(raw_o.get("behavior", "")),
                speech=_to_sentence(raw_o.get("speech", "")),
                thought_process=_to_sentence(raw_o.get("thought_process", "")),
                affect_observed=_to_sentence(raw_o.get("affect_observed", "")),
            )

        if isinstance(raw_a, str):
            assessment = AssessmentNote(clinical_impression=_to_sentence(raw_a))
        else:
            assessment = AssessmentNote(
                clinical_impression=_to_sentence(raw_a.get("clinical_impression", "")),
                progress=_to_sentence(raw_a.get("progress", "")),
                risk_assessment=_to_sentence(raw_a.get("risk_assessment", "")),
                functioning_level=_to_sentence(raw_a.get("functioning_level", "")),
            )

        if isinstance(raw_p, str):
            plan = PlanNote(next_session=_to_sentence(raw_p))
        else:
            plan = PlanNote(
                interventions_used=_to_sentence_list(raw_p.get("interventions_used")),
                homework_assignments=_to_sentence_list(raw_p.get("homework_assignments")),
                next_steps=_to_sentence_list(raw_p.get("next_steps")),
                next_session=_to_sentence(raw_p.get("next_session", "")),
            )

        return cls(
            subjective=subjective,
            objective=objective,
            assessment=assessment,
            plan=plan,
        )
