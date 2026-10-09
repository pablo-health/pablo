# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Fields printed from the chart, written in code rather than by the model.

A field whose definition names a ``source`` (:mod:`.field_sources`) is
written here: the chart's part is the chart's text exactly as recorded (or
"Not recorded" / "None recorded"), and what the visit said about it is
appended as a mark composed from :class:`Statements`, which the extraction
call (:mod:`.chart_statements`) returns. A model never writes the chart's
text, so it cannot reword it, drop it or invent it.

The marks:

- A history field or the allergies: the chart's text, then
  ``(stated this visit: "...")`` with what was said, when anything was.
- A substance-use field: the chart's baseline, then this visit's screen:
  ``(stated this visit: "...")``, ``(stated this visit: denied)``,
  ``(asked this visit: no change)`` or ``(not asked this visit)``.
- The current medications: the chart's lines untouched, then one item
  ``(stated this visit: "...")`` per medication the client says they take
  that the chart lacks. A stated dose that differs from a chart line is an
  item of its own, so the chart's line is never altered; one that only
  restates a chart line adds nothing.
- The diagnoses: the problem list with its codes, then each diagnosis the
  clinician named that it lacks, with the status "stated this visit" and a
  code only when one was said.
- The place of service: the attestation, from the entered place of service
  and locations; "at home" only when the client said so.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from ..problems.models import ProblemStatus
from .chart_context import ChartContext, allergies_line, medication_line
from .field_sources import (
    ALLERGIES,
    MEDICATIONS,
    PLACE_OF_SERVICE,
    PROBLEMS,
    is_history,
    is_substance,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .diagnoses import StatedDiagnosis
    from .registry import NoteFieldDef, NoteTypeDefinition

NOT_RECORDED = "Not recorded"
NONE_RECORDED = "None recorded"
NOT_STATED = "Not stated."
STATED_DIAGNOSIS = "stated this visit"
RULE_OUT = "rule-out"
ASKED_NO_CHANGE = "(asked this visit: no change)"
NOT_ASKED = "(not asked this visit)"
DENIED = "(stated this visit: denied)"

Screen = Literal["stated", "denied", "asked_no_change"]

_CATEGORY_PREFIX = {"psychiatric": "Psychiatric: ", "other": "Other: "}

TELEHEALTH_ATTESTATION = (
    "Visit conducted by synchronous audio and video telehealth on a HIPAA-compliant "
    "platform. {client}; {provider}. The {person} consented to receive care by telehealth."
)
IN_OFFICE = "In-office visit."


@dataclass(frozen=True)
class Statement:
    """What the visit said about one field, with the transcript lines that say it."""

    field_key: str
    screen: Screen = "stated"
    stated: str = ""
    medication: str = ""
    """For the current medications: the drug's name, as its own field of the reply."""
    dose: str = ""
    """For the current medications: the dose as said, as its own field of the reply."""


@dataclass(frozen=True)
class NamedDiagnosis:
    label: str
    code: str | None = None


@dataclass(frozen=True)
class Statements:
    """Everything the extraction kept: each statement cites lines this visit has."""

    fields: tuple[Statement, ...] = ()
    diagnoses: tuple[NamedDiagnosis, ...] = ()
    client_at_home: bool = False

    def about(self, field_key: str) -> list[Statement]:
        return [s for s in self.fields if s.field_key == field_key]


@dataclass(frozen=True)
class RenderedField:
    section: str
    field: NoteFieldDef
    source: str


def rendered_fields(definition: NoteTypeDefinition) -> list[RenderedField]:
    """The definition's fields that code writes, in note order."""
    return [
        RenderedField(section.key, f, f.source)
        for section in definition.sections
        for f in section.fields
        if f.source is not None
    ]


def without_rendered(definition: NoteTypeDefinition) -> NoteTypeDefinition:
    """The definition the model drafts: every code-written field left out, and a
    section with nothing left for the model left out with them."""
    sections = []
    for section in definition.sections:
        kept = tuple(f for f in section.fields if f.source is None)
        if kept:
            sections.append(replace(section, fields=kept))
    return replace(definition, sections=tuple(sections))


# ---------------------------------------------------------------------------
# The chart's part
# ---------------------------------------------------------------------------


def _history_text(chart: ChartContext, key: str) -> str:
    return next((f.text for f in chart.history if f.key == key), "") or NOT_RECORDED


def medication_lines(chart: ChartContext) -> list[str]:
    """The current list a line per medication, each named by its category when it has one."""
    if not chart.medications:
        return [NONE_RECORDED]
    return [
        _CATEGORY_PREFIX.get(m.category or "", "") + medication_line(m) for m in chart.medications
    ]


def problem_diagnoses(chart: ChartContext) -> list[StatedDiagnosis]:
    return [
        {
            "label": p.label,
            "code": p.icd10_code or None,
            "status": RULE_OUT if p.status == ProblemStatus.RULE_OUT else None,
        }
        for p in chart.problems
    ]


def chart_text(source: str, chart: ChartContext) -> str:
    """A source's chart part as text, as the extraction prompt shows it."""
    if source == ALLERGIES:
        return allergies_line(chart)
    if source == MEDICATIONS:
        return "; ".join(medication_lines(chart))
    if source == PROBLEMS:
        lines = [
            " ".join(p for p in (d["code"], d["label"]) if p) for d in problem_diagnoses(chart)
        ]
        return "; ".join(lines) or "no diagnoses recorded"
    if is_history(source):
        return _history_text(chart, source)
    return ""


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def _quoted(text: str) -> str:
    inner = text.strip().strip("\"“”'").strip().replace('"', "'")
    return f'"{inner}"'


def stated_suffix(statements: list[Statement]) -> str:
    """``(stated this visit: "a"; "b")`` for what was said, or "" when nothing was."""
    quotes: list[str] = []
    for s in statements:
        if s.stated.strip() and (q := _quoted(s.stated)) not in quotes:
            quotes.append(q)
    return f"(stated this visit: {'; '.join(quotes)})" if quotes else ""


def _with_suffix(text: str, suffix: str) -> str:
    return f"{text} {suffix}" if suffix else text


def _screen(statements: list[Statement]) -> str:
    stated = [s for s in statements if s.screen == "stated" and s.stated.strip()]
    if stated:
        return stated_suffix(stated)
    if any(s.screen == "denied" for s in statements):
        return DENIED
    if any(s.screen == "asked_no_change" for s in statements):
        return ASKED_NO_CHANGE
    return NOT_ASKED


def _entered(rendered: RenderedField, inputs: Mapping[str, str]) -> list[Statement]:
    """A value entered for the visit under the field's own key is the clinician's
    statement this visit (the evaluation's allergies input, say)."""
    value = (inputs.get(rendered.field.key) or "").strip()
    if not value or value.lower() == "not provided":
        return []
    return [Statement(field_key=rendered.field.key, stated=value)]


def _diagnoses(chart: ChartContext, named: tuple[NamedDiagnosis, ...]) -> list[StatedDiagnosis]:
    listed = problem_diagnoses(chart)
    codes = {(d["code"] or "").upper() for d in listed if d["code"]}
    labels = {d["label"].strip().lower() for d in listed}
    for n in named:
        code = (n.code or "").strip() or None
        label = n.label.strip()
        if not label or (code and code.upper() in codes) or label.lower() in labels:
            continue
        listed.append({"label": label, "code": code, "status": STATED_DIAGNOSIS})
        labels.add(label.lower())
        if code:
            codes.add(code.upper())
    return listed


def _first_word(text: str) -> str:
    words = text.lower().split()
    return words[0] if words else ""


def _dose_figures(dose: str) -> str:
    """A dose's figures alone: "10 milligrams" and "10 mg" are both "10"."""
    return "".join(c for c in dose if c.isdigit() or c == ".")


def restates_chart_medication(statement: Statement, chart: ChartContext) -> bool:
    """Whether a stated medication is one the chart lists, at the chart's dose.

    Read from the reply's own fields, the drug's name and the dose as said,
    never from the quoted words. A statement that names no drug is kept, and
    so is one naming a listed drug at another dose ("20" against the chart's
    10 mg).
    """
    name = _first_word(statement.medication)
    if not name:
        return False
    said = _dose_figures(statement.dose)
    return any(
        _first_word(m.name) == name and (not said or said == _dose_figures(m.dose))
        for m in chart.medications
    )


def place_of_service(inputs: Mapping[str, str], person: str, *, at_home: bool = False) -> str:
    """The attestation from the entered place of service and locations."""
    place = (inputs.get("place_of_service") or "").strip()
    if not place or place.lower() == "not provided":
        return NOT_STATED
    if not place.lower().startswith("tele"):
        return IN_OFFICE if "office" in place.lower() else f"{place}."
    client_at = (inputs.get("client_location") or "").strip()
    provider_at = (inputs.get("provider_location") or "").strip()
    # An entered location that already says home is printed as given, not
    # "at home in Client's home in ...".
    words = "".join(c if c.isalnum() else " " for c in client_at.lower()).split()
    home = "at home " if at_home and "home" not in words else ""
    if client_at:
        client = f"The {person} was {home}in {client_at}"
    elif at_home:
        client = f"The {person} was at home, location not entered"
    else:
        client = f"The {person}'s location was not entered"
    provider = (
        f"the provider was in {provider_at}"
        if provider_at
        else "the provider's location was not entered"
    )
    return TELEHEALTH_ATTESTATION.format(client=client, provider=provider, person=person)


def compose(
    rendered: RenderedField,
    chart: ChartContext,
    inputs: Mapping[str, str],
    statements: Statements,
) -> Any:
    """One code-written field's content: the chart's part, then what the visit said."""
    source = rendered.source
    if source == PLACE_OF_SERVICE:
        return place_of_service(inputs, chart.person, at_home=statements.client_at_home)
    if source == PROBLEMS:
        return _diagnoses(chart, statements.diagnoses)
    said = statements.about(rendered.field.key) + _entered(rendered, inputs)
    if source == MEDICATIONS:
        lines = medication_lines(chart)
        added = [
            stated_suffix([s])
            for s in said
            if s.stated.strip() and not restates_chart_medication(s, chart)
        ]
        return lines + [a for i, a in enumerate(added) if a not in added[:i]]
    if is_substance(source):
        return f"{_history_text(chart, source)} {_screen(said)}"
    if source == ALLERGIES:
        return _with_suffix(allergies_line(chart), stated_suffix(said))
    return _with_suffix(_history_text(chart, source), stated_suffix(said))


def compose_all(
    definition: NoteTypeDefinition,
    chart: ChartContext,
    inputs: Mapping[str, str],
    statements: Statements,
) -> dict[str, dict[str, Any]]:
    """Every code-written field of ``definition``, by section and key."""
    content: dict[str, dict[str, Any]] = {}
    for rendered in rendered_fields(definition):
        content.setdefault(rendered.section, {})[rendered.field.key] = compose(
            rendered, chart, inputs, statements
        )
    return content
