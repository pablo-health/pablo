# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The kinds of chart field a note can propose an update to.

Each family says which field keys it owns, how its fields read in the
proposal prompt, which rules the prompt gives for them, what the chart says
now, and how an accepted proposal is written. A new kind of field (the
medication list, say) is a new family here and nothing else.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from ..chart_history.fields import HISTORY_GROUPS, HISTORY_KEYS, SUBSTANCE_KEYS, is_history_key
from ..notes.chart_context import allergies_line

if TYPE_CHECKING:
    from ..chart_history.service import ChartHistoryService
    from ..models import Patient
    from ..notes.chart_context import ChartContext
    from ..repositories import PatientRepository

ALLERGIES = "allergies"

#: Note field keys an older copy of a template uses for a chart field.
CHART_KEY_ALIASES = {"nicotine": "tobacco_nicotine"}


def chart_key_for(note_field_key: str) -> str | None:
    """The history key a note field fills, or ``None`` when it fills none."""
    key = CHART_KEY_ALIASES.get(note_field_key, note_field_key)
    return key if is_history_key(key) else None


class FieldRef(Protocol):
    """A chart field, and for a list field the entry: what a proposal is about."""

    @property
    def field_key(self) -> str: ...

    @property
    def item_key(self) -> str: ...


@dataclass(frozen=True)
class ChartWriters:
    """What an accepted proposal writes through."""

    history: ChartHistoryService
    patients: PatientRepository


@dataclass(frozen=True)
class WriteSource:
    """Who accepted a proposal, and the note it came from."""

    user_id: str
    note_id: str


class FieldFamily(ABC):
    itemized: bool = False
    """A list field, whose proposals each name the entry (``item_key``) they are about."""

    @abstractmethod
    def field_keys(self) -> tuple[str, ...]:
        """The field keys a proposal in this family may name."""

    def owns(self, field_key: str) -> bool:
        return field_key in self.field_keys()

    @abstractmethod
    def label(self, ref: FieldRef) -> str:
        """The field as the clinician reads it."""

    @abstractmethod
    def chart_lines(self, chart: ChartContext) -> list[str]:
        """The family's fields as the proposal prompt lists them, empty ones included."""

    @abstractmethod
    def rules(self) -> list[str]:
        """What the proposal prompt says about these fields."""

    @abstractmethod
    def current_text(self, chart: ChartContext, ref: FieldRef) -> str | None:
        """What the chart says now, ``None`` when nothing."""

    @abstractmethod
    def apply(
        self, writers: ChartWriters, patient: Patient, ref: FieldRef, text: str, by: WriteSource
    ) -> None:
        """Write ``text`` to the chart field, with the note as its source."""

    def admits(self, ref: FieldRef, proposed_text: str, chart: ChartContext) -> bool:
        """Whether a drafted proposal can be offered: it names its entry if the field
        is a list, and says something the chart does not already say. An empty one
        would empty the field, and removing is the chart's to do, never a note's."""
        text = proposed_text.strip()
        if not text or bool(ref.item_key.strip()) != self.itemized:
            return False
        return text != (self.current_text(chart, ref) or "").strip()


def _history_label(key: str) -> str:
    for group in HISTORY_GROUPS:
        for field in group.fields:
            if field.key == key:
                return group.label if len(group.fields) == 1 else f"{group.label}: {field.label}"
    raise KeyError(key)


class HistoryFamily(FieldFamily):
    """The chart-history fields: free text, one value each."""

    def field_keys(self) -> tuple[str, ...]:
        return HISTORY_KEYS

    def label(self, ref: FieldRef) -> str:
        return _history_label(ref.field_key)

    def chart_lines(self, chart: ChartContext) -> list[str]:
        recorded = {f.key: f.text for f in chart.history}
        lines = ["History fields:"]
        for key in HISTORY_KEYS:
            if key == SUBSTANCE_KEYS[0]:
                lines.append("Substance use baseline (what the client uses, pattern and amount):")
            first, *rest = (recorded.get(key) or "(empty)").splitlines() or [""]
            lines.append(f"- {key} ({_history_label(key)}): {first}")
            lines.extend(f"    {line}" for line in rest)
        return lines

    def rules(self) -> list[str]:
        return [
            "- A history field's proposed_text is the field's full revised text. Keep "
            "the existing text, merge the change into it, and never remove a statement: "
            "when something stopped being true, keep it and say it no longer applies "
            '(for example "Worked full time at the library until March; laid off, no '
            'longer working there."). For an empty field, state what was said.',
            "- A substance-use field records what the client uses. Propose a change only "
            "when the client describes a different pattern or amount, or a substance the "
            'field does not record. "No change" or "same as before" is not a change.',
            "- One stated fact can change more than one field. Propose each field it "
            "changes, citing the same lines.",
            "- The medications the client takes now, and any medication started, stopped "
            "or changed this visit, are kept on the chart's medication list, not in these "
            "fields: never propose them to a history field. medication_trials is the "
            "history of psychiatric medications tried before, as the client recounts it.",
        ]

    def current_text(self, chart: ChartContext, ref: FieldRef) -> str | None:
        return next((f.text for f in chart.history if f.key == ref.field_key), None)

    def apply(
        self, writers: ChartWriters, patient: Patient, ref: FieldRef, text: str, by: WriteSource
    ) -> None:
        writers.history.set(patient.id, ref.field_key, by.user_id, text, source_note_id=by.note_id)


def _allergy_text(entry: dict[str, Any]) -> str:
    detail = ", ".join(v for k in ("reaction", "severity") if (v := entry.get(k)))
    text = f"{entry['substance']} ({detail})" if detail else str(entry["substance"])
    return f"{text}. Note: {entry['note']}" if entry.get("note") else text


def _same_substance(entry: dict[str, Any], substance: str) -> bool:
    return str(entry.get("substance", "")).strip().lower() == substance.strip().lower()


class AllergyFamily(FieldFamily):
    """The allergy record. Proposals only ever add to it.

    A newly stated allergy is added as an entry, NKDA or not. A statement
    about a recorded allergy (a client saying it was a mistake, or that they
    have taken the drug since) is kept as a note on that entry; the substance,
    reaction and severity stay as they are. Removing an allergy is done on the
    chart, never from a note.
    """

    itemized = True

    def field_keys(self) -> tuple[str, ...]:
        return (ALLERGIES,)

    def label(self, ref: FieldRef) -> str:
        return f"Allergies: {ref.item_key}"

    def chart_lines(self, chart: ChartContext) -> list[str]:
        if chart.allergy_status == "recorded" and chart.allergies:
            return [
                "Allergies (recorded):",
                *(f"- {_allergy_text(entry)}" for entry in chart.allergies),
            ]
        return [f"Allergies: {allergies_line(chart)}"]

    def rules(self) -> list[str]:
        return [
            '- Allergies (field_key "allergies") are only ever added to. For an allergy '
            "stated this visit that the chart does not list, put the substance in entry "
            "and the reaction as stated in proposed_text, whatever the chart says, NKDA "
            "included. For something said about an allergy the chart lists (that it was "
            "a mistake, or that the client has taken it since without a reaction), put "
            "that allergy's substance in entry and the statement in proposed_text, as a "
            "note kept with the entry. Never propose removing an allergy or saying the "
            "client is not allergic.",
        ]

    def current_text(self, chart: ChartContext, ref: FieldRef) -> str | None:
        entry = next((e for e in chart.allergies if _same_substance(e, ref.item_key)), None)
        if entry is not None:
            return _allergy_text(entry)
        return None if chart.allergy_status == "not_recorded" else allergies_line(chart)

    def apply(
        self, writers: ChartWriters, patient: Patient, ref: FieldRef, text: str, by: WriteSource
    ) -> None:
        allergies = [dict(entry) for entry in patient.allergies]
        entry = next((e for e in allergies if _same_substance(e, ref.item_key)), None)
        if entry is None:
            allergies.append(
                {"substance": ref.item_key, "reaction": text, "source_note_id": by.note_id}
            )
        else:
            # The entry's own words stay; what was said joins any earlier note.
            earlier = entry.get("note")
            entry["note"] = f"{earlier}\n{text}" if earlier and text not in earlier else text
            entry["source_note_id"] = by.note_id
        patient.allergies = allergies
        patient.allergy_status = "recorded"
        writers.patients.update(patient)


FAMILIES: tuple[FieldFamily, ...] = (HistoryFamily(), AllergyFamily())


def family_for(field_key: str) -> FieldFamily | None:
    return next((f for f in FAMILIES if f.owns(field_key)), None)


def proposable_keys() -> tuple[str, ...]:
    """Every field key a proposal may name."""
    return tuple(key for family in FAMILIES for key in family.field_keys())
