# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL implementation of PasskeyBackupCodeRepository."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select

from ...db.platform_models import PasskeyBackupCodeRow
from ...utcnow import utc_now
from ..passkey_backup_code import PasskeyBackupCodeRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresPasskeyBackupCodeRepository(PasskeyBackupCodeRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_codes(self, user_id: str, code_hashes: list[str], created_at: datetime) -> None:
        self._session.add_all(
            PasskeyBackupCodeRow(
                code_hash=code_hash, user_id=user_id, created_at=created_at, consumed_at=None
            )
            for code_hash in code_hashes
        )
        self._session.flush()

    def delete_unused(self, user_id: str) -> None:
        self._session.execute(
            delete(PasskeyBackupCodeRow).where(
                PasskeyBackupCodeRow.user_id == user_id,
                PasskeyBackupCodeRow.consumed_at.is_(None),
            )
        )
        self._session.flush()

    def count_unused(self, user_id: str) -> int:
        stmt = select(func.count()).where(
            PasskeyBackupCodeRow.user_id == user_id,
            PasskeyBackupCodeRow.consumed_at.is_(None),
        )
        return int(self._session.execute(stmt).scalar_one())

    def consume(self, user_id: str, code_hash: str) -> bool:
        # SELECT ... FOR UPDATE locks the row so two racing redemptions can't
        # both spend the same code: the loser blocks, then sees consumed_at set.
        stmt = (
            select(PasskeyBackupCodeRow)
            .where(
                PasskeyBackupCodeRow.code_hash == code_hash,
                PasskeyBackupCodeRow.user_id == user_id,
                PasskeyBackupCodeRow.consumed_at.is_(None),
            )
            .with_for_update()
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return False
        row.consumed_at = utc_now()
        self._session.flush()
        return True
