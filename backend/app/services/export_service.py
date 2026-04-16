# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient data export service for HIPAA Right to Access compliance."""

from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..models import PatientResponse, TherapySession
from ..repositories import PatientRepository, TherapySessionRepository


class ExportService:
    """Service for exporting patient data in various formats."""

    def __init__(
        self, patient_repo: PatientRepository, session_repo: TherapySessionRepository
    ) -> None:
        """Initialize export service with repositories."""
        self.patient_repo = patient_repo
        self.session_repo = session_repo

    def get_patient_export_data(
        self, patient_id: str, user_id: str, export_format: str
    ) -> dict[str, Any]:
        """
        Export complete patient data for HIPAA Right to Access (§ 164.524).

        Args:
            patient_id: Patient ID to export
            user_id: Therapist/clinician user ID (for multi-tenant security)
            export_format: "json" or "pdf"

        Returns:
            Dictionary with export data or error information

        Raises:
            ValueError: If patient not found or format unsupported
        """
        # Get patient data (enforces multi-tenant access control)
        patient = self.patient_repo.get(patient_id, user_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")

        # Get all sessions for this patient
        sessions = self.session_repo.list_by_patient(patient_id, user_id)

        # Convert to response format
        patient_response = PatientResponse.from_patient(patient)
        exported_at = datetime.now(UTC).isoformat()

        if export_format == "json":
            return self._export_as_json(patient_response, sessions, exported_at)
        elif export_format == "pdf":
            return self._export_as_pdf(patient_response, sessions, exported_at)
        else:
            raise ValueError(f"Unsupported export format: {export_format}")

    def _export_as_json(
        self, patient: PatientResponse, sessions: list[TherapySession], exported_at: str
    ) -> dict[str, Any]:
        """Export patient data as JSON."""
        return {
            "patient": patient.model_dump(),
            "sessions": [self._session_to_export_dict(s) for s in sessions],
            "exported_at": exported_at,
            "export_format": "json",
        }

    def _session_to_export_dict(self, session: TherapySession) -> dict[str, Any]:
        """Convert TherapySession dataclass to export dictionary with all relevant fields."""
        return {
            "id": session.id,
            "session_date": session.session_date,
            "session_number": session.session_number,
            "status": session.status,
            "transcript": {
                "format": session.transcript.format,
                "content": session.transcript.content,
            },
            "soap_note": (session.soap_note.to_dict() if session.soap_note else None),
            "soap_note_edited": (
                session.soap_note_edited.to_dict() if session.soap_note_edited else None
            ),
            "final_soap_note": (
                session.final_soap_note.to_dict() if session.final_soap_note else None
            ),
            "was_edited": session.was_edited,
            "created_at": session.created_at,
            "finalized_at": session.finalized_at,
        }

    def _export_as_pdf(
        self, patient: PatientResponse, sessions: list[TherapySession], exported_at: str
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
            ],  # noqa: E501
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
                if session.final_soap_note:
                    narrative = session.final_soap_note.to_narrative()
                    soap_note_label = (
                        "SOAP Note (Edited by Therapist)"
                        if session.was_edited
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

                # Add page break between sessions (except last)
                if idx < len(sessions):
                    story.append(PageBreak())
        else:
            story.append(Paragraph("No therapy sessions recorded.", styles["Normal"]))

        # Build PDF
        doc.build(story)
        pdf_bytes = buffer.getvalue()
        buffer.close()

        return {
            "content": pdf_bytes,
            "content_type": "application/pdf",
            "filename": f"patient_{patient.id}_export_{exported_at.split('T', maxsplit=1)[0]}.pdf",
        }
