# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Score a derived note type against a reference by what its fields hold.

Label matching misses a proposal that regroups content: a reference's
"Mental status" fields filed under a proposal's "Objective" share no label,
yet the note holds the same things. Here a judge (a structured model call,
or any function of the same shape) maps each reference field to the
proposal fields that would hold the same content, and the score counts
what found a home.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.settings import get_settings

if TYPE_CHECKING:
    from app.notes.practice_types import PracticeNoteTypeSpec
    from app.services.structured_llm_gateway import StructuredLLMGateway


@dataclass(frozen=True)
class FieldRef:
    section: str
    label: str
    hint: str = ""

    @property
    def name(self) -> str:
        return f"{self.section} / {self.label}"


Judge = Callable[[list[FieldRef], list[FieldRef]], dict[int, list[int]]]
"""(reference fields, proposed fields) -> reference index -> proposed indices."""


def field_refs(spec: PracticeNoteTypeSpec) -> list[FieldRef]:
    return [FieldRef(s.label, f.label, f.ai_hint) for s in spec.sections for f in s.fields]


@dataclass
class MeaningScore:
    field_recall: float
    field_precision: float
    section_recall: float
    """Share of reference sections with at least one field that found a home."""
    matches: list[dict[str, Any]] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    unused_fields: list[str] = field(default_factory=list)


def score_meaning(
    proposed: PracticeNoteTypeSpec, reference: PracticeNoteTypeSpec, judge: Judge
) -> MeaningScore:
    ref, prop = field_refs(reference), field_refs(proposed)
    mapping = {
        r: sorted({p for p in ps if 0 <= p < len(prop)})
        for r, ps in judge(ref, prop).items()
        if 0 <= r < len(ref)
    }
    matched = {r for r, ps in mapping.items() if ps}
    used = {p for ps in mapping.values() for p in ps}
    sections = list(dict.fromkeys(f.section for f in ref))
    covered = {ref[r].section for r in matched}

    def share(part: int, whole: int) -> float:
        return round(part / whole, 2) if whole else 1.0

    return MeaningScore(
        field_recall=share(len(matched), len(ref)),
        field_precision=share(len(used), len(prop)),
        section_recall=share(len(covered), len(sections)),
        matches=[
            {"reference": ref[r].name, "proposed": [prop[p].name for p in mapping[r]]}
            for r in sorted(matched)
        ],
        missing_fields=[f.name for n, f in enumerate(ref) if n not in matched],
        unused_fields=[f.name for n, f in enumerate(prop) if n not in used],
    )


JUDGE_SYSTEM_PROMPT = """\
You compare two clinical note templates. For each REFERENCE field, list the \
PROPOSED fields where a clinician using the proposed template would record \
the same content, judged by meaning, not by wording or section name. A \
reference field may map to several proposed fields, or to none when nothing \
in the proposed template would hold that content. Use the numbers given.
"""

_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "reference": {"type": "integer"},
                    "proposed": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["reference", "proposed"],
            },
        }
    },
    "required": ["matches"],
}


def _listing(prefix: str, fields: list[FieldRef]) -> str:
    return "\n".join(
        f"{prefix}{n}: {f.name}" + (f" — {f.hint}" if f.hint else "") for n, f in enumerate(fields)
    )


def judge_prompt(reference: list[FieldRef], proposed: list[FieldRef]) -> str:
    return (
        f"# REFERENCE fields\n{_listing('R', reference)}\n\n"
        f"# PROPOSED fields\n{_listing('P', proposed)}\n\n"
        "Return one entry per reference field: its R number as `reference` and "
        "the P numbers as `proposed` (an empty list when none)."
    )


def parse_judgement(data: dict[str, Any]) -> dict[int, list[int]]:
    mapping: dict[int, list[int]] = {}
    for entry in data.get("matches") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("reference"), int):
            continue
        proposed = [p for p in entry.get("proposed") or [] if isinstance(p, int)]
        mapping.setdefault(entry["reference"], []).extend(proposed)
    return mapping


def model_judge(gateway: StructuredLLMGateway, model: str | None = None) -> Judge:
    """A judge backed by one structured model call."""

    def judge(reference: list[FieldRef], proposed: list[FieldRef]) -> dict[int, list[int]]:
        settings = get_settings()
        completion = gateway.complete_structured(
            model=model or settings.ai_model,
            system_prompt=JUDGE_SYSTEM_PROMPT,
            user_prompt=judge_prompt(reference, proposed),
            response_schema=_JUDGE_SCHEMA,
            max_output_tokens=settings.note_max_output_tokens,
            temperature=0.0,
        )
        return parse_judgement(completion.data)

    return judge
