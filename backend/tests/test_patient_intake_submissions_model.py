# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Contract guards for the per-tenant patient intake submission table.

Pins the load-bearing *shape* invariants in code so a future edit can't
quietly violate them: schema-implicit tenant isolation (no ``practice_id``),
a ``uuid`` ``patient_id`` (required for the per-tenant ``has_patient_access``
RLS policy to apply), and the exact column set.
"""

from __future__ import annotations

from app.db import PATIENT_READABLE_TABLES, PATIENT_WRITABLE_TABLES
from app.db.models import PatientIntakeSubmissionRow

_COLUMNS = set(PatientIntakeSubmissionRow.__table__.columns.keys())


def test_tablename() -> None:
    assert PatientIntakeSubmissionRow.__tablename__ == "patient_intake_submissions"


def test_id_is_primary_key() -> None:
    pk = [c.name for c in PatientIntakeSubmissionRow.__table__.primary_key.columns]
    assert pk == ["id"]


def test_no_practice_id_column() -> None:
    """Tenant scope is implicit in the schema location (house pattern)."""
    assert "practice_id" not in _COLUMNS


def test_patient_id_is_uuid_not_null() -> None:
    col = PatientIntakeSubmissionRow.__table__.columns["patient_id"]
    assert col.nullable is False
    assert col.type.python_type is str  # Uuid(as_uuid=False)
    assert type(col.type).__name__ == "Uuid"


def test_exact_column_set() -> None:
    expected = {
        "id",
        "patient_id",
        "submitted_at",
        "payload",
        "created_by",
        "created_at",
    }
    assert set(PatientIntakeSubmissionRow.__table__.columns.keys()) == expected


def test_registered_patient_readable_and_writable() -> None:
    """A patient submits their own form and reads it back.

    Both arms are pinned here because the write arm is what an INSERT by a
    patient principal depends on: under FORCE ROW LEVEL SECURITY an INSERT
    with no matching policy is refused outright, so a read-only
    registration would leave the table unwritable by the very principal
    the table exists for.
    """
    assert PATIENT_READABLE_TABLES["patient_intake_submissions"] == "patient_id"
    assert PATIENT_WRITABLE_TABLES["patient_intake_submissions"] == "patient_id"
