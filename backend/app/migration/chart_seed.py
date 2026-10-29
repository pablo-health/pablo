# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Fill an empty chart from the notes an archive import landed, as proposals.

A client arriving from another records system comes with notes and no chart:
nothing in the export is a history field, a medication list or an allergy
list, so every chart-fed draft would start knowing nothing and the clinician
would retype each chart by hand. After an archive applies, this reads each
client's most recent progress note, and their treatment plan, the way a
single-document import's document is read (``ChartProposalStep``), and
stores what they say about the chart as proposals on that note: pending,
citing the paragraph they came from, decided by the clinician on the note or
on the client's chart. Nothing lands on the chart here.

One call per note read, one or two per client. A psychotherapy note is never
read: it is a restricted class, outside every model path. A note whose call
fails has its run marked failed, with Retry offered on the note, and the
import is not affected: this runs after it has applied, client by client
(``jobs.run_apply``), and never raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..db import release_db_connection
from ..models.note_source import note_source

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..chart_proposals.step import ChartProposalStep
    from ..models import Note, Patient
    from ..services.note_generation_service import NoteGenerationService

#: The note classes read for the chart, the most recent of each.
READ_KINDS = ("progress", "treatment_plan")


@dataclass
class SeedReport:
    """What the pass did, for the run's report and the receipt."""

    #: Clients whose notes were read.
    checked: int = 0
    #: Clients with at least one proposal waiting afterwards.
    clients: int = 0
    #: Notes whose call failed; each is marked on the note, with Retry.
    failed: int = 0
    #: Clients that had no note to read.
    nothing_to_read: int = 0
    patient_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "clients": self.clients,
            "failed": self.failed,
            "nothing_to_read": self.nothing_to_read,
            "patient_ids": list(self.patient_ids),
        }


def note_body(note: Note) -> str:
    """An imported narrative's text, as the importer stored it."""
    content = note.content or {}
    section = content.get("note")
    body = section.get("body") if isinstance(section, dict) else None
    return body.strip() if isinstance(body, str) else ""


def notes_to_read(notes: Iterable[Note]) -> list[Note]:
    """The most recent imported progress note and treatment plan among ``notes``.

    Recency is the visit's date for a progress note, else the day the note was
    written in its system. A psychotherapy note, or any note that was not
    imported, is never among them.
    """
    best: dict[str, tuple[Any, Note]] = {}
    for note in notes:
        source = note_source(note)
        if source is None or source.kind not in READ_KINDS or not note_body(note):
            continue
        when = source.visit_date or source.created_at or note.created_at
        held = best.get(source.kind)
        if held is None or when > held[0]:
            best[source.kind] = (when, note)
    return [best[kind][1] for kind in READ_KINDS if kind in best]


class ChartSeeder:
    """Proposes clients' charts from their imported notes, one client at a time.

    The caller owns each client's transaction. The proposal call itself holds
    no connection, as the draft worker's does not.
    """

    def __init__(
        self, user_id: str, step: ChartProposalStep, generator: NoteGenerationService
    ) -> None:
        self._user_id = user_id
        self._step = step
        self._generator = generator

    def seed(self, patient: Patient, notes: Iterable[Note], report: SeedReport) -> None:
        """Propose the chart from one client's imported notes, onto those notes."""
        to_read = notes_to_read(notes)
        if not to_read:
            report.nothing_to_read += 1
            return
        report.checked += 1
        proposed = 0
        for note in to_read:
            chart = self._step.chart(patient, self._user_id)
            release_db_connection()
            drafted = self._step.seed_from_document(self._generator, chart, note_body(note))
            if drafted is None:
                return  # no model configured: nothing to propose anywhere
            self._step.store_seed(note, chart, drafted)
            if drafted.error_class:
                report.failed += 1
            else:
                proposed += len(drafted.proposals)
        if proposed:
            report.clients += 1
            report.patient_ids.append(patient.id)
