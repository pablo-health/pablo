# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL allowlist repository — reads/writes from platform.allowed_emails."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...db.platform_models import PlatformAllowedEmailRow
from ...utcnow import utc_now
from ..allowlist import AllowlistRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresAllowlistRepository(AllowlistRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def is_allowed(self, email: str) -> bool:
        row = self._session.get(PlatformAllowedEmailRow, email.lower())
        return row is not None

    def add(self, email: str, added_by: str) -> None:
        now = utc_now()
        row = self._session.get(PlatformAllowedEmailRow, email.lower())
        if row is None:
            row = PlatformAllowedEmailRow(
                email=email.lower(),
                added_by=added_by,
                added_at=now,
            )
            self._session.add(row)
        else:
            row.added_by = added_by
            row.added_at = now
        self._session.flush()

    def remove(self, email: str) -> bool:
        row = self._session.get(PlatformAllowedEmailRow, email.lower())
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    def list_all(self) -> list[dict[str, Any]]:
        rows = self._session.query(PlatformAllowedEmailRow).all()
        return [
            {
                "email": r.email,
                "added_by": r.added_by,
                "added_at": r.added_at,
                "practice_id": r.practice_id,
            }
            for r in rows
        ]
