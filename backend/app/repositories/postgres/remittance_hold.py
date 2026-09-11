# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL implementation of the remittance hold repository.

Runs on the caller's tenant-scoped session. ``remittance_holds`` carries the
claim's ``patient_id`` and is isolated by the same ``has_patient_access``
policy as the claim itself, so no access predicate is written here — a
clinician who cannot see the claim reads zero rows and gets ``None``, which
is the answer we want them to get.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ...db.models import RemittanceHoldRow
from ...models.claims_holds import RemittanceHold
from ..remittance_hold import RemittanceHoldRepository

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

    from ...models.claims_holds import HoldFinding

_FIELDS = (
    "id",
    "claim_id",
    "patient_id",
    "control_number",
    "posting_key",
    "state",
    "reason",
    "stated_cents",
    "computed_cents",
    "patient_responsibility_cents",
    "line_control_number",
    "codes",
    "line_count",
    "payer_name",
    "detected_at",
    "acknowledged_at",
    "resolved_at",
    "resolved_by_user_id",
    "finding",
)


def _to_hold(row: RemittanceHoldRow) -> RemittanceHold:
    return RemittanceHold(**{name: getattr(row, name) for name in _FIELDS})


class PostgresRemittanceHoldRepository(RemittanceHoldRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, hold: RemittanceHold) -> RemittanceHold:
        row = RemittanceHoldRow(**{name: getattr(hold, name) for name in _FIELDS})
        self._session.add(row)
        try:
            # Flushed inside a savepoint so a duplicate posting key raises
            # here, where it means "already held", rather than poisoning the
            # caller's transaction. The posting path is what the receipt
            # ledger already dedupes, and this is the same event arriving
            # twice; losing the whole posting over it would be worse than
            # the duplicate it prevents.
            with self._session.begin_nested():
                self._session.flush()
        except IntegrityError as exc:
            msg = f"hold for posting {hold.posting_key!r} already recorded"
            raise ValueError(msg) from exc
        return _to_hold(row)

    def get(self, hold_id: str) -> RemittanceHold | None:
        row = self._session.get(RemittanceHoldRow, hold_id)
        return _to_hold(row) if row is not None else None

    def get_by_posting_key(self, posting_key: str) -> RemittanceHold | None:
        row = self._session.execute(
            select(RemittanceHoldRow).where(RemittanceHoldRow.posting_key == posting_key)
        ).scalar_one_or_none()
        return _to_hold(row) if row is not None else None

    def open_for_claim(self, claim_id: str) -> RemittanceHold | None:
        row = (
            self._session.execute(
                select(RemittanceHoldRow)
                .where(RemittanceHoldRow.claim_id == claim_id)
                .where(RemittanceHoldRow.state != "resolved")
                .order_by(RemittanceHoldRow.detected_at)
                .limit(1)
            )
            .scalars()
            .first()
        )
        return _to_hold(row) if row is not None else None

    def list_open(self, *, limit: int | None = None) -> list[RemittanceHold]:
        query = (
            select(RemittanceHoldRow)
            .where(RemittanceHoldRow.state != "resolved")
            .order_by(RemittanceHoldRow.detected_at)
        )
        if limit is not None:
            query = query.limit(limit)
        return [_to_hold(row) for row in self._session.execute(query).scalars().all()]

    def acknowledge(self, hold_id: str, *, at: datetime) -> RemittanceHold | None:
        row = self._session.get(RemittanceHoldRow, hold_id)
        if row is None:
            return None
        if row.state == "open":
            row.state = "acknowledged"
            row.acknowledged_at = at
            self._session.flush()
        return _to_hold(row)

    def resolve(
        self,
        hold_id: str,
        *,
        finding: HoldFinding,
        user_id: str,
        at: datetime,
    ) -> RemittanceHold | None:
        row = self._session.get(RemittanceHoldRow, hold_id)
        if row is None:
            return None
        if row.state != "resolved":
            row.state = "resolved"
            row.resolved_at = at
            row.resolved_by_user_id = user_id
            row.finding = finding
            self._session.flush()
        return _to_hold(row)
