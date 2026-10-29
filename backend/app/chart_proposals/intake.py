# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What an accepted intake form proposes for the chart.

A form is a second source of chart updates beside a note, reviewed the same
way: each proposal is accepted, edited or discarded, and nothing reaches the
chart without that. Proposals are made when the clinician accepts the form,
the moment the practice says this is what the client answered (the AI-notes
answer reaches the chart at the same moment, for the same reason). Accepting
the same form's proposals again replaces those still pending.

Two kinds of answer propose, read two ways.

**An answer the form says where to put is mapped, never interpreted.** An
emergency-contact question fills the chart's emergency contact. A written
answer whose question names a chart field (``chart_field`` on its
configuration) fills that field: the preferred name and the pronouns as
written; a history field with the answer, kept after what the field already
says; the medication list a line at a time, each line that begins with (or
names one) known medication proposed as one the client takes now, with the
dose and how often as written. A line that names no medication, or two, is
not guessed at.

**Any other written answer is read only with the client's agreement.** The
answer to "what brings you in" and the form's other written answers are
read by the same proposal call an imported note's document is, citing the
answers it draws on, and only when the client's AI-notes answer on record,
this form's included, is that they consent. Otherwise nothing is proposed
from them: they stay on the form, where the clinician reads them.

Every proposal cites the answers it came from: the question, its position
on the form, and the answer as written, so the chart can open the form at
that answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from ..chart_history.fields import is_history_key
from ..drug_names.names import drug_named, name_key, names_in
from ..intake.items import FreeTextConfig, stored_config, validate_item_config
from .families import (
    EMERGENCY_CONTACT,
    MEDICATIONS,
    PREFERRED_NAME,
    PRONOUNS,
    family_for,
    listed_medication,
)
from .models import ANSWERED_ON_INTAKE, DraftedProposal, Evidence, MedicationChange

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from ..notes.chart_context import ChartContext

#: The question the engine asks in its own words, for a "what brings you in" item
#: carrying no label of its own.
REASON_QUESTION = "What brings you in"

#: Longest pronouns the chart keeps.
PRONOUNS_MAX_LEN = 64

_DOSE = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:mg|mcg|µg|ug|g|ml|mL|units?|iu|IU)\b(?:\s*/\s*\d+(?:\.\d+)?\s*"
    r"(?:mg|mcg|µg|ug|g|ml|mL|units?)\b)?)",
    re.IGNORECASE,
)
_BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s*")


@dataclass(frozen=True)
class Answer:
    """One question on the form and the live answer to it."""

    item_id: str
    position: int
    item_type: str
    question: str
    value: Mapping[str, object]
    chart_field: str | None = None

    def evidence(self, text: str | None = None) -> Evidence:
        return Evidence(
            segment_id=self.position,
            text=text if text is not None else answer_text(self),
            item_id=self.item_id,
            question=self.question,
        )


def answer_text(answer: Answer) -> str:
    """The answer as one line or paragraph of text, "" for one that has none."""
    value = answer.value
    if answer.item_type == "emergency_contact":
        return contact_text(value)
    text = value.get("text")
    return text.strip() if isinstance(text, str) else ""


def contact_text(value: Mapping[str, object]) -> str:
    """An emergency contact as the chart keeps it: "Jordan Lee (sister), 555-0100"."""

    def part(key: str) -> str:
        raw = value.get(key)
        return raw.strip() if isinstance(raw, str) else ""

    name, relationship, phone = part("name"), part("relationship"), part("phone")
    if not name:
        return ""
    named = f"{name} ({relationship})" if relationship else name
    return f"{named}, {phone}" if phone else named


def form_answers(
    items: Iterable[Mapping[str, object]], answers: Mapping[str, Mapping[str, object]]
) -> list[Answer]:
    """Each answered question on the form, in the form's order.

    ``items`` are the form version's question rows and ``answers`` the live
    answers by question id, as the intake assignment service hands them back.
    A question whose configuration no longer parses is passed over.
    """
    found: list[Answer] = []
    for row in items:
        item_id = str(row["id"])
        value = answers.get(item_id)
        if not isinstance(value, dict):
            continue
        item_type = str(row["item_type"])
        try:
            config = validate_item_config(item_type, stored_config(row.get("config")))
        except ValueError:
            continue
        label = row.get("label")
        question = label.strip() if isinstance(label, str) and label.strip() else ""
        if not question and item_type == "reason":
            question = REASON_QUESTION
        position = row.get("position")
        found.append(
            Answer(
                item_id=item_id,
                position=position if isinstance(position, int) else len(found),
                item_type=item_type,
                question=question,
                value=value,
                chart_field=config.chart_field if isinstance(config, FreeTextConfig) else None,
            )
        )
    return found


def _merged(current: str | None, answer: str) -> str | None:
    """A history field's text with the answer kept after what it says; ``None`` when
    the field already says it."""
    existing = (current or "").strip()
    if not existing:
        return answer
    if name_key(answer) in name_key(existing):
        return None
    return f"{existing}\n{answer}"


def medication_line(line: str) -> MedicationChange | None:
    """One line of a "current medications" answer as a medication the client takes,
    or ``None`` when it names no known medication or more than one."""
    text = _BULLET.sub("", line).strip()
    named = list(dict.fromkeys(names_in(text)))
    if len(named) != 1 or drug_named(named[0]) is None:
        return None
    name = named[0]
    words = name_key(name).split()
    leading = name_key(text).split()[: len(words)] == words
    dose_match = _DOSE.search(text)
    dose = re.sub(r"\s+", " ", dose_match.group(1)).strip() if dose_match else None
    frequency = None
    if leading:
        rest = text[dose_match.end() :] if dose_match else _after_words(text, len(words))
        frequency = rest.strip(" ,;:-\u2013\u2014.").strip() or None
    return MedicationChange(
        action="add",
        drug_name=name,
        dose=dose,
        frequency=frequency,
        heard_as=name,
    )


def _after_words(text: str, count: int) -> str:
    """``text`` after its first ``count`` words."""
    spans = list(re.finditer(r"[A-Za-z0-9']+", text))
    return text[spans[count - 1].end() :] if len(spans) >= count else ""


def _medication_lines(text: str) -> list[str]:
    return [part.strip() for line in text.splitlines() for part in line.split(";") if part.strip()]


@dataclass(frozen=True)
class Mapped:
    """The proposals mapped from the answers that say where they go, and the written
    answers left for the proposal call to read, with consent."""

    proposals: list[DraftedProposal]
    to_read: list[Answer]


def mapped_proposals(answers: Sequence[Answer], chart: ChartContext) -> Mapped:
    """The proposals the form's answers make by where the form puts them.

    A medications answer with a line that could not be read as one medication is
    also left for the proposal call, whole; what that call proposes for a
    medication already mapped here is dropped, since this one is first."""
    proposals: list[DraftedProposal] = []
    to_read: list[Answer] = []
    for answer in answers:
        text = answer_text(answer)
        if not text:
            continue
        if answer.item_type == "emergency_contact":
            proposals.append(_profile(EMERGENCY_CONTACT, text, answer))
        elif answer.chart_field in (PREFERRED_NAME, PRONOUNS):
            if answer.chart_field == PRONOUNS and len(text) > PRONOUNS_MAX_LEN:
                continue
            proposals.append(_profile(answer.chart_field, text, answer))
        elif answer.chart_field == MEDICATIONS:
            unread = False
            for line in _medication_lines(text):
                change = medication_line(line)
                if change is None:
                    unread = True
                    continue
                proposals.append(_medication(change, line, chart, answer))
            if unread:
                to_read.append(answer)
        elif answer.chart_field is not None and is_history_key(answer.chart_field):
            family = family_for(answer.chart_field)
            current = family.current_text(chart, _Ref(answer.chart_field)) if family else None
            merged = _merged(current, text)
            if merged is not None:
                proposals.append(
                    DraftedProposal(
                        field_key=answer.chart_field,
                        proposed_text=merged,
                        what_changed=ANSWERED_ON_INTAKE,
                        evidence=(answer.evidence(text),),
                        origin="intake",
                    )
                )
        elif answer.item_type in ("reason", "free_text"):
            to_read.append(answer)
    return Mapped(admitted(proposals, chart), to_read)


@dataclass(frozen=True)
class _Ref:
    field_key: str
    item_key: str = ""
    change: MedicationChange | None = None


def _profile(field_key: str, text: str, answer: Answer) -> DraftedProposal:
    return DraftedProposal(
        field_key=field_key,
        proposed_text=text,
        what_changed=ANSWERED_ON_INTAKE,
        evidence=(answer.evidence(text),),
        origin="intake",
    )


def _medication(
    change: MedicationChange, line: str, chart: ChartContext, answer: Answer
) -> DraftedProposal:
    listed = listed_medication(chart, change.drug_name)
    named = f"{change.drug_name} {change.dose or ''}".strip()
    return DraftedProposal(
        field_key=MEDICATIONS,
        item_key=listed.name if listed is not None else change.drug_name,
        proposed_text=f"{named}, {change.frequency}" if change.frequency else named,
        what_changed="Taken now, as written on the intake",
        evidence=(answer.evidence(line),),
        origin="intake",
        change=change,
    )


def admitted(proposals: Iterable[DraftedProposal], chart: ChartContext) -> list[DraftedProposal]:
    """The proposals their field can take, one per field and entry, first wins."""
    kept: list[DraftedProposal] = []
    seen: set[tuple[str, str]] = set()
    for proposal in proposals:
        family = family_for(proposal.field_key)
        identity = (proposal.field_key, proposal.item_key.strip().lower())
        if family is None or identity in seen:
            continue
        if not family.admits(proposal, proposal.proposed_text, chart):
            continue
        seen.add(identity)
        kept.append(proposal)
    return kept


def read_segments(answers: Sequence[Answer]) -> dict[int, str]:
    """The written answers left for the proposal call, numbered by their position on
    the form, each under the question it answers."""
    return {
        answer.position: f"{answer.question}\n{answer_text(answer)}"
        if answer.question
        else answer_text(answer)
        for answer in answers
        if answer_text(answer)
    }


def pointed(
    proposals: Iterable[DraftedProposal], answers: Sequence[Answer]
) -> list[DraftedProposal]:
    """The proposal call's proposals with each cited segment pointing back at the
    answer it is: the question and the answer as written."""
    by_position = {a.position: a for a in answers}
    out: list[DraftedProposal] = []
    for proposal in proposals:
        evidence = tuple(
            by_position[e.segment_id].evidence()
            for e in proposal.evidence
            if e.segment_id in by_position
        )
        if evidence:
            out.append(replace(proposal, evidence=evidence, origin="intake"))
    return out


__all__ = [
    "REASON_QUESTION",
    "Answer",
    "Mapped",
    "admitted",
    "answer_text",
    "contact_text",
    "form_answers",
    "mapped_proposals",
    "medication_line",
    "pointed",
    "read_segments",
]
