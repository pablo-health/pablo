# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``clients.csv`` and ``appointments.csv``: the flat files another system imports.

Every practice-management importer reads a CSV of demographics and one of
appointment history. Both are written from the export document, so a
per-patient archive and a practice-wide export are the same rows, once or
many times over. RFC 4180 throughout: CRLF line ends, a header row always,
a field quoted when it holds a comma, a quote or a line break, a quote
doubled inside one. UTF-8 with a byte-order mark, which is what spreadsheet
applications read as UTF-8 rather than guessing.

Column names are a contract: once shipped they are not renamed or moved.
Aligning with another system's export later means adding columns.
"""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from ..models.export import PatientExportDocument

CLIENT_COLUMNS: tuple[str, ...] = (
    "client_id",
    "first_name",
    "last_name",
    "date_of_birth",
    "sex",
    "email",
    "phone",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "postal_code",
    "primary_clinician",
    "status",
    "created_at",
    "diagnosis_codes",
)

APPOINTMENT_COLUMNS: tuple[str, ...] = (
    "appointment_id",
    "client_id",
    "start",
    "end",
    "timezone",
    "appointment_type",
    "status",
    "clinician",
    "location",
    "note_type",
    "cpt_codes",
)

#: How several codes share one cell.
CODE_SEPARATOR = ";"


def _write(columns: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    out = io.StringIO()
    out.write("﻿")
    writer = csv.writer(out, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    return out.getvalue()


def _primary_clinician(document: PatientExportDocument) -> str | None:
    """The clinician on the most recent appointment: who the client sees.

    The chart records who may open it, not who is primary, so the schedule
    is the closest thing to an answer the export carries.
    """
    for appointment in reversed(document.appointments):
        if appointment.clinician_name:
            return appointment.clinician_name
    return None


def _diagnosis_codes(document: PatientExportDocument) -> str:
    """Every confirmed ICD-10-CM code, once, in the order it was first assessed."""
    codes = dict.fromkeys(d.icd10_code for d in document.diagnoses if d.icd10_code)
    return CODE_SEPARATOR.join(codes)


def clients_csv(documents: Iterable[PatientExportDocument]) -> str:
    """One row per document, in the order given."""
    return _write(
        CLIENT_COLUMNS,
        (
            (
                d.patient.identifier,
                d.patient.first_name,
                d.patient.last_name,
                d.patient.birth_date,
                d.patient.sex,
                d.patient.email,
                d.patient.phone,
                d.patient.address_line1,
                d.patient.address_line2,
                d.patient.city,
                d.patient.state,
                d.patient.postal_code,
                _primary_clinician(d),
                d.patient.status,
                d.patient.created_at.isoformat(),
                _diagnosis_codes(d),
            )
            for d in documents
        ),
    )


def appointments_csv(documents: Iterable[PatientExportDocument]) -> str:
    """One row per appointment, documents in the order given, each oldest first."""
    return _write(
        APPOINTMENT_COLUMNS,
        (
            (
                a.id,
                d.patient.identifier,
                a.start.isoformat(),
                a.end.isoformat(),
                a.timezone,
                a.appointment_type,
                a.status,
                a.clinician_name,
                a.place_of_service,
                a.note_type,
                a.service_code or "",
            )
            for d in documents
            for a in d.appointments
        ),
    )
