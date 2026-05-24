# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL clinician profile repository — tenant-scoped."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...db.models import ClinicianProfileRow
from ...utcnow import utc_now
from ..clinician_profile import ClinicianProfile, ClinicianProfileRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


__all__ = ["ClinicianProfile", "PostgresClinicianProfileRepository"]


class PostgresClinicianProfileRepository(ClinicianProfileRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: str) -> ClinicianProfile | None:
        row = self._session.get(ClinicianProfileRow, user_id)
        if row is None:
            return None
        return ClinicianProfile(
            user_id=row.user_id,
            practice_id=row.practice_id,
            title=row.title,
            credentials=row.credentials,
            role=row.role,
            joined_at=row.joined_at,
        )

    def create(self, profile: ClinicianProfile) -> ClinicianProfile:
        row = ClinicianProfileRow(
            user_id=profile.user_id,
            practice_id=profile.practice_id,
            title=profile.title,
            credentials=profile.credentials,
            role=profile.role,
            joined_at=profile.joined_at or utc_now(),
        )
        self._session.add(row)
        self._session.flush()
        return profile

    def update(self, profile: ClinicianProfile) -> ClinicianProfile:
        row = self._session.get(ClinicianProfileRow, profile.user_id)
        if row is None:
            return self.create(profile)
        row.title = profile.title
        row.credentials = profile.credentials
        row.role = profile.role
        self._session.flush()
        return profile
