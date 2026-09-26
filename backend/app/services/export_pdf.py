# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The patient export as a PDF: the chart as a document to read or print.

Served on its own for ``format=pdf`` and as ``chart.pdf`` inside the export
archive. What it holds was already chosen by the caller's
:class:`~app.services.record_set.RecordSetSelector`; this module only lays
it out.
"""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING, Any
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

from ..models.session import SOAPNote

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..models import Note, PatientResponse, TherapySession, Transcript
    from ..models.export import ExportDocument
    from ..notes import NoteTypeDefinition, NoteTypeRegistry
    from .record_set import RecordSetSelector


def final_content(note: Note) -> dict[str, Any] | None:
    return note.content_edited or note.content


def _key_label(key: str) -> str:
    return key.replace("_", " ").capitalize()


def _field_text(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value if item not in (None, ""))
    return "" if value is None else str(value)


def _in_definition_order(present: list[str], declared: list[str]) -> list[str]:
    """``present`` keys in the order the note type declares them, then the rest."""
    return [k for k in declared if k in present] + [k for k in present if k not in declared]


def note_paragraphs(
    content: dict[str, Any] | None, definition: NoteTypeDefinition | None
) -> list[tuple[str, str]]:
    """Flatten ``{section: {field: value}}`` note content into labelled text.

    Labels come from the note type's definition, so a field reads as the
    clinician saw it on screen. A section or field the definition does not
    describe (written under an earlier version, or a type no longer
    registered) still prints, labelled from its key, so nothing in the note
    is dropped. A section with a single field (narrative's ``note.body``)
    prints under the section label alone.
    """
    if not content:
        return []
    sections = {s.key: s for s in definition.sections} if definition else {}
    out: list[tuple[str, str]] = []
    for section_key in _in_definition_order(list(content), list(sections)):
        section = content[section_key]
        section_def = sections.get(section_key)
        section_label = section_def.label if section_def else _key_label(section_key)
        if not isinstance(section, dict):
            text = _field_text(section)
            if text:
                out.append((section_label, text))
            continue
        field_labels = {f.key: f.label for f in section_def.fields} if section_def else {}
        for field_key in _in_definition_order(list(section), list(field_labels)):
            text = _field_text(section[field_key])
            if not text:
                continue
            label = section_label
            if len(section) > 1:
                label = f"{label} - {field_labels.get(field_key) or _key_label(field_key)}"
            out.append((label, text))
    return out


def _definition(note_types: NoteTypeRegistry, note: Note) -> NoteTypeDefinition | None:
    try:
        return note_types.get(note.note_type, note.note_type_version)
    except KeyError:
        return None


def _transcript_flowables(transcript: Transcript, styles: StyleSheet1) -> list[Flowable]:
    """A "Transcript" heading and the transcript as plain paragraphs.

    Escaped, because ``Paragraph`` parses its text as markup and a
    transcript line can hold an angle bracket or an ampersand.
    """
    out: list[Flowable] = [Paragraph("Transcript", styles["Heading4"])]
    out.extend(
        Paragraph(escape(line), styles["Normal"])
        for line in transcript.content.splitlines()
        if line.strip()
    )
    out.append(Spacer(1, 0.1 * inch))
    return out


def _standalone_note_flowables(
    notes: list[Note], styles: StyleSheet1, note_types: NoteTypeRegistry
) -> list[Flowable]:
    """Each note written without a session: its type and date, then its text."""
    out: list[Flowable] = []
    for note in notes:
        definition = _definition(note_types, note)
        type_label = definition.label if definition else _key_label(note.note_type)
        written = note.finalized_at or note.created_at
        out.append(Paragraph(f"{escape(type_label)} - {written.date()}", styles["Heading3"]))
        for label, text in note_paragraphs(final_content(note), definition):
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


def _patient_table(patient: PatientResponse) -> Table:
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
    table = Table(patient_data, colWidths=[2 * inch, 4.5 * inch])
    table.setStyle(
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
    return table


def _session_flowables(
    session: TherapySession,
    note: Note | None,
    styles: StyleSheet1,
    selector: RecordSetSelector,
) -> list[Flowable]:
    out: list[Flowable] = [
        Paragraph(f"Session {session.session_number} - {session.session_date}", styles["Heading3"])
    ]
    meta_table = Table(
        [["Status", session.status], ["Session ID", session.id]],
        colWidths=[1.5 * inch, 5 * inch],
    )
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
    out.append(meta_table)
    out.append(Spacer(1, 0.15 * inch))

    final_soap_note = _coerce_soap_note(final_content(note) if note else None)
    if final_soap_note:
        narrative = final_soap_note.to_narrative()
        was_edited = bool(note and note.content_edited)
        soap_note_label = (
            "SOAP Note (Edited by Therapist)" if was_edited else "SOAP Note (AI Generated)"
        )
        out.append(Paragraph(soap_note_label, styles["Heading4"]))
        for section_name, section_key in (
            ("Subjective", "subjective"),
            ("Objective", "objective"),
            ("Assessment", "assessment"),
            ("Plan", "plan"),
        ):
            out.append(Paragraph(f"<b>{section_name}:</b>", styles["Normal"]))
            out.append(Paragraph(narrative[section_key], styles["Normal"]))
            out.append(Spacer(1, 0.1 * inch))

    transcript = selector.transcript_for(session)
    if transcript is not None:
        out.extend(_transcript_flowables(transcript, styles))
    return out


#: How each document category reads in the chart copy.
_CATEGORY_LABELS: dict[str, str] = {
    "chart": "Chart",
    "consent": "Consent",
    "intake_artifact": "Intake",
    "message": "Message attachment",
    "psychotherapy_notes": "Psychotherapy notes",
}


def _document_flowables(documents: Sequence[ExportDocument], styles: StyleSheet1) -> list[Flowable]:
    """Each uploaded file: what it is, and where its copy is in the archive.

    The files themselves are not embedded; the checksum lets a reader match
    this page to the file beside it.
    """
    out: list[Flowable] = []
    for document in documents:
        out.append(Paragraph(escape(document.filename), styles["Heading3"]))
        table = Table(
            [
                ["Category", _CATEGORY_LABELS.get(document.category, document.category)],
                ["Type", document.content_type],
                ["Size", f"{document.bytes:,} bytes"],
                ["Uploaded", str(document.uploaded_at.date())],
                ["Uploaded by", document.uploaded_by.capitalize()],
                ["In this archive", document.archive_path],
                ["SHA-256", document.sha256],
                ["Document ID", document.id],
            ],
            colWidths=[1.5 * inch, 5 * inch],
        )
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        out.append(table)
        out.append(Spacer(1, 0.15 * inch))
    return out


def render_chart_pdf(
    patient: PatientResponse,
    sessions: list[TherapySession],
    notes_by_session: dict[str, Note | None],
    standalone_notes: list[Note],
    exported_at: str,
    selector: RecordSetSelector,
    note_types: NoteTypeRegistry,
    documents: Sequence[ExportDocument] = (),
) -> bytes:
    """The chart as a PDF. ``documents`` lists files carried beside it in an archive."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=1 * inch,
        bottomMargin=0.75 * inch,
    )
    styles = getSampleStyleSheet()
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

    story: list[Flowable] = [
        Paragraph("Patient Medical Records Export", title_style),
        Paragraph(
            f"Exported: {exported_at.replace('T', ' ').split('.')[0]} UTC",
            styles["Normal"],
        ),
        Spacer(1, 0.3 * inch),
        Paragraph("Patient Information", heading_style),
        _patient_table(patient),
        Spacer(1, 0.4 * inch),
    ]

    if sessions:
        story.append(Paragraph(f"Therapy Sessions ({len(sessions)})", heading_style))
        story.append(Spacer(1, 0.2 * inch))
        for idx, session in enumerate(sessions, 1):
            story.extend(
                _session_flowables(session, notes_by_session.get(session.id), styles, selector)
            )
            if idx < len(sessions):
                story.append(PageBreak())
    else:
        story.append(Paragraph("No therapy sessions recorded.", styles["Normal"]))

    if standalone_notes:
        story.append(PageBreak())
        story.append(Paragraph("Other notes", heading_style))
        story.extend(_standalone_note_flowables(standalone_notes, styles, note_types))

    if documents:
        story.append(PageBreak())
        story.append(Paragraph(f"Documents ({len(documents)})", heading_style))
        story.extend(_document_flowables(documents, styles))

    doc.build(story)
    return buffer.getvalue()
