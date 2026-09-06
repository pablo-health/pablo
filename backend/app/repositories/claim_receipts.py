# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Data access for a claim's receipts: the hops it took and the alerts raised.

Append-only. A receipt is written when a claim moves or an alert fires and
never edited afterwards; the tracker reads them back in order. The two
uniqueness questions the acknowledgement paths ask — has this vendor event
been handled, has this deadline rung fired — are answered here so the
callers stay idempotent across retries and restarts.

Rides the caller's transaction like the claim repository: flushed, never
committed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.claims import ClaimReceipt


class ClaimReceiptRepository(ABC):
    @abstractmethod
    def add(self, receipt: ClaimReceipt) -> ClaimReceipt:
        """Record a receipt. Flushed, not committed."""

    @abstractmethod
    def list_for_claim(self, claim_id: str) -> list[ClaimReceipt]:
        """Every receipt on the claim, oldest first."""

    @abstractmethod
    def has_rung(self, claim_id: str, kind: str, *, deadline_kind: str, rung: int) -> bool:
        """Has this deadline alert already fired for the claim?"""

    @abstractmethod
    def vendor_event_seen(self, vendor_event_id: str) -> bool:
        """Has a webhook delivery with this vendor event id been handled?"""

    @abstractmethod
    def vendor_transaction_seen(self, vendor_transaction_id: str) -> bool:
        """Has this vendor transaction (a 277CA, say) been applied to any claim?"""


class InMemoryClaimReceiptRepository(ClaimReceiptRepository):
    def __init__(self) -> None:
        self._receipts: list[ClaimReceipt] = []

    def add(self, receipt: ClaimReceipt) -> ClaimReceipt:
        if receipt.vendor_event_id and self.vendor_event_seen(receipt.vendor_event_id):
            msg = f"vendor event {receipt.vendor_event_id!r} already recorded"
            raise ValueError(msg)
        if receipt.rung is not None and self.has_rung(
            receipt.claim_id,
            receipt.kind,
            deadline_kind=receipt.deadline_kind or "",
            rung=receipt.rung,
        ):
            msg = f"deadline rung already recorded for claim {receipt.claim_id!r}"
            raise ValueError(msg)
        self._receipts.append(receipt.model_copy(deep=True))
        return receipt

    def list_for_claim(self, claim_id: str) -> list[ClaimReceipt]:
        matches = [r for r in self._receipts if r.claim_id == claim_id]
        return [r.model_copy(deep=True) for r in sorted(matches, key=lambda r: r.occurred_at)]

    def has_rung(self, claim_id: str, kind: str, *, deadline_kind: str, rung: int) -> bool:
        return any(
            r.claim_id == claim_id
            and r.kind == kind
            and (r.deadline_kind or "") == deadline_kind
            and r.rung == rung
            for r in self._receipts
        )

    def vendor_event_seen(self, vendor_event_id: str) -> bool:
        return any(r.vendor_event_id == vendor_event_id for r in self._receipts)

    def vendor_transaction_seen(self, vendor_transaction_id: str) -> bool:
        return any(r.vendor_transaction_id == vendor_transaction_id for r in self._receipts)
