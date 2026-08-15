# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL appointment type repository implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from ...db.models import AppointmentTypeRow
from ...scheduling_engine.models.appointment_type import AppointmentType
from ...scheduling_engine.repositories.appointment_type import AppointmentTypeRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


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
        self._session.add(row)
        self._session.flush()
        return appointment_type

    def update(self, appointment_type: AppointmentType) -> AppointmentType:
        row = self._session.get(AppointmentTypeRow, appointment_type.id)
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


def _row_to_appointment_type(row: AppointmentTypeRow) -> AppointmentType:
    return AppointmentType(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        default_fee_cents=row.default_fee_cents,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _appointment_type_to_row(appointment_type: AppointmentType, row: AppointmentTypeRow) -> None:
    row.id = appointment_type.id
    row.user_id = appointment_type.user_id
    row.name = appointment_type.name
    row.default_fee_cents = appointment_type.default_fee_cents
    row.created_at = appointment_type.created_at
    row.updated_at = appointment_type.updated_at
