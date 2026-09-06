# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Data access for claims and their lines.

One repository for the claim with its lines: a claim is read and written as
a whole, and a line has no life outside its claim. It does not commit — it
rides the request's transaction the same way the coverage repositories do,
so the claim and the audit entry describing it land together or not at all.

Access is not decided here. In Postgres the ``has_patient_access`` row
policy on both tables hides other clinicians' clients; the route confirms
the client is visible before it reads a claim, and the in-memory
implementation relies on the route doing that too.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..utcnow import utc_now

if TYPE_CHECKING:
    from collections.abc import Collection
    from datetime import date

    from ..models.claims import Claim


class ClaimRepository(ABC):
    @abstractmethod
    def get(self, claim_id: str) -> Claim | None:
        """One claim with its lines, or ``None``."""

    @abstractmethod
    def get_by_control_number(self, control_number: str) -> Claim | None:
        """The claim filed under ``control_number``, matched case-insensitively.

        Acknowledgements echo the control number back, and some payers
        upper-case it on the way.
        """

    @abstractmethod
    def list_by_patient(self, patient_id: str) -> list[Claim]:
        """Every claim for a client, newest first."""

    @abstractmethod
    def list_by_state(
        self, states: Collection[str], *, limit: int, newest_first: bool = False
    ) -> list[Claim]:
        """Claims in any of ``states``, oldest first unless asked otherwise.

        Oldest first is the outbox's order — the claim that has waited
        longest goes next; newest first is the tracker's.
        """

    @abstractmethod
    def list_for_export(self, from_date: date, to_date: date) -> list[Claim]:
        """Every claim past ``draft`` with a service line dated in the range.

        Both ends inclusive; oldest first, so the biller's file reads in the
        order the visits happened. Drafts are left out — nothing that has
        not passed the scrub leaves the practice.
        """

    @abstractmethod
    def create(self, claim: Claim) -> Claim:
        """Add a claim and its lines. Flushed, not committed."""

    @abstractmethod
    def update(self, claim: Claim) -> Claim:
        """Write the claim's current header and line amounts back.

        The lines' codes, dates and charges are fixed at build time; what
        changes later is the state, the timestamps and what the payer
        allowed and paid. Flushed, not committed.
        """


class InMemoryClaimRepository(ClaimRepository):
    def __init__(self) -> None:
        self._claims: dict[str, Claim] = {}

    def get(self, claim_id: str) -> Claim | None:
        claim = self._claims.get(claim_id)
        return claim.model_copy(deep=True) if claim is not None else None

    def get_by_control_number(self, control_number: str) -> Claim | None:
        wanted = control_number.upper()
        match = next((c for c in self._claims.values() if c.control_number.upper() == wanted), None)
        return match.model_copy(deep=True) if match is not None else None

    def list_by_patient(self, patient_id: str) -> list[Claim]:
        matches = [c for c in self._claims.values() if c.patient_id == patient_id]
        return [c.model_copy(deep=True) for c in sorted(matches, key=_newest_first)]

    def list_by_state(
        self, states: Collection[str], *, limit: int, newest_first: bool = False
    ) -> list[Claim]:
        matches = [c for c in self._claims.values() if c.state in states]
        ordered = sorted(matches, key=_newest_first if newest_first else _oldest_first)
        return [c.model_copy(deep=True) for c in ordered[:limit]]

    def list_for_export(self, from_date: date, to_date: date) -> list[Claim]:
        matches = [
            c
            for c in self._claims.values()
            if c.state != "draft"
            and any(from_date <= line.service_date <= to_date for line in c.lines)
        ]
        return [c.model_copy(deep=True) for c in sorted(matches, key=_oldest_first)]

    def create(self, claim: Claim) -> Claim:
        if any(c.control_number == claim.control_number for c in self._claims.values()):
            msg = f"control number {claim.control_number!r} already used"
            raise ValueError(msg)
        self._claims[claim.id] = claim.model_copy(deep=True)
        return claim

    def update(self, claim: Claim) -> Claim:
        if claim.id not in self._claims:
            msg = f"claim {claim.id!r} not found for update"
            raise LookupError(msg)
        updated = claim.model_copy(update={"updated_at": utc_now()}, deep=True)
        self._claims[claim.id] = updated
        return updated


def _newest_first(claim: Claim) -> tuple[float, str]:
    return (-claim.created_at.timestamp(), claim.id)


def _oldest_first(claim: Claim) -> tuple[float, str]:
    return (claim.created_at.timestamp(), claim.id)
