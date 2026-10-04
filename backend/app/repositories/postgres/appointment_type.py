# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL appointment type repository implementation."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ...db.models import AppointmentTypeRow
from ...scheduling_engine.exceptions import AppointmentTypeNameTakenError
from ...scheduling_engine.models.appointment_type import AppointmentType
from ...scheduling_engine.repositories.appointment_type import AppointmentTypeRepository

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session

_NAME_CONSTRAINT = "uq_appointment_types_user_name"


class PostgresAppointmentTypeRepository(AppointmentTypeRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, appointment_type_id: str, user_id: str) -> AppointmentType | None:
        row = self._session.get(AppointmentTypeRow, appointment_type_id)
        if row is None or row.user_id != user_id:
            return None
        return _row_to_appointment_type(row)

    def list_by_user(self, user_id: str) -> list[AppointmentType]:
        rows = (
            self._session.execute(
                select(AppointmentTypeRow)
                .where(AppointmentTypeRow.user_id == user_id)
                .order_by(AppointmentTypeRow.created_at)
            )
            .scalars()
            .all()
        )
        return [_row_to_appointment_type(r) for r in rows]

    def create(self, appointment_type: AppointmentType) -> AppointmentType:
        row = AppointmentTypeRow()
        _appointment_type_to_row(appointment_type, row)
        with self._name_must_be_free(appointment_type.name):
            self._session.add(row)
            self._session.flush()
        return appointment_type

    def update(self, appointment_type: AppointmentType) -> AppointmentType:
        row = self._session.get(AppointmentTypeRow, appointment_type.id)
        with self._name_must_be_free(appointment_type.name):
            if row is None:
                row = AppointmentTypeRow()
                self._session.add(row)
            _appointment_type_to_row(appointment_type, row)
            self._session.flush()
        return appointment_type

    def delete(self, appointment_type_id: str, user_id: str) -> bool:
        row = self._session.get(AppointmentTypeRow, appointment_type_id)
        if row is None or row.user_id != user_id:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    @contextmanager
    def _name_must_be_free(self, name: str) -> Iterator[None]:
        """Turn a name collision into a domain error, undoing only this write.

        A SAVEPOINT rather than a bare flush: rolling back the whole request
        session would throw away whatever else the caller had staged, and a
        repository is the wrong place to make that call. Only the per-user
        name constraint is translated; any other integrity failure is a
        different bug and propagates as itself.
        """
        try:
            with self._session.begin_nested():
                yield
        except IntegrityError as e:
            diag = getattr(e.orig, "diag", None)
            if getattr(diag, "constraint_name", None) != _NAME_CONSTRAINT:
                raise
            raise AppointmentTypeNameTakenError(name) from e


# Mapped in both directions from one list, so a column added to the model
# cannot be silently dropped on read or on write — the previous shape had the
# field list written out twice and would have needed both edited by hand.
_SCHEDULING_FIELDS = (
    "name",
    "default_fee_cents",
    "duration_minutes",
    "cpt",
    "audience",
    "min_notice_hours",
    "earliest_offer_business_days",
    "horizon",
    "horizon_unit",
    "self_bookable",
    "offerable",
    "created_at",
    "updated_at",
)


def _row_to_appointment_type(row: AppointmentTypeRow) -> AppointmentType:
    return AppointmentType(
        id=row.id,
        user_id=row.user_id,
        **{name: getattr(row, name) for name in _SCHEDULING_FIELDS},
    )


def _appointment_type_to_row(appointment_type: AppointmentType, row: AppointmentTypeRow) -> None:
    row.id = appointment_type.id
    row.user_id = appointment_type.user_id
    for name in _SCHEDULING_FIELDS:
        setattr(row, name, getattr(appointment_type, name))
