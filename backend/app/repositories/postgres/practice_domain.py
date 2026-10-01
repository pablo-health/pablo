# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL implementation of PracticeDomainRepository."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from ...db.platform_models import PracticeDomainApexRow, PracticeDomainRow
from ...models.practice_domain import PracticeDomain, PracticeDomainApex
from ...utcnow import utc_now
from ..practice_domain import DomainTakenError, PracticeDomainRepository

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import Delete, Update
    from sqlalchemy.engine import CursorResult
    from sqlalchemy.orm import Session

    from ...models.practice_domain import (
        DomainPurpose,
        EmailIdentityStatus,
        HostStatus,
        ServingState,
    )

# SQLSTATE 23505. A fresh row is never primary, so the only unique constraint
# an insert can trip is the primary key: the hostname, or the domain.
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
        cert_auth_value=row.cert_auth_value,
        cert_status=row.cert_status,
        last_error=row.last_error,
        cert_reissued_at=row.cert_reissued_at,
    )


def _to_apex(row: PracticeDomainApexRow) -> PracticeDomainApex:
    return PracticeDomainApex(
        apex=row.apex,
        practice_id=row.practice_id,
        verify_token=row.verify_token,
        created_at=row.created_at,
        verified_at=row.verified_at,
        email_identity_status=row.email_identity_status,  # type: ignore[arg-type]  # CHECK-constrained VARCHAR
        email_dkim_tokens=list(row.email_dkim_tokens) if row.email_dkim_tokens else None,
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
            cert_auth_value=domain.cert_auth_value,
            cert_status=domain.cert_status,
            last_error=domain.last_error,
            cert_reissued_at=domain.cert_reissued_at,
            created_at=domain.created_at,
            updated_at=domain.updated_at,
        )
        self._insert(row, domain.domain)
        return domain

    def _insert(self, row: PracticeDomainRow | PracticeDomainApexRow, key: str) -> None:
        # SAVEPOINT, so a host or domain someone else took between our read and
        # this insert undoes this row and nothing else the request staged.
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except IntegrityError as e:
            if getattr(e.orig, "pgcode", None) != _UNIQUE_VIOLATION:
                raise
            raise DomainTakenError(key) from e

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

    def get_apex(self, apex: str) -> PracticeDomainApex | None:
        row = self._session.get(PracticeDomainApexRow, apex)
        return _to_apex(row) if row else None

    def list_apexes_for_practice(self, practice_id: str) -> list[PracticeDomainApex]:
        stmt = (
            select(PracticeDomainApexRow)
            .where(PracticeDomainApexRow.practice_id == practice_id)
            .order_by(PracticeDomainApexRow.apex)
        )
        return [_to_apex(row) for row in self._session.execute(stmt).scalars()]

    def add_apex(self, apex: PracticeDomainApex) -> PracticeDomainApex:
        row = PracticeDomainApexRow(
            apex=apex.apex,
            practice_id=apex.practice_id,
            verify_token=apex.verify_token,
            verified_at=apex.verified_at,
            email_identity_status=apex.email_identity_status,
            email_dkim_tokens=apex.email_dkim_tokens,
            created_at=apex.created_at,
            updated_at=apex.updated_at,
        )
        self._insert(row, apex.apex)
        return apex

    def remove_apex(self, apex: str, practice_id: str) -> bool:
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                delete(PracticeDomainApexRow).where(
                    PracticeDomainApexRow.apex == apex,
                    PracticeDomainApexRow.practice_id == practice_id,
                )
            ),
        )
        self._session.flush()
        return bool(result.rowcount)

    def mark_apex_verified(self, apex: str, practice_id: str, at: datetime) -> None:
        self._session.execute(
            update(PracticeDomainApexRow)
            .where(
                PracticeDomainApexRow.apex == apex,
                PracticeDomainApexRow.practice_id == practice_id,
            )
            .values(verified_at=at, updated_at=at)
        )
        self._session.flush()

    def list_all(self) -> list[PracticeDomain]:
        stmt = select(PracticeDomainRow).order_by(
            PracticeDomainRow.created_at, PracticeDomainRow.domain
        )
        return [_to_domain(row) for row in self._session.execute(stmt).scalars()]

    def mark_removing(self, domain: str, practice_id: str) -> bool:
        return self._changed(
            update(PracticeDomainRow)
            .where(
                PracticeDomainRow.domain == domain,
                PracticeDomainRow.practice_id == practice_id,
            )
            .values(status="removing", is_primary=False, updated_at=utc_now())
        )

    def record_serving(
        self,
        domain: str,
        *,
        expected_status: HostStatus,
        state: ServingState,
    ) -> bool:
        return self._changed(
            update(PracticeDomainRow)
            .where(
                PracticeDomainRow.domain == domain,
                PracticeDomainRow.status == expected_status,
            )
            .values(
                status=state.status,
                cert_auth_value=state.cert_auth_value,
                cert_status=state.cert_status,
                last_error=state.last_error,
                cert_reissued_at=state.cert_reissued_at,
                verified_at=state.verified_at,
                updated_at=utc_now(),
            )
        )

    def claim_reissue(self, domain: str, *, last: datetime | None, at: datetime) -> bool:
        unchanged = (
            PracticeDomainRow.cert_reissued_at.is_(None)
            if last is None
            else PracticeDomainRow.cert_reissued_at == last
        )
        return self._changed(
            update(PracticeDomainRow)
            .where(PracticeDomainRow.domain == domain, unchanged)
            .values(cert_reissued_at=at)
        )

    def delete_removing(self, domain: str) -> bool:
        return self._changed(
            delete(PracticeDomainRow).where(
                PracticeDomainRow.domain == domain,
                PracticeDomainRow.status == "removing",
            )
        )

    def set_email_identity(
        self,
        apex: str,
        practice_id: str,
        status: EmailIdentityStatus,
        dkim_tokens: list[str] | None,
    ) -> None:
        self._changed(
            update(PracticeDomainApexRow)
            .where(
                PracticeDomainApexRow.apex == apex,
                PracticeDomainApexRow.practice_id == practice_id,
            )
            .values(
                email_identity_status=status,
                email_dkim_tokens=list(dkim_tokens) if dkim_tokens else None,
                updated_at=utc_now(),
            )
        )

    def _changed(self, stmt: Update | Delete) -> bool:
        # cast: an executed UPDATE or DELETE is a CursorResult, which carries
        # rowcount.
        result = cast("CursorResult[Any]", self._session.execute(stmt))
        self._session.flush()
        return bool(result.rowcount)
