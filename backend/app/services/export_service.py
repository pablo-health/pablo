# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient data export service for HIPAA Right to Access compliance.

Two parts of the chart are left out unless the caller asks for them:

* **Psychotherapy notes** (``Note.restricted``). The right of access in
  45 CFR 164.524(a)(1)(i) does not reach them, and disclosing them needs a
  separate authorization under 164.508(a)(2). Row security already hides
  other authors' restricted notes, so the option only governs the caller's
  own.
* **Session transcripts.** Not carved out of the right of access, but the
  rawest thing in the chart, so including them in a copy is a deliberate
  choice rather than the default.

Whatever was applied is echoed in the JSON ``options`` object, so a consumer
can tell an omitted transcript from an empty one, and the route records it
on the audit row.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, StyleSheet1, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Flowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..models import Note, PatientResponse, TherapySession
from ..models.session import SOAPNote
from ..repositories import NotesRepository, PatientRepository, TherapySessionRepository


@dataclass(frozen=True)
class ExportOptions:
    """What the caller chose to include beyond the default record copy."""

    include_transcripts: bool = False
    include_psychotherapy_notes: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "include_transcripts": self.include_transcripts,
            "include_psychotherapy_notes": self.include_psychotherapy_notes,
        }


def _final_content(note: Note) -> dict[str, Any] | None:
    return note.content_edited or note.content


def _field_label(key: str) -> str:
    return key.replace("_", " ").capitalize()


def _field_text(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value if item not in (None, ""))
    return "" if value is None else str(value)


def _note_paragraphs(content: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Flatten ``{section: {field: value}}`` note content into labelled text.

    Works for every note type without consulting the registry, so a
    practice-defined type exports as faithfully as a built-in one. A section
    with a single field (narrative's ``note.body``) prints under the section
    label alone.
    """
    if not content:
        return []
    out: list[tuple[str, str]] = []
    for section_key, section in content.items():
        if isinstance(section, dict):
            for field_key, value in section.items():
                text = _field_text(value)
                if not text:
                    continue
                label = _field_label(section_key)
                if len(section) > 1:
                    label = f"{label} - {_field_label(field_key)}"
                out.append((label, text))
        else:
            text = _field_text(section)
            if text:
                out.append((_field_label(section_key), text))
    return out


def _transcript_flowables(session: TherapySession, styles: StyleSheet1) -> list[Flowable]:
    """A "Transcript" heading and the transcript as plain paragraphs.

    Escaped, because ``Paragraph`` parses its text as markup and a
    transcript line can hold an angle bracket or an ampersand.
    """
    out: list[Flowable] = [Paragraph("Transcript", styles["Heading4"])]
    out.extend(
        Paragraph(escape(line), styles["Normal"])
        for line in session.transcript.content.splitlines()
        if line.strip()
    )
    out.append(Spacer(1, 0.1 * inch))
    return out


def _standalone_note_flowables(notes: list[Note], styles: StyleSheet1) -> list[Flowable]:
    """Each note written without a session: its type and date, then its text."""
    out: list[Flowable] = []
    for note in notes:
        written = note.finalized_at or note.created_at
        out.append(
            Paragraph(
                f"{escape(_field_label(note.note_type))} - {written.date()}",
                styles["Heading3"],
            )
        )
        for label, text in _note_paragraphs(_final_content(note)):
            out.append(Paragraph(f"<b>{escape(label)}:</b>", styles["Normal"]))
            out.append(Paragraph(escape(text), styles["Normal"]))
            out.append(Spacer(1, 0.1 * inch))
    return out


def _coerce_soap_note(content: dict[str, Any] | None) -> SOAPNote | None:
    """Build a SOAPNote dataclass from JSONB content, if shaped as SOAP."""
    if not content:
        return None
    try:
        return SOAPNote.from_dict(content)
    except (KeyError, TypeError, AttributeError):
        return None


class ExportService:
    """Service for exporting patient data in various formats."""

    def __init__(
        self,
        patient_repo: PatientRepository,
        session_repo: TherapySessionRepository,
        notes_repo: NotesRepository,
    ) -> None:
        """Initialize export service with repositories."""
        self.patient_repo = patient_repo
        self.session_repo = session_repo
        self.notes_repo = notes_repo

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
            export_format: "json" or "pdf"
            include_transcripts: Add each session's transcript
            include_psychotherapy_notes: Add the caller's restricted notes

        Returns:
            Dictionary with export data or error information

        Raises:
            ValueError: If patient not found or format unsupported
        """
        # Get patient data (enforces multi-tenant access control)
        patient = self.patient_repo.get(patient_id, user_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")

        if export_format not in ("json", "pdf"):
            raise ValueError(f"Unsupported export format: {export_format}")

        options = ExportOptions(
            include_transcripts=include_transcripts,
            include_psychotherapy_notes=include_psychotherapy_notes,
        )

        # Get all sessions for this patient
        sessions = self.session_repo.list_by_patient(patient_id, user_id)
        # Load every note for the patient in one query and index by session,
        # rather than a per-session round-trip (a 200-session export was 201
        # queries). list_by_patient is newest-first, so the first note seen
        # for a session is the one to keep. Notes with no session (a
        # narrative or intake written without a recording) are exported on
        # their own. Restricted notes are dropped before either, so one can
        # never stand in as a session's note.
        notes_by_session: dict[str, Note | None] = {}
        standalone_notes: list[Note] = []
        for note in self.notes_repo.list_by_patient(patient_id, user_id):
            if note.restricted and not options.include_psychotherapy_notes:
                continue
            if note.session_id is not None:
                notes_by_session.setdefault(note.session_id, note)
            else:
                standalone_notes.append(note)

        # Convert to response format
        patient_response = PatientResponse.from_patient(patient)
        exported_at = datetime.now(UTC).isoformat()

        if export_format == "json":
            return self._export_as_json(
                patient_response, sessions, notes_by_session, standalone_notes, exported_at, options
            )
        return self._export_as_pdf(
            patient_response, sessions, notes_by_session, standalone_notes, exported_at, options
        )

    def _export_as_json(
        self,
        patient: PatientResponse,
        sessions: list[TherapySession],
        notes_by_session: dict[str, Note | None],
        standalone_notes: list[Note],
        exported_at: str,
        options: ExportOptions,
    ) -> dict[str, Any]:
        """Export patient data as JSON."""
        return {
            "patient": patient.model_dump(),
            "sessions": [
                self._session_to_export_dict(
                    s,
                    notes_by_session.get(s.id),
                    include_transcript=options.include_transcripts,
                )
                for s in sessions
            ],
            "standalone_notes": [self._note_to_export_dict(n) for n in standalone_notes],
            "exported_at": exported_at,
            "export_format": "json",
            "options": options.as_dict(),
        }

    def _session_to_export_dict(
        self, session: TherapySession, note: Note | None, *, include_transcript: bool
    ) -> dict[str, Any]:
        """Convert TherapySession + linked note to export dictionary.

        With ``include_transcript`` false the ``transcript`` key is absent,
        not null, so an omitted transcript never reads as an empty one.
        """
        final_content = _final_content(note) if note else None
        exported: dict[str, Any] = {
            "id": session.id,
            "session_date": session.session_date,
            "session_number": session.session_number,
            "status": session.status,
        }
        if include_transcript:
            exported["transcript"] = {
                "format": session.transcript.format,
                "content": session.transcript.content,
            }
        exported.update(
            {
                "soap_note": note.content if note else None,
                "soap_note_edited": note.content_edited if note else None,
                "final_soap_note": final_content,
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
            "final_content": _final_content(note),
            "was_edited": bool(note.content_edited),
            "created_at": note.created_at,
            "finalized_at": note.finalized_at,
        }

    def _export_as_pdf(
        self,
        patient: PatientResponse,
        sessions: list[TherapySession],
        notes_by_session: dict[str, Note | None],
        standalone_notes: list[Note],
        exported_at: str,
        options: ExportOptions,
    ) -> dict[str, Any]:
        """Export patient data as PDF."""
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.75 * inch,
            leftMargin=0.75 * inch,
            topMargin=1 * inch,
            bottomMargin=0.75 * inch,
        )

        # Build PDF content
        story = []
        styles = getSampleStyleSheet()

        # Custom styles
        title_style = ParagraphStyle(
            "CustomTitle",
            parent=styles["Heading1"],
            fontSize=18,
            textColor=colors.HexColor("#1a365d"),
            spaceAfter=30,
        )

        heading_style = ParagraphStyle(
            "CustomHeading",
            parent=styles["Heading2"],
            fontSize=14,
            textColor=colors.HexColor("#2c5282"),
            spaceAfter=12,
            spaceBefore=12,
        )

        # Title
        story.append(Paragraph("Patient Medical Records Export", title_style))
        story.append(
            Paragraph(
                f"Exported: {exported_at.replace('T', ' ').split('.')[0]} UTC",
                styles["Normal"],
            )
        )
        story.append(Spacer(1, 0.3 * inch))

        # Patient Demographics
        story.append(Paragraph("Patient Information", heading_style))
        patient_data = [
            ["Field", "Value"],
            ["Patient ID", patient.id],
            ["Name", f"{patient.first_name} {patient.last_name}"],
            ["Date of Birth", patient.date_of_birth or "Not provided"],
            ["Diagnosis", patient.diagnosis or "Not provided"],
            ["Total Sessions", str(patient.session_count)],
            [
                "Last Session",
                str(patient.last_session_date.date()) if patient.last_session_date else "None",
            ],
            ["Record Created", str(patient.created_at.date()) if patient.created_at else "Unknown"],
        ]

        patient_table = Table(patient_data, colWidths=[2 * inch, 4.5 * inch])
        patient_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1a365d")),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 11),
                    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 1), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f7fafc")],
                    ),
                ]
            )
        )
        story.append(patient_table)
        story.append(Spacer(1, 0.4 * inch))

        # Sessions
        if sessions:
            story.append(Paragraph(f"Therapy Sessions ({len(sessions)})", heading_style))
            story.append(Spacer(1, 0.2 * inch))

            for idx, session in enumerate(sessions, 1):
                story.append(
                    Paragraph(
                        f"Session {session.session_number} - {session.session_date}",
                        styles["Heading3"],
                    )
                )

                # Session metadata
                session_meta = [
                    ["Status", session.status],
                    ["Session ID", session.id],
                ]

                meta_table = Table(session_meta, colWidths=[1.5 * inch, 5 * inch])
                meta_table.setStyle(
                    TableStyle(
                        [
                            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                            ("FONTSIZE", (0, 0), (-1, -1), 9),
                            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ]
                    )
                )
                story.append(meta_table)
                story.append(Spacer(1, 0.15 * inch))

                # SOAP Note
                note = notes_by_session.get(session.id)
                final_soap_note = _coerce_soap_note(
                    (note.content_edited or note.content) if note else None
                )
                if final_soap_note:
                    narrative = final_soap_note.to_narrative()
                    was_edited = bool(note and note.content_edited)
                    soap_note_label = (
                        "SOAP Note (Edited by Therapist)"
                        if was_edited
                        else "SOAP Note (AI Generated)"
                    )
                    story.append(Paragraph(soap_note_label, styles["Heading4"]))

                    soap_content = [
                        ("Subjective", narrative["subjective"]),
                        ("Objective", narrative["objective"]),
                        ("Assessment", narrative["assessment"]),
                        ("Plan", narrative["plan"]),
                    ]

                    for section_name, section_text in soap_content:
                        story.append(Paragraph(f"<b>{section_name}:</b>", styles["Normal"]))
                        story.append(Paragraph(section_text, styles["Normal"]))
                        story.append(Spacer(1, 0.1 * inch))

                if options.include_transcripts:
                    story.extend(_transcript_flowables(session, styles))

                # Add page break between sessions (except last)
                if idx < len(sessions):
                    story.append(PageBreak())
        else:
            story.append(Paragraph("No therapy sessions recorded.", styles["Normal"]))

        if standalone_notes:
            story.append(PageBreak())
            story.append(Paragraph("Other notes", heading_style))
            story.extend(_standalone_note_flowables(standalone_notes, styles))

        # Build PDF
        doc.build(story)
        pdf_bytes = buffer.getvalue()
        buffer.close()

        return {
            "content": pdf_bytes,
            "content_type": "application/pdf",
            "filename": f"patient_{patient.id}_export_{exported_at.split('T', maxsplit=1)[0]}.pdf",
        }
