# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Timeouts and deadlines: the alerts nobody else would raise.

Every non-terminal state has a clock. When it runs out the claim becomes
``stalled`` — the alert *is* the state; a person looks, and the late
receipt, when it comes, moves the claim on as it would have. Nothing is
retried into a duplicate.

==================  ==========  =================================================
state               after       what it means
==================  ==========  =================================================
validated, pending  3 days      the outbox could not get an answer to its attempt
submitted           5 days      no payer acknowledgement of the filing
ch_accepted         5 days      the clearinghouse forwarded it; the payer is silent
payer_accepted      30 days     no remittance: confirm remittance enrollment is live
==================  ==========  =================================================

The deadline ladder runs beside it: for every open claim,
:func:`app.claims.deadlines.deadlines_for` says which clock the claim is
under and how many days are left, and the watchdog announces
``deadline_approaching`` at 14, 7 and 2 days and ``deadline_missed`` once
the date has passed — each rung once per claim and clock, recorded on the
receipt ledger so a restart never re-alerts.

Alerts carry the control number, the state and the age. Never a name, a
member id or a diagnosis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .deadlines import deadlines_for
from .receipts import announce, owned_by_principal, record, stall
from .transitions import TERMINAL_STATES

if TYPE_CHECKING:
    from collections.abc import Collection
    from datetime import date

    from ..models.claims import Claim
    from ..repositories.coverage import PayerRepository
    from .events import ClaimEventKind
    from .receipts import ClaimPipeline

logger = logging.getLogger(__name__)

PENDING_TIMEOUT = timedelta(days=3)
ACKNOWLEDGMENT_TIMEOUT = timedelta(days=5)
REMITTANCE_TIMEOUT = timedelta(days=30)

#: Days before the deadline each alert fires at; ``0`` is the missed rung.
DEADLINE_RUNGS: tuple[int, ...] = (14, 7, 2)
MISSED_RUNG = 0

MAX_CLAIMS_PER_RUN = 500

#: Every state a claim can still need a person in. A paid claim is done.
OPEN_STATES: tuple[str, ...] = (
    "draft",
    "validated",
    "submitted",
    "ch_accepted",
    "payer_accepted",
    "rejected",
    "denied",
    "partial",
    "stalled",
)
_REMITTANCE_STATES = frozenset({"denied", "partial"})


@dataclass
class WatchdogSummary:
    checked: int = 0
    stalled: int = 0
    deadline_alerts: int = 0


def _days(delta: timedelta) -> int:
    return delta.days


def _timeout(claim: Claim, now: datetime) -> tuple[str, str] | None:
    """The stall code and its wording if the claim's clock has run out."""
    if claim.state == "validated" and claim.submission_pending_at is not None:
        age = now - claim.submission_pending_at
        if age > PENDING_TIMEOUT:
            return (
                "submission_unconfirmed",
                f"Sent {_days(age)} days ago and the clearinghouse has not answered",
            )
    elif claim.state in ("submitted", "ch_accepted") and claim.submitted_at is not None:
        age = now - claim.submitted_at
        if age > ACKNOWLEDGMENT_TIMEOUT:
            return (
                "no_payer_acknowledgment",
                f"Submitted {_days(age)} days ago with no acknowledgment from the payer",
            )
    elif claim.state == "payer_accepted" and claim.payer_accepted_at is not None:
        age = now - claim.payer_accepted_at
        if age > REMITTANCE_TIMEOUT:
            return (
                "no_remittance",
                f"Accepted by the payer {_days(age)} days ago with no remittance; "
                "confirm remittance enrollment is live",
            )
    return None


def _deadline_alert(
    pipeline: ClaimPipeline,
    claim: Claim,
    payers: PayerRepository,
    *,
    today: date,
    summary: WatchdogSummary,
) -> None:
    payer = payers.get(claim.payer_id)
    if payer is None:
        return
    remittance_at = claim.adjudicated_at if claim.state in _REMITTANCE_STATES else None
    deadlines = deadlines_for(claim, payer, remittance_at, today=today)
    if deadlines.applicable is None or deadlines.days_left is None:
        return
    kind: ClaimEventKind
    if deadlines.days_left <= 0:
        kind, rung = "deadline_missed", MISSED_RUNG
    else:
        reached = [r for r in DEADLINE_RUNGS if deadlines.days_left <= r]
        if not reached:
            return
        kind, rung = "deadline_approaching", min(reached)
    if pipeline.receipts.has_rung(claim.id, kind, deadline_kind=deadlines.applicable, rung=rung):
        return
    deadline_date = getattr(deadlines, deadlines.applicable)
    record(
        pipeline,
        claim,
        kind,
        deadline_kind=deadlines.applicable,
        rung=rung,
        detail={"deadline_date": deadline_date.isoformat(), "days_left": deadlines.days_left},
    )
    announce(
        pipeline,
        claim,
        kind,
        deadline_kind=deadlines.applicable,
        deadline_date=datetime.combine(deadline_date, datetime.min.time()),
        days_left=deadlines.days_left,
    )
    summary.deadline_alerts += 1


def run_watchdog(
    pipeline: ClaimPipeline,
    *,
    payers: PayerRepository,
    practice_user_ids: Collection[str],
    limit: int = MAX_CLAIMS_PER_RUN,
) -> WatchdogSummary:
    """Stall what has timed out and climb the deadline ladder, for the principal's claims."""
    now = pipeline.now()
    today = now.date()
    summary = WatchdogSummary()
    for claim in pipeline.claims.list_by_state(OPEN_STATES, limit=limit):
        if not owned_by_principal(pipeline, claim, practice_user_ids):
            continue
        summary.checked += 1
        current = claim
        timeout = _timeout(claim, now) if claim.state not in TERMINAL_STATES else None
        if timeout is not None:
            code, description = timeout
            current = stall(pipeline, claim, code=code, description=description)
            logger.info(
                "claim_stalled control_number=%s from_state=%s code=%s",
                claim.control_number,
                claim.state,
                code,
            )
            summary.stalled += 1
        _deadline_alert(pipeline, current, payers, today=today, summary=summary)
    return summary
