# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL implementation of PasskeyCredentialRepository."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from ...db.platform_models import PasskeyCredentialRow
from ...models.passkey import PasskeyCredential
from ...utcnow import utc_now
from ..passkey_credential import PasskeyCredentialRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _to_domain(row: PasskeyCredentialRow) -> PasskeyCredential:
    return PasskeyCredential(
        credential_id=row.credential_id,
        user_id=str(row.user_id),
        public_key=bytes(row.public_key),
        sign_count=row.sign_count,
        transports=row.transports,
        aaguid=row.aaguid,
        fmt=row.fmt,
        attestation_verified=row.attestation_verified,
        backup_eligible=row.backup_eligible,
        backup_state=row.backup_state,
        device_label=row.device_label,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


class PostgresPasskeyCredentialRepository(PasskeyCredentialRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, credential: PasskeyCredential) -> None:
        self._session.add(
            PasskeyCredentialRow(
                credential_id=credential.credential_id,
                user_id=credential.user_id,
                public_key=credential.public_key,
                sign_count=credential.sign_count,
                transports=credential.transports,
                aaguid=credential.aaguid,
                fmt=credential.fmt,
                attestation_verified=credential.attestation_verified,
                backup_eligible=credential.backup_eligible,
                backup_state=credential.backup_state,
                device_label=credential.device_label,
                created_at=credential.created_at,
                last_used_at=credential.last_used_at,
                revoked_at=credential.revoked_at,
            )
        )
        self._session.flush()

    def get_active(self, credential_id: str) -> PasskeyCredential | None:
        stmt = select(PasskeyCredentialRow).where(
            PasskeyCredentialRow.credential_id == credential_id,
            PasskeyCredentialRow.revoked_at.is_(None),
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    def list_for_user(self, user_id: str) -> list[PasskeyCredential]:
        stmt = (
            select(PasskeyCredentialRow)
            .where(
                PasskeyCredentialRow.user_id == user_id,
                PasskeyCredentialRow.revoked_at.is_(None),
            )
            .order_by(PasskeyCredentialRow.created_at.asc())
        )
        rows = self._session.execute(stmt).scalars().all()
        return [_to_domain(row) for row in rows]

    def update_after_assertion(
        self,
        credential_id: str,
        *,
        sign_count: int,
        backup_state: bool,
        last_used_at: datetime | None = None,
    ) -> None:
        stmt = (
            update(PasskeyCredentialRow)
            .where(
                PasskeyCredentialRow.credential_id == credential_id,
                PasskeyCredentialRow.revoked_at.is_(None),
            )
            .values(
                sign_count=sign_count,
                backup_state=backup_state,
                last_used_at=last_used_at or utc_now(),
            )
        )
        self._session.execute(stmt)
        self._session.flush()

    def revoke(self, credential_id: str, *, user_id: str) -> bool:
        stmt = select(PasskeyCredentialRow).where(
            PasskeyCredentialRow.credential_id == credential_id,
            PasskeyCredentialRow.user_id == user_id,
            PasskeyCredentialRow.revoked_at.is_(None),
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return False
        row.revoked_at = utc_now()
        self._session.flush()
        return True
