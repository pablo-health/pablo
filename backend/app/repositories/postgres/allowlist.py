# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL allowlist repository — reads/writes from platform.allowed_emails."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from ...db.platform_models import EmailTenantMappingRow, PlatformAllowedEmailRow
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

    def add(self, email: str, added_by: str, *, practice_id: str) -> None:
        now = utc_now()
        normalized = email.lower()

        row = self._session.get(PlatformAllowedEmailRow, normalized)
        if row is None:
            row = PlatformAllowedEmailRow(
                email=normalized,
                practice_id=practice_id,
                added_by=added_by,
                added_at=now,
            )
            self._session.add(row)
        else:
            row.practice_id = practice_id
            row.added_by = added_by
            row.added_at = now

        # The mapping the login path reads. Written here, in the same
        # session as the grant, so the two cannot drift apart: an email
        # that is allowed is an email that resolves.
        mapping = self._session.get(EmailTenantMappingRow, normalized)
        if mapping is None:
            self._session.add(
                EmailTenantMappingRow(
                    email=normalized,
                    tenant_id=practice_id,
                    practice_id=practice_id,
                    created_at=now,
                )
            )
        else:
            mapping.tenant_id = practice_id
            mapping.practice_id = practice_id

        self._session.flush()

    def remove(self, email: str) -> bool:
        normalized = email.lower()
        row = self._session.get(PlatformAllowedEmailRow, normalized)
        if row is None:
            return False
        self._session.delete(row)
        # Revoking the grant retires the mapping with it, for the same
        # reason granting creates it: a mapping without a grant is an
        # identity that resolves to a practice it may no longer enter.
        mapping = self._session.get(EmailTenantMappingRow, normalized)
        if mapping is not None:
            self._session.delete(mapping)
        self._session.flush()
        return True

    def list_all(self) -> list[dict[str, Any]]:
        rows = self._session.execute(select(PlatformAllowedEmailRow)).scalars().all()
        return [
            {
                "email": r.email,
                "added_by": r.added_by,
                "added_at": r.added_at,
                "practice_id": r.practice_id,
            }
            for r in rows
        ]
