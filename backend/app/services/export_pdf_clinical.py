# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The chart PDF's clinical sections: appointments, outcome measures,
messages, medications and diagnoses, in that order.

Each list has a section even when it is empty, so a reader can tell "none on
the chart" from "not in this copy". Every value that came from a person is
escaped, because ``Paragraph`` parses its text as markup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from xml.sax.saxutils import escape

from reportlab.lib.units import inch
from reportlab.platypus import Flowable, KeepTogether, Paragraph, Spacer, Table, TableStyle

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from reportlab.lib.styles import ParagraphStyle, StyleSheet1

    from ..models.export import (
        Condition,
        ExportAppointment,
        ExportMessageThread,
        MedicationStatement,
        Observation,
    )
    from .export_clinical import ClinicalRecord

_SENDER_LABELS = {"patient": "Client", "clinician": "Clinician", "practice": "Practice"}
_STATUS_LABELS = {"no_show": "No-show", "on_hold": "On hold"}


def _label(value: str) -> str:
    return _STATUS_LABELS.get(value) or value.replace("_", " ").capitalize()


def _yes_no(value: bool | None) -> str:
    return "" if value is None else "Yes" if value else "No"


def _when(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M %Z").strip()


def _detail_table(rows: Sequence[tuple[str, str]], styles: StyleSheet1) -> Table:
    """Label and value, one row each; values wrap rather than run off the page."""
    table = Table(
        [[label, Paragraph(escape(value), styles["BodyText"])] for label, value in rows],
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
    return table


def _item(title: str, rows: Sequence[tuple[str, str]], styles: StyleSheet1) -> Flowable:
    return KeepTogether(
        [
            Paragraph(escape(title), styles["Heading4"]),
            _detail_table([(label, value) for label, value in rows if value], styles),
            Spacer(1, 0.1 * inch),
        ]
    )


def _appointment(appointment: ExportAppointment, styles: StyleSheet1) -> Flowable:
    return _item(
        f"{_when(appointment.start)} - {appointment.appointment_type}",
        [
            ("Status", _label(appointment.status)),
            ("Ends", _when(appointment.end)),
            ("Time zone", appointment.timezone),
            ("Clinician", appointment.clinician_name or ""),
            ("Held", "By video" if appointment.telehealth else "In person"),
            ("Place of service", appointment.place_of_service or ""),
            ("Session ID", appointment.session_id or ""),
            ("Appointment ID", appointment.id),
        ],
        styles,
    )


def _observation(measure: Observation, styles: StyleSheet1) -> Flowable:
    score = "" if measure.total_score is None else str(measure.total_score)
    if score and measure.severity:
        score = f"{score} ({measure.severity})"
    items = measure.item_responses or {}
    return _item(
        f"{measure.instrument_name or measure.instrument} - {measure.administered_at.date()}",
        [
            ("Total score", score),
            ("Item responses", ", ".join(f"{k}: {v}" for k, v in sorted(items.items(), key=_n))),
            ("Answered by", _label(measure.source)),
            ("Complete", _yes_no(measure.is_complete)),
            ("Measure ID", measure.id),
        ],
        styles,
    )


def _n(pair: tuple[str, int]) -> tuple[int, str]:
    """Item keys are numbers as text; "10" sorts after "9"."""
    key = pair[0]
    return (int(key), key) if key.isdigit() else (10**6, key)


def _thread(
    thread: ExportMessageThread, filenames: Mapping[str, str], styles: StyleSheet1
) -> list[Flowable]:
    """The conversation as a transcript: who wrote, when, then what they wrote."""
    out: list[Flowable] = [
        Paragraph(
            escape(f"{thread.subject or 'Messages'} - {thread.created_at.date()}"),
            styles["Heading4"],
        ),
        Paragraph(escape(f"{_label(thread.status)}. Thread ID {thread.id}"), styles["Italic"]),
    ]
    for message in thread.messages:
        sender = _SENDER_LABELS.get(message.sender, message.sender)
        out.append(
            Paragraph(f"<b>{escape(sender)}</b>, {_when(message.sent_at)}", styles["Normal"])
        )
        out.extend(
            Paragraph(escape(line), styles["Normal"])
            for line in message.body.splitlines()
            if line.strip()
        )
        for document_id in message.attachment_document_ids:
            name = filenames.get(document_id, document_id)
            out.append(Paragraph(escape(f"Attached: {name}"), styles["Italic"]))
        out.append(Spacer(1, 0.08 * inch))
    out.append(Spacer(1, 0.1 * inch))
    return out


def _medication(medication: MedicationStatement, styles: StyleSheet1) -> Flowable:
    return _item(
        f"{medication.drug_name} {medication.dose}",
        [
            ("Status", _label(medication.status)),
            ("Started", str(medication.started_on or "")),
            ("Stopped", str(medication.stopped_on or "")),
            ("Reason stopped", medication.stop_reason or ""),
            ("Notes", medication.notes or ""),
        ],
        styles,
    )


def _condition(condition: Condition, styles: StyleSheet1) -> Flowable:
    title = " - ".join(p for p in (condition.icd10_code, condition.description) if p)
    return _item(
        f"{title or 'No code chosen'} - {condition.assessed_at.date()}",
        [
            ("Status", _label(condition.status)),
            ("Assessed with", condition.instrument),
            ("Meets criteria", _yes_no(condition.meets_criteria)),
            ("Assessment ID", condition.id),
        ],
        styles,
    )


def clinical_flowables(
    record: ClinicalRecord,
    heading: ParagraphStyle,
    styles: StyleSheet1,
    filenames: Mapping[str, str],
) -> list[Flowable]:
    """One headed section per list. ``filenames`` names message attachments."""
    threads: list[Flowable] = []
    for thread in record.message_threads:
        threads.extend(_thread(thread, filenames, styles))
    sections: list[tuple[str, int, list[Flowable]]] = [
        (
            "Appointments",
            len(record.appointments),
            [_appointment(a, styles) for a in record.appointments],
        ),
        (
            "Outcome measures",
            len(record.outcome_measures),
            [_observation(m, styles) for m in record.outcome_measures],
        ),
        ("Messages", len(record.message_threads), threads),
        (
            "Medications",
            len(record.medications),
            [_medication(m, styles) for m in record.medications],
        ),
        ("Diagnoses", len(record.diagnoses), [_condition(c, styles) for c in record.diagnoses]),
    ]
    out: list[Flowable] = []
    for title, count, body in sections:
        out.append(Paragraph(f"{title} ({count})", heading))
        out.extend(body or [Paragraph("None recorded.", styles["Normal"])])
        out.append(Spacer(1, 0.2 * inch))
    return out
