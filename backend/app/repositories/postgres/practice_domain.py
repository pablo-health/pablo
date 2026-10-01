# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL implementation of PracticeDomainRepository."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from ...db.platform_models import PracticeDomainRow
from ...models.practice_domain import PracticeDomain
from ...utcnow import utc_now
from ..practice_domain import DomainTakenError, PracticeDomainRepository

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult
    from sqlalchemy.orm import Session

    from ...models.practice_domain import DomainPurpose

# SQLSTATE 23505. A fresh row is never primary, so the only unique constraint
# an insert can trip is the hostname primary key.
_UNIQUE_VIOLATION = "23505"


def _to_domain(row: PracticeDomainRow) -> PracticeDomain:
    return PracticeDomain(
        domain=row.domain,
        practice_id=row.practice_id,
        purpose=row.purpose,  # type: ignore[arg-type]  # CHECK-constrained VARCHAR
        kind=row.kind,  # type: ignore[arg-type]  # CHECK-constrained VARCHAR
        status=row.status,  # type: ignore[arg-type]  # CHECK-constrained VARCHAR
        is_primary=row.is_primary,
        created_at=row.created_at,
        verified_at=row.verified_at,
        updated_at=row.updated_at,
    )


class PostgresPracticeDomainRepository(PracticeDomainRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, domain: str) -> PracticeDomain | None:
        row = self._session.get(PracticeDomainRow, domain)
        return _to_domain(row) if row else None

    def list_for_practice(self, practice_id: str) -> list[PracticeDomain]:
        stmt = (
            select(PracticeDomainRow)
            .where(PracticeDomainRow.practice_id == practice_id)
            .order_by(PracticeDomainRow.created_at, PracticeDomainRow.domain)
        )
        return [_to_domain(row) for row in self._session.execute(stmt).scalars()]

    def add(self, domain: PracticeDomain) -> PracticeDomain:
        row = PracticeDomainRow(
            domain=domain.domain,
            practice_id=domain.practice_id,
            purpose=domain.purpose,
            kind=domain.kind,
            status=domain.status,
            is_primary=domain.is_primary,
            verified_at=domain.verified_at,
            created_at=domain.created_at,
            updated_at=domain.updated_at,
        )
        # SAVEPOINT, so a host someone else took between our read and this
        # insert undoes this row and nothing else the request staged.
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except IntegrityError as e:
            if getattr(e.orig, "pgcode", None) != _UNIQUE_VIOLATION:
                raise
            raise DomainTakenError(domain.domain) from e
        return domain

    def remove(self, domain: str, practice_id: str) -> bool:
        # cast: an executed DELETE is a CursorResult, which carries rowcount.
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                delete(PracticeDomainRow).where(
                    PracticeDomainRow.domain == domain,
                    PracticeDomainRow.practice_id == practice_id,
                )
            ),
        )
        self._session.flush()
        return bool(result.rowcount)

    def set_primary(self, domain: str, practice_id: str, purpose: DomainPurpose) -> None:
        now = utc_now()
        # Unset first and flush, so the partial unique index never sees two
        # primaries for one purpose, even inside the transaction.
        self._session.execute(
            update(PracticeDomainRow)
            .where(
                PracticeDomainRow.practice_id == practice_id,
                PracticeDomainRow.purpose == purpose,
                PracticeDomainRow.is_primary.is_(True),
                PracticeDomainRow.domain != domain,
            )
            .values(is_primary=False, updated_at=now)
        )
        self._session.flush()
        self._session.execute(
            update(PracticeDomainRow)
            .where(
                PracticeDomainRow.domain == domain,
                PracticeDomainRow.practice_id == practice_id,
            )
            .values(is_primary=True, updated_at=now)
        )
        self._session.flush()
