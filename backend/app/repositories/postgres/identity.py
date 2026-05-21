# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL implementation of IdentityRepository."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

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

    def get_subject_id(self, user_id: str, provider: str) -> str | None:
        stmt = select(UserIdentityRow.subject_id).where(
            UserIdentityRow.user_id == user_id,
            UserIdentityRow.provider == provider,
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

    def resolve_or_create(self, provider: str, subject_id: str) -> str:
        # Base-class implementation does SELECT-then-INSERT, which races
        # across parallel first-login requests for the same firebase_uid
        # and crashes losers with UniqueViolation on user_identities_pkey.
        # Collapse it into one atomic UPSERT + read-back so concurrent
        # callers all converge on the same canonical user_id.
        candidate_user_id = str(uuid.uuid4())
        stmt = (
            pg_insert(UserIdentityRow)
            .values(
                provider=provider,
                subject_id=subject_id,
                user_id=candidate_user_id,
                linked_at=utc_now(),
            )
            .on_conflict_do_nothing(index_elements=["provider", "subject_id"])
        )
        self._session.execute(stmt)
        self._session.flush()
        canonical = self.get_user_id(provider, subject_id)
        if canonical is None:
            raise RuntimeError(
                "user_identities row vanished between UPSERT and SELECT — "
                f"provider={provider} subject_id={subject_id}"
            )
        return canonical
