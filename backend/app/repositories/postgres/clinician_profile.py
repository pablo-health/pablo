# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL clinician profile repository — tenant-scoped."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ...db.models import ClinicianProfileRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass
class ClinicianProfile:
    user_id: str
    practice_id: str
    title: str | None = None
    credentials: str | None = None
    role: str = "clinician"
    joined_at: datetime | None = None


class PostgresClinicianProfileRepository:
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
            joined_at=profile.joined_at,
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
