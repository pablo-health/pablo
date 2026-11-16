# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL allowlist repository — reads/writes from platform.allowed_emails."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from ...db.platform_models import (
    EmailTenantMappingRow,
    PlatformAllowedEmailRow,
    PlatformUserRow,
    PracticeMemberRow,
)
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
        granted_practices = {row.practice_id} if row.practice_id else set[str]()
        self._session.delete(row)
        # Revoking the grant retires the mapping with it, for the same
        # reason granting creates it: a mapping without a grant is an
        # identity that resolves to a practice it may no longer enter.
        mapping = self._session.get(EmailTenantMappingRow, normalized)
        if mapping is not None:
            granted_practices.add(mapping.practice_id)
            self._session.delete(mapping)
        # And the membership the grant led to. A signed-in account resolves
        # from ``practice_members`` before the mapping is read
        # (``app.auth.service._resolve_practice``), so with the mapping gone
        # and the membership kept, a revoked account would still enter.
        # Only a membership in the practice this grant named: an account that
        # has since joined another practice keeps that one. In this session,
        # so the resolution cache listener drops the account's cached answer
        # when the revocation commits.
        if granted_practices:
            self._revoke_memberships(normalized, granted_practices)
        self._session.flush()
        return True

    def _revoke_memberships(self, email: str, practice_ids: set[str]) -> None:
        members = (
            self._session.execute(
                select(PracticeMemberRow)
                .join(PlatformUserRow, PlatformUserRow.id == PracticeMemberRow.user_id)
                .where(
                    func.lower(PlatformUserRow.email) == email,
                    PracticeMemberRow.practice_id.in_(practice_ids),
                )
            )
            .scalars()
            .all()
        )
        for member in members:
            self._session.delete(member)

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
