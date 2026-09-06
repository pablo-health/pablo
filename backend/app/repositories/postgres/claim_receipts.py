# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL implementation of the claim receipt repository.

Runs on the caller's tenant-scoped session. ``claim_events`` carries the
claim's ``patient_id`` and is isolated by the same ``has_patient_access``
policy as the claim, so no access predicate is written here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import exists, select

from ...db.models import ClaimEventRow, ClaimRow
from ...models.claims import ClaimReceipt
from ...utcnow import utc_now
from ..claim_receipts import ClaimReceiptRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_FIELDS = (
    "claim_id",
    "kind",
    "from_state",
    "to_state",
    "deadline_kind",
    "rung",
    "vendor_event_id",
    "vendor_transaction_id",
    "detail",
    "occurred_at",
)


def _to_receipt(row: ClaimEventRow) -> ClaimReceipt:
    return ClaimReceipt(id=row.id, **{name: getattr(row, name) for name in _FIELDS})


class PostgresClaimReceiptRepository(ClaimReceiptRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, receipt: ClaimReceipt) -> ClaimReceipt:
        claim = self._session.get(ClaimRow, receipt.claim_id)
        if claim is None:
            msg = f"claim {receipt.claim_id!r} not found for receipt"
            raise LookupError(msg)
        row = ClaimEventRow(
            id=receipt.id,
            patient_id=claim.patient_id,
            created_at=utc_now(),
            **{name: getattr(receipt, name) for name in _FIELDS},
        )
        self._session.add(row)
        self._session.flush()
        return _to_receipt(row)

    def list_for_claim(self, claim_id: str) -> list[ClaimReceipt]:
        rows = (
            self._session.execute(
                select(ClaimEventRow)
                .where(ClaimEventRow.claim_id == claim_id)
                .order_by(ClaimEventRow.occurred_at, ClaimEventRow.created_at)
            )
            .scalars()
            .all()
        )
        return [_to_receipt(row) for row in rows]

    def has_rung(self, claim_id: str, kind: str, *, deadline_kind: str, rung: int) -> bool:
        return bool(
            self._session.execute(
                select(
                    exists().where(
                        ClaimEventRow.claim_id == claim_id,
                        ClaimEventRow.kind == kind,
                        ClaimEventRow.deadline_kind == deadline_kind,
                        ClaimEventRow.rung == rung,
                    )
                )
            ).scalar()
        )

    def vendor_event_seen(self, vendor_event_id: str) -> bool:
        return bool(
            self._session.execute(
                select(exists().where(ClaimEventRow.vendor_event_id == vendor_event_id))
            ).scalar()
        )

    def vendor_transaction_seen(self, vendor_transaction_id: str) -> bool:
        return bool(
            self._session.execute(
                select(exists().where(ClaimEventRow.vendor_transaction_id == vendor_transaction_id))
            ).scalar()
        )
