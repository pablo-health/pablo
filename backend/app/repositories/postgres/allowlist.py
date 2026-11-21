# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL allowlist repository — reads/writes from platform.allowed_emails."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from ...db.platform_models import (
    EmailTenantMappingRow,
    PlatformAllowedEmailRow,
    PracticeMemberRow,
)
from ...utcnow import utc_now
from ..allowlist import AllowlistRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: The memberships revoking a grant takes with it: the first sign-in's claim,
#: the migrate step's backfill of accounts mapped before memberships existed,
#: and a redeemed invitation, which writes the grant and the mapping for the
#: account's address and records that address on the membership
#: (``app.services.practice_invites.redeem``). Revoking is today the only way
#: an operator takes a clinician out of a practice, so an invited member who
#: kept access after it would be a silent gap. A membership with no recorded
#: address is never matched.
_REMOVED_WITH_GRANT = ("email_claim", "backfill", "invite")


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
        # Flushed before the membership read, so this transaction holds the
        # mapping's row lock by then. A claim in flight either holds its share
        # lock already (this waits, and the read below sees its row) or waits
        # for this commit and finds no mapping (``practice_members._CLAIM_SQL``).
        # Autoflush would order it the same way; this does not depend on it.
        self._session.flush()
        # And the membership the grant led to. A signed-in account resolves
        # from its membership before the mapping is read
        # (``app.auth.service._resolve_practice``), so with only the mapping
        # gone a revoked account would still enter. Matched on the address
        # the membership was claimed with, not the one stored on the account
        # (written once and never refreshed), and wherever the membership is,
        # so it is found after a re-grant moved the mapping elsewhere, or for a
        # grant that names no practice. In this session, so the resolution
        # cache drops the account's answer when the revoke commits.
        members = self._session.execute(
            select(PracticeMemberRow).where(
                PracticeMemberRow.claimed_email == normalized,
                PracticeMemberRow.joined_via.in_(_REMOVED_WITH_GRANT),
            )
        ).scalars()
        for member in members:
            self._session.delete(member)
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
