# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Data access for remittance holds: the client bills the engine refused to write.

Flushed, never committed, so a hold and its posting's receipt commit or roll
back together.

Two writes only: raising a hold, and a person deciding how it ends. Nothing
here releases one on its own.
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

        Raises :class:`ValueError` on a duplicate ``posting_key`` — one
        remittance delivered twice is one disagreement.
        """

    @abstractmethod
    def get(self, hold_id: str) -> RemittanceHold | None:
        """One hold by id, or ``None`` if this principal cannot see it.

        Hidden and absent are indistinguishable on purpose.
        """

    @abstractmethod
    def get_by_posting_key(self, posting_key: str) -> RemittanceHold | None:
        """The hold raised for this posting, if one was."""

    @abstractmethod
    def open_for_claim(self, claim_id: str) -> RemittanceHold | None:
        """The claim's unresolved hold, if it has one.

        A second disagreement while the first is open is the same
        conversation with the practice, not a new one.
        """

    @abstractmethod
    def list_open(self, *, limit: int | None = None) -> list[RemittanceHold]:
        """Every hold still withholding a ledger row, oldest first.

        Includes ``acknowledged``.
        """

    @abstractmethod
    def acknowledge(self, hold_id: str, *, at: datetime) -> RemittanceHold | None:
        """Mark the hold seen, leaving it open. ``None`` if there is none.

        Idempotent — keeps the FIRST timestamp, and leaves a resolved hold
        alone.
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

        An already-resolved hold keeps its first decision.
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
