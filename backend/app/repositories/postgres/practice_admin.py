# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL PracticeAdminRepository implementation.

Tenant scope is the session's ``search_path``, set before the request reaches
here, so none of these queries carries a practice predicate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from ...db.models import PracticeAdminRow
from ..practice_admin import PracticeAdminRepository

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session


class PostgresPracticeAdminRepository(PracticeAdminRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def is_admin(self, user_id: str) -> bool:
        return self._session.get(PracticeAdminRow, user_id) is not None

    def list_admin_ids(self) -> set[str]:
        rows = self._session.execute(select(PracticeAdminRow.user_id)).scalars()
        return {str(user_id) for user_id in rows}

    def grant(self, user_id: str, granted_by: str, granted_at: datetime) -> bool:
        if self.is_admin(user_id):
            return False
        self._session.add(
            PracticeAdminRow(user_id=user_id, granted_by=granted_by, granted_at=granted_at)
        )
        self._session.flush()
        return True

    def revoke(self, user_id: str) -> bool:
        row = self._session.get(PracticeAdminRow, user_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True
