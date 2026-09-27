# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient data export service for HIPAA Right to Access compliance.

Three formats:

* ``zip``: the export archive (:mod:`.export_archive`): the chart as a PDF,
  the same chart as ``patient.json``, its JSON Schema, the files uploaded to
  the chart, each submitted intake form, and a manifest.
* ``json``: the chart as a bare JSON object, the shape this endpoint has
  always returned.
* ``pdf``: the chart as a PDF on its own (:mod:`.export_pdf`).

What goes into any of them is decided once, by a
:class:`~.record_set.RecordSetSelector` built from the caller's options.
Transcripts and psychotherapy notes are out unless asked for; the reasons
are with the selector. Whatever was applied is echoed in ``options``, so a
consumer can tell an omitted transcript from an empty one, and the route
records it on the audit row.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..models import Patient, PatientResponse
from ..models.export import Practitioner
from ..notes import get_default_registry
from .export_archive import (
    ArchiveFile,
    build_archive,
    build_export_document,
    export_document,
    intake_form_archive_path,
)
from .export_billing import STATEMENT_PATH, SUPERBILL_PATH, BillingRecord, BillingRecordReader
from .export_clinical import ClinicalRecord, ClinicalRecordReader
from .export_pdf import final_content, render_chart_pdf
from .record_set import RecordSetSelector

if TYPE_CHECKING:
    from ..models import Note, PatientDocument, TherapySession, Transcript
    from ..notes import NoteTypeRegistry
    from ..repositories import NotesRepository, PatientRepository, TherapySessionRepository
    from .patient_documents_service import PatientDocumentsService

EXPORT_FORMATS = ("json", "pdf", "zip")

#: Given the patient, the exporting clinician's id and the export time, each
#: submitted intake form on the chart as ``(assignment_id, document bytes)``.
IntakeFormFiles = Callable[[Patient, str, datetime], list[tuple[str, bytes]]]


def _thread_counts(clinical: ClinicalRecord) -> list[tuple[str, int]]:
    """Each exported thread's id and message count, for the route's audit rows."""
    return [(thread.id, len(thread.messages)) for thread in clinical.message_threads]


def _billing_ids(billing: BillingRecord) -> dict[str, Any]:
    """What left of the billing record, by id, for the route's audit rows."""
    return {
        "charge_ids": [charge.id for charge in billing.charges],
        "coverage_ids": [plan.id for plan in billing.coverage],
        "claim_ids": [claim.id for claim in billing.claims],
        "statement": billing.statement is not None,
        "superbill": billing.superbill is not None,
        "balance_cents": billing.balance_cents,
    }


def _billing_files(billing: BillingRecord) -> list[ArchiveFile]:
    files = []
    if billing.statement is not None:
        files.append(ArchiveFile(STATEMENT_PATH, "statement", billing.statement))
    if billing.superbill is not None:
        files.append(ArchiveFile(SUPERBILL_PATH, "superbill", billing.superbill))
    return files


class ExportService:
    """Service for exporting patient data in various formats."""

    def __init__(
        self,
        patient_repo: PatientRepository,
        session_repo: TherapySessionRepository,
        notes_repo: NotesRepository,
        *,
        practitioner: Callable[[str], Practitioner] | None = None,
        note_types: NoteTypeRegistry | None = None,
        documents: PatientDocumentsService | None = None,
        intake_forms: IntakeFormFiles | None = None,
        clinical_record: ClinicalRecordReader | None = None,
        billing_record: BillingRecordReader | None = None,
    ) -> None:
        """``practitioner`` loads who the archive says the record comes from,
        given the exporting clinician's user id. ``documents`` reads the
        files uploaded to the chart, and ``intake_forms`` renders each
        submitted intake form. All three are read only for ``zip``.
        ``clinical_record`` reads appointments, outcome measures, messages,
        medications and diagnoses, and ``billing_record`` the ledger,
        coverage and claims with the statement and superbill, for ``zip``
        and ``pdf``. ``note_types`` labels note fields in the PDF.
        """
        self.patient_repo = patient_repo
        self.session_repo = session_repo
        self.notes_repo = notes_repo
        self._practitioner = practitioner or (lambda _user_id: Practitioner())
        self._note_types = note_types or get_default_registry()
        self._documents = documents
        self._intake_forms = intake_forms or (lambda _patient, _user_id, _at: [])
        self._clinical_record = clinical_record or (lambda _patient_id, _user_id: ClinicalRecord())
        self._billing_record = billing_record or (lambda _patient, _user_id, _at: BillingRecord())

    def get_patient_export_data(
        self,
        patient_id: str,
        user_id: str,
        export_format: str,
        *,
        include_transcripts: bool = False,
        include_psychotherapy_notes: bool = False,
    ) -> dict[str, Any]:
        """
        Export patient data for HIPAA Right to Access (§ 164.524).

        Args:
            patient_id: Patient ID to export
            user_id: Therapist/clinician user ID (for multi-tenant security)
            export_format: "zip", "json" or "pdf"
            include_transcripts: Add each session's transcript
            include_psychotherapy_notes: Add the caller's restricted notes

        Returns:
            The JSON object for ``json``; for ``pdf`` and ``zip``, the file
            as ``content``, ``content_type`` and ``filename``.

        Raises:
            ValueError: If patient not found or format unsupported
        """
        # Get patient data (enforces multi-tenant access control)
        patient = self.patient_repo.get(patient_id, user_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")

        if export_format not in EXPORT_FORMATS:
            raise ValueError(f"Unsupported export format: {export_format}")

        selector = RecordSetSelector(
            include_transcripts=include_transcripts,
            include_psychotherapy_notes=include_psychotherapy_notes,
        )
        sessions = self.session_repo.list_by_patient(patient_id, user_id)
        notes_by_session, standalone_notes = self._select_notes(patient_id, user_id, selector)
        patient_response = PatientResponse.from_patient(patient)
        exported_at = datetime.now(UTC)
        exported_at_iso = exported_at.isoformat()

        if export_format == "json":
            return self._export_as_json(
                patient_response,
                sessions,
                notes_by_session,
                standalone_notes,
                exported_at_iso,
                selector,
            )

        stem = f"patient_{patient.id}_export_{exported_at_iso.split('T', maxsplit=1)[0]}"
        clinical = self._clinical_record(patient_id, user_id)
        billing = self._billing_record(patient, user_id, exported_at)
        if export_format == "pdf":
            return {
                "content": render_chart_pdf(
                    patient_response,
                    sessions,
                    notes_by_session,
                    standalone_notes,
                    exported_at_iso,
                    selector,
                    self._note_types,
                    clinical=clinical,
                    billing=billing,
                ),
                "content_type": "application/pdf",
                "filename": f"{stem}.pdf",
                "message_threads": _thread_counts(clinical),
                **_billing_ids(billing),
            }

        uploads = self._select_documents(patient_id, user_id, selector)
        documents = [export_document(upload, data) for upload, data in uploads]
        intake_forms = self._intake_forms(patient, user_id, exported_at)
        chart_pdf = render_chart_pdf(
            patient_response,
            sessions,
            notes_by_session,
            standalone_notes,
            exported_at_iso,
            selector,
            self._note_types,
            documents,
            clinical,
            billing,
        )
        document = build_export_document(
            patient,
            self._practitioner(user_id),
            sessions,
            notes_by_session,
            standalone_notes,
            documents,
            clinical,
            exported_at,
            selector,
            billing,
        )
        files = [
            *(
                ArchiveFile(entry.archive_path, "document", data)
                for entry, (_, data) in zip(documents, uploads, strict=True)
            ),
            *(
                ArchiveFile(intake_form_archive_path(assignment_id), "intake_form", html)
                for assignment_id, html in intake_forms
            ),
            *_billing_files(billing),
        ]
        return {
            "content": build_archive(document, chart_pdf, files),
            "content_type": "application/zip",
            "filename": f"{stem}.zip",
            # What left beside the chart, for the route's audit rows.
            "documents": [upload for upload, _ in uploads],
            "intake_assignment_ids": [assignment_id for assignment_id, _ in intake_forms],
            "message_threads": _thread_counts(clinical),
            **_billing_ids(billing),
        }

    def _select_documents(
        self, patient_id: str, user_id: str, selector: RecordSetSelector
    ) -> list[tuple[PatientDocument, bytes]]:
        """The uploaded files the selector admits, each with its stored bytes.

        Read through the same repository the chart's document list uses, so
        a file the caller cannot open there is not in the copy either.
        """
        if self._documents is None:
            return []
        return [
            (upload, self._documents.read_file(upload))
            for upload in self._documents.list_for_patient(patient_id, user_id)
            if selector.includes_document(upload)
        ]

    def _select_notes(
        self, patient_id: str, user_id: str, selector: RecordSetSelector
    ) -> tuple[dict[str, Note | None], list[Note]]:
        """The patient's notes the selector admits, by session and on their own.

        One query for every note, indexed by session, rather than a
        per-session round-trip (a 200-session export was 201 queries).
        list_by_patient is newest-first, so the first note seen for a session
        is the one to keep. Notes with no session (a narrative or intake
        written without a recording) are exported on their own. The selector
        runs before either, so an excluded note can never stand in as a
        session's note.
        """
        notes_by_session: dict[str, Note | None] = {}
        standalone_notes: list[Note] = []
        for note in self.notes_repo.list_by_patient(patient_id, user_id):
            if not selector.includes_note(note):
                continue
            if note.session_id is not None:
                notes_by_session.setdefault(note.session_id, note)
            else:
                standalone_notes.append(note)
        return notes_by_session, standalone_notes

    def _export_as_json(
        self,
        patient: PatientResponse,
        sessions: list[TherapySession],
        notes_by_session: dict[str, Note | None],
        standalone_notes: list[Note],
        exported_at: str,
        selector: RecordSetSelector,
    ) -> dict[str, Any]:
        """Export patient data as JSON."""
        return {
            "patient": patient.model_dump(),
            "sessions": [
                self._session_to_export_dict(
                    s, notes_by_session.get(s.id), transcript=selector.transcript_for(s)
                )
                for s in sessions
            ],
            "standalone_notes": [self._note_to_export_dict(n) for n in standalone_notes],
            "exported_at": exported_at,
            "export_format": "json",
            "options": selector.options.model_dump(),
        }

    def _session_to_export_dict(
        self, session: TherapySession, note: Note | None, *, transcript: Transcript | None
    ) -> dict[str, Any]:
        """Convert TherapySession + linked note to export dictionary.

        With no ``transcript`` the ``transcript`` key is absent, not null, so
        an omitted transcript never reads as an empty one.
        """
        final = final_content(note) if note else None
        exported: dict[str, Any] = {
            "id": session.id,
            "session_date": session.session_date,
            "session_number": session.session_number,
            "status": session.status,
        }
        if transcript is not None:
            exported["transcript"] = {"format": transcript.format, "content": transcript.content}
        exported.update(
            {
                "soap_note": note.content if note else None,
                "soap_note_edited": note.content_edited if note else None,
                "final_soap_note": final,
                "was_edited": bool(note and note.content_edited),
                "created_at": session.created_at,
                "finalized_at": note.finalized_at if note else None,
            }
        )
        return exported

    def _note_to_export_dict(self, note: Note) -> dict[str, Any]:
        """Convert a note with no session to an export dictionary."""
        return {
            "id": note.id,
            "note_type": note.note_type,
            "restricted": note.restricted,
            "content": note.content,
            "content_edited": note.content_edited,
            "final_content": final_content(note),
            "was_edited": bool(note.content_edited),
            "created_at": note.created_at,
            "finalized_at": note.finalized_at,
        }
