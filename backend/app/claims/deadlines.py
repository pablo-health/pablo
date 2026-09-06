# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""When a claim runs out of time.

Three clocks, read off the payer row:

* **filing** — the original claim must reach the payer within
  ``timely_filing_days`` of the earliest service date on its lines. It runs
  while the claim has not been accepted by the payer: a draft, a validated
  claim waiting to go out, a rejection that needs a corrected resubmission,
  or a claim that stalled before the payer acknowledged it.
* **correction** — after a denial or a partial payment, a corrected claim
  must follow within ``corrected_claim_days`` of the remittance.
* **appeal** — likewise an appeal within ``appeal_days`` of the remittance.

The one that applies is the one the claim's state puts it under; when a
denied or partially-paid claim has both a correction and an appeal window,
the sooner of the two is what a person needs to know about. A paid claim
and a void are under no clock.

Pure: dates in, dates out, no I/O and no reading of the clock — ``today``
is an argument, so the answer for a given day is the same whenever it is
asked. ``days_left`` goes negative once the date has passed rather than
clamping, so "how late is this" is answered too.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ..models.claims import Claim
    from ..models.coverage import Payer

DeadlineKind = Literal["filing", "correction", "appeal"]


@dataclass(frozen=True)
class ClaimDeadlines:
    """The three dates, which one applies right now, and how many days remain."""

    filing: date | None
    correction: date | None
    appeal: date | None
    applicable: DeadlineKind | None
    days_left: int | None


#: States under the filing clock outright.
_FILING_STATES: frozenset[str] = frozenset({"draft", "validated", "rejected"})

#: States under the correction / appeal clocks, once a remittance exists.
_REMITTANCE_STATES: frozenset[str] = frozenset({"denied", "partial"})


def deadlines_for(
    claim: Claim,
    payer: Payer,
    remittance_received_at: datetime | None,
    *,
    today: date,
) -> ClaimDeadlines:
    """Every deadline the claim has, and the one that applies on ``today``.

    ``remittance_received_at`` is when the 835 that denied or partially paid
    the claim arrived; ``None`` when there is none yet, in which case the
    correction and appeal dates are unknown.
    """
    filing = _filing_deadline(claim, payer)
    correction, appeal = _remittance_deadlines(payer, remittance_received_at)

    applicable, deadline = _applicable(claim, filing, correction, appeal)
    days_left = (deadline - today).days if deadline is not None else None
    return ClaimDeadlines(
        filing=filing,
        correction=correction,
        appeal=appeal,
        applicable=applicable,
        days_left=days_left,
    )


def _filing_deadline(claim: Claim, payer: Payer) -> date | None:
    if not claim.lines:
        return None
    earliest = min(line.service_date for line in claim.lines)
    return earliest + timedelta(days=payer.timely_filing_days)


def _remittance_deadlines(
    payer: Payer, remittance_received_at: datetime | None
) -> tuple[date | None, date | None]:
    if remittance_received_at is None:
        return None, None
    received = remittance_received_at.date()
    return (
        received + timedelta(days=payer.corrected_claim_days),
        received + timedelta(days=payer.appeal_days),
    )


def _applicable(
    claim: Claim,
    filing: date | None,
    correction: date | None,
    appeal: date | None,
) -> tuple[DeadlineKind | None, date | None]:
    if claim.frequency_code == "8" or claim.state == "paid":
        return None, None
    stalled_before_acceptance = claim.state == "stalled" and claim.payer_accepted_at is None
    if claim.state in _FILING_STATES or stalled_before_acceptance:
        return ("filing", filing) if filing is not None else (None, None)
    if claim.state in _REMITTANCE_STATES:
        # The sooner of the two; on a tie the correction, since it is the
        # cheaper action.
        chosen: tuple[DeadlineKind | None, date | None] = (None, None)
        if correction is not None:
            chosen = ("correction", correction)
        if appeal is not None and (chosen[1] is None or appeal < chosen[1]):
            chosen = ("appeal", appeal)
        return chosen
    return None, None
