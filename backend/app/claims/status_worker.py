# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The poll backstop: ask the feed about claims nobody has acknowledged.

Acknowledgements normally arrive by webhook. When they do not — the
destination was not configured, a delivery was lost past its retries —
this worker reads the transaction feed for the claims still waiting and
applies whatever 277CA it finds through the same path the webhook uses
(:mod:`app.claims.acknowledgments`), so the two can never disagree.

Bounded on every axis: only claims still waiting for a hop and not asked
about in the last hour; one feed read per run, starting at the oldest such
claim's submission and never further back than the vendor keeps; a page
cap; and a 277CA is fetched only when its business identifiers name one
of the waiting claims (or name nothing, in which case it is read to be
safe). :func:`check_status` is the same read for one claim, run when a
person asks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .acknowledgments import (
    ACKNOWLEDGMENT_TRANSACTION_SET,
    FetchedAcknowledgment,
    apply_fetched,
)
from .receipts import owned_by_principal, record
from .responses import parse_277

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator

    from ..models.claims import Claim
    from ..models.claims_transport import TransactionDocument
    from .clearinghouse import ClearinghouseClient
    from .receipts import ClaimPipeline

logger = logging.getLogger(__name__)

#: States a claim waits in for the next acknowledgement.
AWAITING_STATES: tuple[str, ...] = ("submitted", "ch_accepted", "payer_accepted", "stalled")

POLL_AFTER = timedelta(hours=1)
MAX_CLAIMS_PER_RUN = 200
_FEED_LOOKBACK = timedelta(minutes=2)
#: The vendor keeps the feed for a bounded window; a claim older than this
#: with no acknowledgement is the watchdog's to flag, not the poll's to find.
_FEED_MAX_AGE = timedelta(days=45)
_FEED_MAX_PAGES = 10
_CONTROL_NUMBER_ELEMENT = "TRN-02"


@dataclass
class PollSummary:
    checked: int = 0
    acknowledgments_read: int = 0
    moved: int = 0


def _feed(
    client: ClearinghouseClient, *, start: datetime, max_pages: int = _FEED_MAX_PAGES
) -> Iterator[TransactionDocument]:
    page_token: str | None = None
    for _ in range(max_pages):
        page = client.list_transactions(start=start, page_token=page_token)
        yield from page.items
        if not page.nextPageToken or not page.items:
            return
        page_token = page.nextPageToken


def _names_one_of(document: TransactionDocument, control_numbers: set[str]) -> bool:
    """Does the transaction's envelope name a waiting claim (or name nothing)?"""
    echoed = {value.upper() for value in document.identifier_values(_CONTROL_NUMBER_ELEMENT)}
    return not echoed or bool(echoed & control_numbers)


def _apply_feed(
    pipeline: ClaimPipeline,
    client: ClearinghouseClient,
    *,
    start: datetime,
    control_numbers: set[str],
    summary: PollSummary,
) -> None:
    for document in _feed(client, start=start):
        if document.direction != "INBOUND":
            continue
        if document.transaction_set != ACKNOWLEDGMENT_TRANSACTION_SET:
            continue
        if pipeline.receipts.vendor_transaction_seen(document.transactionId):
            continue
        if not _names_one_of(document, control_numbers):
            continue
        fetched = FetchedAcknowledgment(
            transaction_id=document.transactionId,
            processed_at=_processed_at(document.processedAt),
            acknowledgments=parse_277(client.get_claim_acknowledgment(document.transactionId)),
        )
        summary.acknowledgments_read += 1
        for outcome, _claim in apply_fetched(pipeline, fetched):
            if outcome == "moved":
                summary.moved += 1


def _processed_at(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _due(claim: Claim, now: datetime) -> bool:
    asked = claim.status_checked_at or claim.last_receipt_at or claim.submitted_at
    return asked is None or now - asked >= POLL_AFTER


def _feed_start(claims: list[Claim], now: datetime) -> datetime:
    earliest = min(claim.submitted_at or claim.created_at for claim in claims)
    return max(earliest - _FEED_LOOKBACK, now - _FEED_MAX_AGE)


def _mark_checked(pipeline: ClaimPipeline, claims: list[Claim], now: datetime) -> None:
    for stale in claims:
        current = pipeline.claims.get(stale.id)
        if current is not None:
            pipeline.claims.update(current.model_copy(update={"status_checked_at": now}))


def poll_acknowledgments(
    pipeline: ClaimPipeline,
    client: ClearinghouseClient,
    *,
    practice_user_ids: Collection[str],
    limit: int = MAX_CLAIMS_PER_RUN,
) -> PollSummary:
    """Read the feed for the principal's waiting claims and apply what it says."""
    now = pipeline.now()
    waiting = [
        claim
        for claim in pipeline.claims.list_by_state(AWAITING_STATES, limit=limit)
        if owned_by_principal(pipeline, claim, practice_user_ids) and _due(claim, now)
    ]
    summary = PollSummary(checked=len(waiting))
    if not waiting:
        return summary
    _apply_feed(
        pipeline,
        client,
        start=_feed_start(waiting, now),
        control_numbers={claim.control_number.upper() for claim in waiting},
        summary=summary,
    )
    _mark_checked(pipeline, waiting, now)
    return summary


def check_status(pipeline: ClaimPipeline, client: ClearinghouseClient, claim: Claim) -> Claim:
    """Read the feed for one claim because a person asked; the claim as it stands after.

    A check that finds nothing new records a ``status_checked`` receipt so
    the tracker shows when somebody last looked.
    """
    now = pipeline.now()
    summary = PollSummary(checked=1)
    if claim.state in AWAITING_STATES:
        _apply_feed(
            pipeline,
            client,
            start=_feed_start([claim], now),
            control_numbers={claim.control_number.upper()},
            summary=summary,
        )
    refreshed = pipeline.claims.get(claim.id)
    if refreshed is None:
        msg = f"claim {claim.id!r} vanished during the status check"
        raise LookupError(msg)
    if summary.moved == 0:
        record(
            pipeline,
            refreshed,
            "status_checked",
            detail={"acknowledgments_read": summary.acknowledgments_read},
        )
    return pipeline.claims.update(refreshed.model_copy(update={"status_checked_at": now}))
