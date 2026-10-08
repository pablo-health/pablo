# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The kinds of chart field a note can propose an update to.

Each family says which field keys it owns, how its fields read in the
proposal prompt, which rules the prompt gives for them, what the chart says
now, and how an accepted proposal is written. A new kind of field is a new
family here and nothing else.

Free-text families are proposed as text in the call's ``proposals`` list. A
family whose changes are actions on structured rows (the medication list)
names its own ``reply_key`` and item schema, and turns each item into a
proposal; the evidence is checked the same way for both.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast, override

from ..chart_history.fields import HISTORY_GROUPS, HISTORY_KEYS, SUBSTANCE_KEYS, is_history_key
from ..medications.schemas import CreateMedicationRequest, UpdateMedicationRequest
from ..notes.chart_context import allergies_line, medication_line
from ..utcnow import utc_now
from .models import DraftedProposal, MedicationChange

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from ..chart_history.service import ChartHistoryService
    from ..medications.service import MedicationService
    from ..models import Patient
    from ..notes.chart_context import ChartContext, ChartMedication
    from ..repositories import PatientRepository
    from .models import Evidence, MedicationAction

ALLERGIES = "allergies"
MEDICATIONS = "medications"

#: Note field keys an older copy of a template uses for a chart field.
CHART_KEY_ALIASES = {"nicotine": "tobacco_nicotine"}

#: Note fields that print a chart field another family owns, by that field's key.
NOTE_FIELD_CHART_KEYS = {"current_medications": MEDICATIONS}


def chart_key_for(note_field_key: str) -> str | None:
    """The history key a note field fills, or ``None`` when it fills none."""
    key = CHART_KEY_ALIASES.get(note_field_key, note_field_key)
    return key if is_history_key(key) else None


class FieldRef(Protocol):
    """A chart field, and for a list field the entry: what a proposal is about."""

    @property
    def field_key(self) -> str:
        """The chart field."""

    @property
    def item_key(self) -> str:
        """The entry within a list field; empty for a free-text field."""

    @property
    def change(self) -> MedicationChange | None:
        """The structured change, for a family whose proposals are actions."""


@dataclass(frozen=True)
class ChartWriters:
    """What an accepted proposal writes through."""

    history: ChartHistoryService
    patients: PatientRepository
    medications: MedicationService | None = None


@dataclass(frozen=True)
class WriteSource:
    """Who accepted a proposal, the note it came from, and the visit's date."""

    user_id: str
    note_id: str
    visit_date: date | None = None


class ChartChangedError(ValueError):
    """The chart no longer has what the proposal was measured against."""


class FieldFamily(ABC):
    itemized: bool = False
    """A list field, whose proposals each name the entry (``item_key``) they are about."""

    editable: bool = True
    """Whether the clinician may rewrite the proposed text before it is written."""

    reply_key: str | None = None
    """A family whose proposals the call returns as structured items in a list of
    their own, under this key, rather than as text in ``proposals``."""

    def reply_item_schema(self) -> dict[str, Any] | None:
        """The JSON schema of one item under ``reply_key``."""
        return None

    def drafted(
        self, item: Mapping[str, Any], evidence: tuple[Evidence, ...], chart: ChartContext
    ) -> DraftedProposal | None:
        """One item under ``reply_key`` as a proposal, ``None`` when it is not one.
        Only called on a family that names a ``reply_key``."""
        raise NotImplementedError

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
            (
                "- A history field's proposed_text is the field's full revised text. Keep "
                + "the existing text, merge the change into it, and never remove a statement: "
                + "when something stopped being true, keep it and say it no longer applies "
                + '(for example "Worked full time at the library until March; laid off, no '
                + 'longer working there."). For an empty field, state what was said.'
            ),
            (
                "- A substance-use field records what the client uses. Propose a change only "
                + "when the client describes a different pattern or amount, or a substance the "
                + 'field does not record. "No change" or "same as before" is not a change.'
            ),
            (
                "- One stated fact can change more than one field. Propose each field it "
                + "changes, citing the same lines."
            ),
            (
                "- The medications the client takes now, and any medication started, stopped "
                + "or changed this visit, are kept on the chart's medication list, not in these "
                + "fields: never propose them to a history field. medication_trials is the "
                + "history of psychiatric medications tried before, as the client recounts it."
            ),
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
            (
                '- Allergies (field_key "allergies") are only ever added to. For an allergy '
                + "stated this visit that the chart does not list, put the substance in entry "
                + "and the reaction as stated in proposed_text, whatever the chart says, NKDA "
                + "included. For something said about an allergy the chart lists (that it was "
                + "a mistake, or that the client has taken it since without a reaction), put "
                + "that allergy's substance in entry and the statement in proposed_text, as a "
                + "note kept with the entry. Never propose removing an allergy or saying the "
                + "client is not allergic."
            ),
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


_ACTION_VERBS: dict[str, str] = {
    "start": "Start",
    "stop": "Stop",
    "change": "Change",
    "add": "Add",
}


def _optional(value: Any) -> str | None:
    text = str(value).strip() if isinstance(value, str) else ""
    return text or None


def _listed(chart: ChartContext, drug_name: str) -> ChartMedication | None:
    name = drug_name.strip().lower()
    return next((m for m in chart.medications if m.name.strip().lower() == name), None)


def _line(name: str, dose: str | None, frequency: str | None) -> str:
    named = f"{name} {dose or ''}".strip()
    return f"{named}, {frequency}" if frequency else named


class MedicationFamily(FieldFamily):
    """The medication list. Each proposal is one action on one medication.

    ``start``, ``stop`` and ``change`` are decisions the clinician states in
    the visit; ``add`` is a medication the client takes now that the list
    lacks. Accepting writes through the medication record with the note as
    the source: a start or an add creates an active row, a stop marks the row
    discontinued on the visit's date with the stated reason, a change sets
    the dose or the frequency (each on its own) and keeps the prior values in
    the row's notes. Nothing is ever deleted. The proposal is structured, so
    it is accepted or discarded, not rewritten; the list itself can be edited
    on the client's page.
    """

    itemized = True
    editable = False
    reply_key = "medication_changes"

    def field_keys(self) -> tuple[str, ...]:
        return (MEDICATIONS,)

    def label(self, ref: FieldRef) -> str:
        action = ref.change.action if ref.change is not None else "change"
        return f"Medications: {_ACTION_VERBS[action]} {ref.item_key}"

    def chart_lines(self, chart: ChartContext) -> list[str]:
        if not chart.medications:
            return ["Medication list: none recorded"]
        return [
            "Medication list (what the client takes now):",
            *(f"- {medication_line(m)}" for m in chart.medications),
        ]

    def rules(self) -> list[str]:
        return [
            (
                "- Changes to the medication list go in medication_changes, never in "
                + "proposals, one item per medication. action is start, stop or change only "
                + "for a decision the clinician states in this visit, including anything "
                + "dictated after the client's last line: starting a medication, stopping one, "
                + "or changing its dose or how often it is taken. A medication discussed, "
                + "considered or planned for later is not a change, and neither is a client "
                + "saying they stopped or changed one without a decision from the clinician. "
                + "action is add for a medication the client says they take now that the list "
                + "does not show, such as one another prescriber started. A medication taken "
                + "as the list shows needs nothing."
            ),
            (
                "- For each item give drug_name (for stop or change, as the list names it); "
                + "dose and frequency (how often and when) as stated, and for a change only "
                + "the ones that change; category, psychiatric or other, when it is clear; "
                + "for a stop, the reason the clinician gives, if any, as reason; "
                + "what_changed; and evidence_segment_ids, the lines that state it."
            ),
        ]

    def reply_item_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTION_VERBS)},
                "drug_name": {"type": "string"},
                "dose": {"type": "string"},
                "frequency": {"type": "string"},
                "category": {"type": "string", "enum": ["psychiatric", "other"]},
                "reason": {"type": "string"},
                "what_changed": {"type": "string"},
                "evidence_segment_ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["action", "drug_name", "what_changed", "evidence_segment_ids"],
        }

    def drafted(
        self, item: Mapping[str, Any], evidence: tuple[Evidence, ...], chart: ChartContext
    ) -> DraftedProposal | None:
        action = item.get("action")
        name = _optional(item.get("drug_name"))
        if action not in _ACTION_VERBS or name is None:
            return None
        listed = _listed(chart, name)
        category = item.get("category")
        change = MedicationChange(
            action=cast("MedicationAction", action),
            drug_name=listed.name if listed is not None and action in ("stop", "change") else name,
            dose=_optional(item.get("dose")),
            frequency=_optional(item.get("frequency")),
            category=category if category in ("psychiatric", "other") else None,
            reason=_optional(item.get("reason")) if action == "stop" else None,
        )
        return DraftedProposal(
            field_key=MEDICATIONS,
            item_key=change.drug_name,
            proposed_text=self._proposed_text(change, listed),
            what_changed=_optional(item.get("what_changed")) or "",
            evidence=evidence,
            change=change,
        )

    @staticmethod
    def _proposed_text(change: MedicationChange, listed: ChartMedication | None) -> str:
        if change.action == "stop":
            return f"Stopped: {change.reason}" if change.reason else "Stopped"
        if change.action == "change" and listed is not None:
            return _line(
                listed.name, change.dose or listed.dose, change.frequency or listed.frequency
            )
        return _line(change.drug_name, change.dose, change.frequency)

    @override
    def admits(self, ref: FieldRef, proposed_text: str, chart: ChartContext) -> bool:
        """A start or an add of a medication not on the list; a stop of one that is;
        a change that leaves the listed dose or frequency different."""
        change = ref.change
        if change is None or not ref.item_key.strip():
            return False
        listed = _listed(chart, change.drug_name)
        if change.action in ("start", "add"):
            return listed is None
        if listed is None:
            return False
        if change.action == "stop":
            return True
        return (change.dose or listed.dose) != listed.dose or (
            change.frequency or listed.frequency
        ) != listed.frequency

    def current_text(self, chart: ChartContext, ref: FieldRef) -> str | None:
        listed = _listed(chart, ref.item_key)
        return medication_line(listed) if listed is not None else None

    @override
    def apply(
        self, writers: ChartWriters, patient: Patient, ref: FieldRef, text: str, by: WriteSource
    ) -> None:
        change = ref.change
        service = writers.medications
        if change is None:
            raise ValueError("a medication proposal carries its change")
        if service is None:
            raise RuntimeError("accepting a medication change needs the medication record")
        name = change.drug_name.strip().lower()
        row = next(
            (
                r
                for r in service.list_by_patient(patient.id, by.user_id, status="active")
                if str(r["drug_name"]).strip().lower() == name
            ),
            None,
        )
        visit_date = by.visit_date or utc_now().date()
        if change.action in ("start", "add"):
            if row is not None:
                raise ChartChangedError(f"{change.drug_name} is already on the list")
            request = CreateMedicationRequest(
                drug_name=change.drug_name,
                dose=change.dose or "",
                frequency=change.frequency,
                category=cast("Any", change.category),
                started_at=visit_date if change.action == "start" else None,
            )
            service.create(patient.id, by.user_id, request, source_note_id=by.note_id)
            return
        if row is None:
            raise ChartChangedError(f"{change.drug_name} is no longer on the list")
        if change.action == "stop":
            update = UpdateMedicationRequest(
                status="discontinued", stopped_at=visit_date, stop_reason=change.reason
            )
        else:
            prior = _line("", str(row["dose"]), cast("str | None", row.get("frequency")))
            earlier = cast("str | None", row.get("notes"))
            kept = f"Was {prior} until {visit_date.isoformat()}."
            fields: dict[str, Any] = {"notes": f"{earlier}\n{kept}" if earlier else kept}
            if change.dose:
                fields["dose"] = change.dose
            if change.frequency:
                fields["frequency"] = change.frequency
            update = UpdateMedicationRequest(**fields)
        service.update(str(row["id"]), by.user_id, update, source_note_id=by.note_id)


FAMILIES: tuple[FieldFamily, ...] = (HistoryFamily(), AllergyFamily(), MedicationFamily())


def family_for(field_key: str) -> FieldFamily | None:
    return next((f for f in FAMILIES if f.owns(field_key)), None)


def proposable_keys() -> tuple[str, ...]:
    """Every field key a proposal may name."""
    return tuple(key for family in FAMILIES for key in family.field_keys())
