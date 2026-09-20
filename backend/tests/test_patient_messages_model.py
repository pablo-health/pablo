# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Contract guards for the secure patient messaging tables.

Pins the shape invariants that other code silently depends on: the
denormalized ``patient_id`` the RLS registration keys on, the composite
foreign key that keeps it honest, and the constrained vocabularies. A
future edit that drops one of these would still pass every route test —
the symptom would appear in provisioning, or nowhere at all until two
patients' rows mixed.
"""

from __future__ import annotations

from app.db import PATIENT_READABLE_TABLES, PATIENT_WRITABLE_TABLES
from app.db.models import PatientMessageRow, PatientMessageThreadRow
from app.models.patient_message import (
    MESSAGE_SENDERS,
    SENDER_CLINICIAN,
    SENDER_PATIENT,
    SENDER_PRACTICE,
)

_THREAD_COLUMNS = set(PatientMessageThreadRow.__table__.columns.keys())
_MESSAGE_COLUMNS = set(PatientMessageRow.__table__.columns.keys())


def _constraint_text(table, name: str) -> str:
    for constraint in table.constraints:
        if constraint.name == name:
            return str(constraint.sqltext)
    raise AssertionError(f"{name} is not on {table.name}")


def test_tablenames() -> None:
    assert PatientMessageThreadRow.__tablename__ == "patient_message_threads"
    assert PatientMessageRow.__tablename__ == "patient_messages"


def test_no_practice_id_column() -> None:
    """Tenant scope is implicit in the schema location (house pattern)."""
    assert "practice_id" not in _THREAD_COLUMNS
    assert "practice_id" not in _MESSAGE_COLUMNS


def test_exact_column_sets() -> None:
    assert {
        "id",
        "patient_id",
        "subject",
        "status",
        "closed_at",
        "closed_by",
        "assigned_user_id",
        "clinician_last_read_at",
        "created_at",
        "last_message_at",
    } == _THREAD_COLUMNS
    assert {
        "id",
        "thread_id",
        "patient_id",
        "sender",
        "body",
        "created_at",
        "read_at",
    } == _MESSAGE_COLUMNS


def test_patient_id_is_uuid_not_null_on_both() -> None:
    """The column every per-patient policy keys on, on both tables."""
    for table in (PatientMessageThreadRow, PatientMessageRow):
        col = table.__table__.columns["patient_id"]
        assert col.nullable is False, table.__tablename__
        assert type(col.type).__name__ == "Uuid", table.__tablename__


def test_message_carries_its_own_patient_id_rather_than_joining() -> None:
    """Why there is no bespoke policy branch for ``patient_messages``.

    ``chat_messages`` has neither ``patient_id`` nor ``user_id``, so it
    needs a hand-written parent-join predicate in ``enable_rls_on_schema``
    and a second one in the patient-principal predicate builder. Carrying
    the column here is what avoids both.
    """
    assert "patient_id" in _MESSAGE_COLUMNS


def test_composite_fk_ties_the_denormalized_patient_to_the_thread() -> None:
    """The denormalized column cannot disagree with its thread's owner."""
    fks = [
        fk
        for fk in PatientMessageRow.__table__.foreign_key_constraints
        if fk.name == "fk_patient_messages_thread"
    ]
    assert len(fks) == 1, "the composite FK is gone; the denormalized copy is now unchecked"
    fk = fks[0]
    assert [c.name for c in fk.columns] == ["thread_id", "patient_id"]
    assert [e.column.name for e in fk.elements] == ["id", "patient_id"]
    assert fk.ondelete == "CASCADE"


def test_the_thread_cascades_from_its_patient() -> None:
    """Retention is the chart's retention: no patient, no correspondence."""
    fks = [
        fk
        for fk in PatientMessageThreadRow.__table__.foreign_key_constraints
        if [c.name for c in fk.columns] == ["patient_id"]
    ]
    assert len(fks) == 1, "the thread no longer cascades from patients"
    assert [e.column.table.name for e in fks[0].elements] == ["patients"]
    assert fks[0].ondelete == "CASCADE"


def test_the_lifecycle_columns_are_all_nullable() -> None:
    """A thread that was never closed or assigned has nothing to say about it."""
    for name in ("closed_at", "closed_by", "assigned_user_id", "clinician_last_read_at"):
        assert PatientMessageThreadRow.__table__.columns[name].nullable is True, name


def test_assignment_is_not_a_user_id_column() -> None:
    """``enable_rls_on_schema`` force-policies on a column named ``user_id``.

    Naming the assignee that would have made a routing hint look like an
    ownership claim to the policy builder. It is ``assigned_user_id``, and
    the table's isolation stays the patient arm it already had.
    """
    assert "user_id" not in _THREAD_COLUMNS
    assert "assigned_user_id" in _THREAD_COLUMNS


def test_thread_has_the_unique_constraint_the_fk_targets() -> None:
    """Redundant with the primary key to read, load-bearing to the FK."""
    names = {c.name for c in PatientMessageThreadRow.__table__.constraints}
    assert "uq_patient_message_threads_id_patient" in names


def test_sender_check_accepts_three_values_and_nothing_else() -> None:
    """``practice`` ships now so the after-hours reply needs no migration."""
    sqltext = _constraint_text(PatientMessageRow.__table__, "ck_patient_messages_sender")
    for sender in (SENDER_PATIENT, SENDER_CLINICIAN, SENDER_PRACTICE):
        assert f"'{sender}'" in sqltext
    assert "'system'" not in sqltext
    assert {SENDER_PATIENT, SENDER_CLINICIAN, SENDER_PRACTICE} == MESSAGE_SENDERS


def test_thread_status_check() -> None:
    sqltext = _constraint_text(
        PatientMessageThreadRow.__table__, "ck_patient_message_threads_status"
    )
    assert "'open'" in sqltext
    assert "'closed'" in sqltext


def test_registered_patient_readable_and_writable() -> None:
    """Both arms, on both tables.

    Writable is the half worth pinning: under FORCE ROW LEVEL SECURITY an
    INSERT with no matching policy is refused outright, so a read-only
    registration would leave a patient unable to write to their own
    messages.
    """
    for table in ("patient_message_threads", "patient_messages"):
        assert PATIENT_READABLE_TABLES[table] == "patient_id"
        assert PATIENT_WRITABLE_TABLES[table] == "patient_id"
