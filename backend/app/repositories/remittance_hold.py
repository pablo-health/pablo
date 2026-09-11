# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Data access for remittance holds: the client bills the engine refused to write.

Rides the caller's transaction like the claim repositories — flushed, never
committed — so a hold and the receipt for the same remittance commit or roll
back together. A hold that survived a posting that did not would be a
withheld bill nobody withheld.

Only two writes exist and both are deliberate: raising a hold, and a person
deciding how it ends. Nothing here releases a hold on its own, because time
passing is not evidence that a self-contradicting remittance was right.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from ..models.claims_holds import HoldFinding, RemittanceHold


class RemittanceHoldRepository(ABC):
    """Reads and writes for one practice's remittance holds."""

    @abstractmethod
    def add(self, hold: RemittanceHold) -> RemittanceHold:
        """Record a hold. Flushed, not committed.

        Raises :class:`ValueError` when a hold with the same
        ``posting_key`` already exists — the same remittance delivered
        twice is one disagreement, not two.
        """

    @abstractmethod
    def get(self, hold_id: str) -> RemittanceHold | None:
        """One hold by id, or ``None`` if this principal cannot see it.

        A hold the row policy hides is indistinguishable from one that was
        never written, and deliberately so: a clinician who does not own
        the claim has no business learning that a hold exists on it.
        """

    @abstractmethod
    def get_by_posting_key(self, posting_key: str) -> RemittanceHold | None:
        """The hold raised for this posting, if one was."""

    @abstractmethod
    def open_for_claim(self, claim_id: str) -> RemittanceHold | None:
        """The claim's unresolved hold, if it has one.

        Asked before raising a new one. A claim can be adjudicated more
        than once — a secondary payer, a reversal — and a second
        disagreement while the first is still open is the same
        conversation with the practice, not a new one.
        """

    @abstractmethod
    def list_open(self, *, limit: int | None = None) -> list[RemittanceHold]:
        """Every hold still withholding a ledger row, oldest first.

        ``acknowledged`` holds are included: somebody saying they have seen
        a disagreement is not somebody deciding what to bill.
        """

    @abstractmethod
    def acknowledge(self, hold_id: str, *, at: datetime) -> RemittanceHold | None:
        """Mark the hold seen, leaving it open. ``None`` if there is no such hold.

        Idempotent: acknowledging an already-acknowledged hold keeps the
        first timestamp, because the question it answers is "when did
        somebody first see this", and a resolved hold is left alone.
        """

    @abstractmethod
    def resolve(
        self,
        hold_id: str,
        *,
        finding: HoldFinding,
        user_id: str,
        at: datetime,
    ) -> RemittanceHold | None:
        """Close the hold with how it ended and who ended it.

        Returns ``None`` when there is no such hold, and leaves an
        already-resolved hold exactly as it was rather than overwriting the
        first decision with a second.
        """


class InMemoryRemittanceHoldRepository(RemittanceHoldRepository):
    """The hold repository backed by a dict, for tests and fakes."""

    def __init__(self) -> None:
        self._holds: dict[str, RemittanceHold] = {}

    def add(self, hold: RemittanceHold) -> RemittanceHold:
        if self.get_by_posting_key(hold.posting_key) is not None:
            msg = f"hold for posting {hold.posting_key!r} already recorded"
            raise ValueError(msg)
        self._holds[hold.id] = hold.model_copy(deep=True)
        return hold

    def get(self, hold_id: str) -> RemittanceHold | None:
        found = self._holds.get(hold_id)
        return found.model_copy(deep=True) if found else None

    def get_by_posting_key(self, posting_key: str) -> RemittanceHold | None:
        return next(
            (h.model_copy(deep=True) for h in self._holds.values() if h.posting_key == posting_key),
            None,
        )

    def open_for_claim(self, claim_id: str) -> RemittanceHold | None:
        return next(
            (
                h.model_copy(deep=True)
                for h in sorted(self._holds.values(), key=lambda h: h.detected_at)
                if h.claim_id == claim_id and h.is_open
            ),
            None,
        )

    def list_open(self, *, limit: int | None = None) -> list[RemittanceHold]:
        found = sorted(
            (h.model_copy(deep=True) for h in self._holds.values() if h.is_open),
            key=lambda h: h.detected_at,
        )
        return found[:limit] if limit is not None else found

    def acknowledge(self, hold_id: str, *, at: datetime) -> RemittanceHold | None:
        found = self._holds.get(hold_id)
        if found is None:
            return None
        if found.state == "open":
            self._holds[hold_id] = found.model_copy(
                update={"state": "acknowledged", "acknowledged_at": at}
            )
        return self._holds[hold_id].model_copy(deep=True)

    def resolve(
        self,
        hold_id: str,
        *,
        finding: HoldFinding,
        user_id: str,
        at: datetime,
    ) -> RemittanceHold | None:
        found = self._holds.get(hold_id)
        if found is None:
            return None
        if found.state != "resolved":
            self._holds[hold_id] = found.model_copy(
                update={
                    "state": "resolved",
                    "resolved_at": at,
                    "resolved_by_user_id": user_id,
                    "finding": finding,
                }
            )
        return self._holds[hold_id].model_copy(deep=True)
