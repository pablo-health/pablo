# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The CSV files in an export: the columns in their contracted order, RFC 4180
quoting that survives a comma, a quote and a line break in a name or an
address, and both files in the archive.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, datetime
from unittest.mock import Mock

from app.models import Patient
from app.models.export import (
    Condition,
    ExportAppointment,
    ExportOptions,
    ExportPatient,
    PatientExportDocument,
    Practitioner,
)
from app.services import ExportService
from app.services.export_csv import (
    APPOINTMENT_COLUMNS,
    CLIENT_COLUMNS,
    appointments_csv,
    clients_csv,
)

_T0 = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)


def _appointment(appointment_id: str, day: int, clinician: str | None) -> ExportAppointment:
    return ExportAppointment(
        id=appointment_id,
        start=_T0.replace(day=day),
        end=_T0.replace(day=day, hour=16),
        timezone="America/Chicago",
        appointment_type="Individual therapy",
        status="completed",
        clinician_name=clinician,
        telehealth=False,
        place_of_service="11",
        note_type="soap",
        service_code="90837",
        session_id=None,
    )


def _document(**patient: object) -> PatientExportDocument:
    fields: dict[str, object] = {
        "identifier": "client-1",
        "first_name": "Robin",
        "last_name": "Ash",
        "birth_date": "1980-01-15",
        "sex": "F",
        "email": "robin@example.test",
        "phone": "555-0100",
        "address_line1": "12 Harbor Rd, Apt 3",
        "city": "Chicago",
        "state": "IL",
        "postal_code": "60601",
        "status": "active",
        "created_at": _T0,
        "updated_at": _T0,
    }
    fields.update(patient)
    return PatientExportDocument(
        exported_at=_T0,
        options=ExportOptions(),
        patient=ExportPatient.model_validate(fields),
        practitioner=Practitioner(),
        sessions=[],
        standalone_notes=[],
        documents=[],
        appointments=[
            _appointment("appt-1", 2, "Dana Reyes"),
            _appointment("appt-2", 9, "Sam Okafor"),
        ],
        outcome_measures=[],
        message_threads=[],
        medications=[],
        diagnoses=[
            Condition(
                id=f"dx-{code}",
                icd10_code=code,
                description=None,
                assessed_at=_T0,
                status="confirmed" if code else "unconfirmed",
                instrument="gad",
                meets_criteria=True,
                session_id=None,
            )
            for code in ("F41.1", None, "F33.1", "F41.1")
        ],
        charges=[],
        coverage=[],
        claims=[],
    )


def _rows(text: str) -> list[dict[str, str]]:
    assert text.startswith("﻿"), "a byte-order mark, for spreadsheet applications"
    assert "\r\n" in text, "CRLF line ends"
    return list(csv.DictReader(io.StringIO(text.removeprefix("﻿"))))


def test_clients_csv_has_the_columns_in_order_and_the_row_as_the_document_says() -> None:
    text = clients_csv([_document()])

    header = text.removeprefix("﻿").split("\r\n", maxsplit=1)[0]
    assert header == ",".join(CLIENT_COLUMNS)
    [row] = _rows(text)
    assert row == {
        "client_id": "client-1",
        "first_name": "Robin",
        "last_name": "Ash",
        "date_of_birth": "1980-01-15",
        "sex": "F",
        "email": "robin@example.test",
        "phone": "555-0100",
        "address_line1": "12 Harbor Rd, Apt 3",
        "address_line2": "",
        "city": "Chicago",
        "state": "IL",
        "postal_code": "60601",
        "primary_clinician": "Sam Okafor",
        "status": "active",
        "created_at": "2026-03-02T15:00:00+00:00",
        "diagnosis_codes": "F41.1;F33.1",
    }


def test_appointments_csv_has_one_row_per_appointment() -> None:
    text = appointments_csv([_document()])

    header = text.removeprefix("﻿").split("\r\n", maxsplit=1)[0]
    assert header == ",".join(APPOINTMENT_COLUMNS)
    rows = _rows(text)
    assert [r["appointment_id"] for r in rows] == ["appt-1", "appt-2"]
    assert rows[0] == {
        "appointment_id": "appt-1",
        "client_id": "client-1",
        "start": "2026-03-02T15:00:00+00:00",
        "end": "2026-03-02T16:00:00+00:00",
        "timezone": "America/Chicago",
        "appointment_type": "Individual therapy",
        "status": "completed",
        "clinician": "Dana Reyes",
        "location": "11",
        "note_type": "soap",
        "cpt_codes": "90837",
    }


def test_commas_quotes_and_line_breaks_round_trip() -> None:
    awkward = _document(
        first_name='Robin "Bird"',
        last_name="Ash, Jr.",
        address_line1="12 Harbor Rd\nApt 3",
    )

    text = clients_csv([awkward])

    [row] = _rows(text)
    assert row["first_name"] == 'Robin "Bird"'
    assert row["last_name"] == "Ash, Jr."
    assert row["address_line1"] == "12 Harbor Rd\nApt 3"
    assert '"Robin ""Bird"""' in text, "a quote is doubled inside a quoted field"
    assert '"Ash, Jr."' in text


def test_many_documents_give_many_rows_in_the_order_given() -> None:
    first, second = _document(identifier="a"), _document(identifier="b")

    assert [r["client_id"] for r in _rows(clients_csv([second, first]))] == ["b", "a"]
    assert [r["client_id"] for r in _rows(appointments_csv([second, first]))] == [
        "b",
        "b",
        "a",
        "a",
    ]


def test_empty_lists_still_write_the_header() -> None:
    assert _rows(clients_csv([])) == []
    assert clients_csv([]).removeprefix("﻿") == ",".join(CLIENT_COLUMNS) + "\r\n"


def test_the_archive_carries_both_files_as_csv() -> None:
    patients, sessions, notes = Mock(), Mock(), Mock()
    patients.get.return_value = Patient(
        id="client-1", first_name="Robin", last_name="Ash", created_at=_T0, updated_at=_T0
    )
    sessions.list_by_patient.return_value = []
    notes.list_by_patient.return_value = []

    result = ExportService(patients, sessions, notes).get_patient_export_data(
        "client-1", "user-1", "zip"
    )

    with zipfile.ZipFile(io.BytesIO(result["content"])) as archive:
        clients = archive.read("clients.csv").decode()
        appointments = archive.read("appointments.csv").decode()
        manifest = archive.read("manifest.json").decode()
    [row] = _rows(clients)
    assert (row["client_id"], row["first_name"]) == ("client-1", "Robin")
    assert _rows(appointments) == []
    assert '"path": "clients.csv"' in manifest
    assert '"kind": "csv"' in manifest
