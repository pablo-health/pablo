# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL implementation of IdentityRepository."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from ...db.platform_models import UserIdentityRow
from ...utcnow import utc_now
from ..identity import IdentityRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresIdentityRepository(IdentityRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_user_id(self, provider: str, subject_id: str) -> str | None:
        stmt = select(UserIdentityRow.user_id).where(
            UserIdentityRow.provider == provider,
            UserIdentityRow.subject_id == subject_id,
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def link(
        self,
        provider: str,
        subject_id: str,
        user_id: str,
        linked_at: datetime | None = None,
    ) -> None:
        row = UserIdentityRow(
            provider=provider,
            subject_id=subject_id,
            user_id=user_id,
            linked_at=linked_at or utc_now(),
        )
        self._session.add(row)
        self._session.flush()
