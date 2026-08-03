# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for ``list_overlapping`` against the in-memory appointment repo.

The Postgres implementation shares the same contract and is covered by
``tests_integration/database/test_appointment_overlap_db.py`` — mirroring
the pattern used for the rest of the appointment repository (see
``test_repo_query_batching.py``).

The scenario in ``test_starts_before_window_and_ends_inside_collides`` is
the one ``list_by_range`` cannot answer: it filters on ``start_at`` alone,
so an appointment that begins before the window and ends inside it is
invisible to it. A collision check built on ``list_by_range`` would pass
every other case here and still miss double-bookings against long
appointments.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.scheduling_engine.models.appointment import Appointment
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository

_NOW = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)


def _appt(
    user_id: str = "user-1",
    *,
    start: datetime,
    end: datetime,
    status: str = "confirmed",
) -> Appointment:
    return Appointment(
        id=str(uuid.uuid4()),
        user_id=user_id,
        patient_id="patient-1",
        title="Session",
        start_at=start,
        end_at=end,
        duration_minutes=int((end - start).total_seconds() // 60),
        status=status,
        session_type="individual",
    )


def test_exact_same_start_and_end_collides() -> None:
    repo = InMemoryAppointmentRepository()
    repo.create(_appt(start=_NOW, end=_NOW + timedelta(hours=1)))

    result = repo.list_overlapping("user-1", _NOW, _NOW + timedelta(hours=1))

    assert len(result) == 1


def test_existing_fully_contains_proposed_slot_collides() -> None:
    repo = InMemoryAppointmentRepository()
    repo.create(_appt(start=_NOW - timedelta(hours=1), end=_NOW + timedelta(hours=2)))

    result = repo.list_overlapping("user-1", _NOW, _NOW + timedelta(hours=1))

    assert len(result) == 1


def test_proposed_slot_fully_contains_existing_collides() -> None:
    repo = InMemoryAppointmentRepository()
    repo.create(_appt(start=_NOW + timedelta(minutes=15), end=_NOW + timedelta(minutes=45)))

    result = repo.list_overlapping("user-1", _NOW, _NOW + timedelta(hours=1))

    assert len(result) == 1


def test_existing_starts_before_window_and_ends_inside_collides() -> None:
    """The case ``list_by_range`` misses — it filters on ``start_at`` only,
    so this appointment (started before the window) would never surface."""
    repo = InMemoryAppointmentRepository()
    repo.create(_appt(start=_NOW - timedelta(minutes=30), end=_NOW + timedelta(minutes=30)))

    result = repo.list_overlapping("user-1", _NOW, _NOW + timedelta(hours=1))

    assert len(result) == 1


def test_back_to_back_does_not_collide() -> None:
    repo = InMemoryAppointmentRepository()
    repo.create(_appt(start=_NOW - timedelta(hours=1), end=_NOW))

    result = repo.list_overlapping("user-1", _NOW, _NOW + timedelta(hours=1))

    assert result == []


def test_cancelled_appointment_does_not_collide() -> None:
    repo = InMemoryAppointmentRepository()
    repo.create(_appt(start=_NOW, end=_NOW + timedelta(hours=1), status="cancelled"))

    result = repo.list_overlapping("user-1", _NOW, _NOW + timedelta(hours=1))

    assert result == []


def test_exclude_appointment_id_omits_that_appointment() -> None:
    repo = InMemoryAppointmentRepository()
    moving = _appt(start=_NOW, end=_NOW + timedelta(hours=1))
    repo.create(moving)

    result = repo.list_overlapping(
        "user-1",
        _NOW,
        _NOW + timedelta(hours=1),
        exclude_appointment_id=moving.id,
    )

    assert result == []


def test_different_user_id_not_returned() -> None:
    repo = InMemoryAppointmentRepository()
    repo.create(_appt("user-2", start=_NOW, end=_NOW + timedelta(hours=1)))

    result = repo.list_overlapping("user-1", _NOW, _NOW + timedelta(hours=1))

    assert result == []
