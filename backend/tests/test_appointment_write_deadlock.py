# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Two racing overlapping bookings can deadlock; the loser gets the overlap error.

The exclusion constraint on ``appointments`` makes a write wait on any
uncommitted row it overlaps, so two bookings that each insert before the other
commits can wait on each other. Postgres breaks that cycle with
deadlock_detected rather than an exclusion violation. The race itself is
exercised against Postgres in
``tests_integration/database/test_appointment_no_overlap_db.py``, but which of
the two errors Postgres raises there is up to timing, so the translation is
pinned here.
"""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from app.repositories.postgres import appointment as appointment_repo
from app.repositories.postgres.appointment import PostgresAppointmentRepository
from app.scheduling_engine.exceptions import (
    AppointmentConflictError,
    OutsideEventAlreadyBookedError,
)
from sqlalchemy.exc import OperationalError


def _operational_error(sqlstate: str) -> OperationalError:
    orig = Exception("database error")
    orig.diag = SimpleNamespace(sqlstate=sqlstate)  # type: ignore[attr-defined]
    return OperationalError("INSERT INTO appointments ...", {}, orig)


def _repo_whose_flush_raises(exc: Exception) -> PostgresAppointmentRepository:
    session = MagicMock()
    session.begin_nested.return_value = nullcontext()
    session.flush.side_effect = exc
    return PostgresAppointmentRepository(session)


def test_a_deadlocked_booking_is_reported_as_a_conflict() -> None:
    repo = _repo_whose_flush_raises(_operational_error("40P01"))

    with pytest.raises(AppointmentConflictError):
        repo.create_batch([])


def test_any_other_operational_error_is_not_mistaken_for_a_conflict() -> None:
    # 57014 is query_canceled: a real fault, not a lost race.
    repo = _repo_whose_flush_raises(_operational_error("57014"))

    with pytest.raises(OperationalError):
        repo.create_batch([])


def test_a_deadlock_over_an_outside_event_already_booked_names_the_event() -> None:
    with pytest.raises(OutsideEventAlreadyBookedError):
        appointment_repo._translate_deadlock(_operational_error("40P01"), outside_event_taken=True)
