# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which parts of a chart go into a copy of it.

The one place the include rules for a patient export are written. Every
builder that assembles part of an export asks this selector rather than
testing a flag itself, so a rule changes in one place.

Two parts of the chart are left out unless the caller asks for them:

* **Psychotherapy notes** (``Note.restricted``, and documents filed as
  ``psychotherapy_notes``). The right of access in 45 CFR 164.524(a)(1)(i)
  does not reach them, and disclosing them needs a separate authorization
  under 164.508(a)(2). Row security already hides other authors' restricted
  notes, so the option only governs the caller's own.
* **Session transcripts.** Not carved out of the right of access, but the
  rawest thing in the chart, so including one in a copy is a deliberate
  choice rather than the default.

Documents filed as ``therapist_private`` are never included: they are the
clinician's working material, not part of the record a copy is made from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..models.export import ExportOptions
from ..models.patient_document import DocumentCategory

if TYPE_CHECKING:
    from ..models import Note, PatientDocument, TherapySession, Transcript


@dataclass(frozen=True)
class RecordSetSelector:
    include_transcripts: bool = False
    include_psychotherapy_notes: bool = False

    @property
    def options(self) -> ExportOptions:
        """The choices as the export echoes them back."""
        return ExportOptions(
            include_transcripts=self.include_transcripts,
            include_psychotherapy_notes=self.include_psychotherapy_notes,
        )

    def includes_note(self, note: Note) -> bool:
        return not note.restricted or self.include_psychotherapy_notes

    def transcript_for(self, session: TherapySession) -> Transcript | None:
        """The session's transcript when transcripts are included, else ``None``."""
        return session.transcript if self.include_transcripts else None

    def includes_document(self, document: PatientDocument) -> bool:
        if document.category is DocumentCategory.THERAPIST_PRIVATE:
            return False
        if document.category is DocumentCategory.PSYCHOTHERAPY_NOTES:
            return self.include_psychotherapy_notes
        return True
