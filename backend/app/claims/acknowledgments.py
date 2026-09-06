# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading a 277CA and moving the claim it names.

A 277CA arrives two ways — the clearinghouse's webhook names the
transaction, or the status poll finds it in the feed — and both end here.
:func:`fetch_acknowledgment` turns a transaction id into the parsed
acknowledgements (or ``None`` when the transaction is not an inbound
277); :func:`apply_acknowledgment` matches one acknowledgement to a claim
by the control number it echoes and moves the claim the way the state
table allows:

* the clearinghouse (``AY``) accepting means it has the claim and has
  passed it on: ``ch_accept``;
* the payer (``PR``) accepting means the payer has it: ``payer_accept``,
  from ``submitted`` or ``ch_accepted`` alike;
* either of them rejecting is ``reject``, with the status codes stored on
  the claim and announced as a ``rejected`` event.

An acknowledgement that confirms where the claim already is — a late
clearinghouse receipt after the payer has spoken, a second copy — is
recorded as a receipt and moves nothing. The same is true of a status
this code cannot read; the codes are kept for a person to look at.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from ..models.claims import SubmissionFinding
from .events import CodeRef
from .receipts import codes_detail, move, record, reject
from .responses import parse_277
from .transitions import TERMINAL_STATES, next_state

if TYPE_CHECKING:
    from ..models.claims import Claim
    from ..models.claims_responses import ClaimAcknowledgment
    from .clearinghouse import ClearinghouseClient
    from .receipts import ClaimPipeline
    from .transitions import ClaimEvent as Transition

logger = logging.getLogger(__name__)

ACKNOWLEDGMENT_TRANSACTION_SET = "277"

AcknowledgmentOutcome = Literal["moved", "recorded", "duplicate", "unmatched"]


@dataclass(frozen=True)
class FetchedAcknowledgment:
    """One inbound 277CA, parsed: which transaction it was and what it says."""

    transaction_id: str
    processed_at: datetime | None
    acknowledgments: list[ClaimAcknowledgment]

    @property
    def control_numbers(self) -> set[str]:
        return {ack.control_number.upper() for ack in self.acknowledgments}


def _processed_at(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_acknowledgment(
    client: ClearinghouseClient, transaction_id: str
) -> FetchedAcknowledgment | None:
    """The parsed 277CA behind ``transaction_id``, or ``None`` if it is not one.

    Raises the adapter's typed errors — a transaction another account
    owns is :class:`~app.claims.clearinghouse.ClearinghouseNotFoundError`.
    """
    document = client.get_transaction(transaction_id)
    if (
        document.direction != "INBOUND"
        or document.transaction_set != ACKNOWLEDGMENT_TRANSACTION_SET
    ):
        return None
    report = client.get_claim_acknowledgment(transaction_id)
    return FetchedAcknowledgment(
        transaction_id=transaction_id,
        processed_at=_processed_at(document.processedAt),
        acknowledgments=parse_277(report),
    )


def _status_codes(ack: ClaimAcknowledgment) -> tuple[CodeRef, ...]:
    return tuple(CodeRef(system="status", code=status.code) for status in ack.statuses)


def _findings(ack: ClaimAcknowledgment) -> list[SubmissionFinding]:
    return [
        SubmissionFinding(
            source="status",
            code=status.code,
            description=status.status_description or status.category_description or "",
        )
        for status in ack.statuses
    ]


def _accepting_transition(ack: ClaimAcknowledgment, claim: Claim) -> Transition | None:
    """Which hop an accepting acknowledgement is, or ``None`` if it moves nothing."""
    if ack.source == "payer":
        return "payer_accept" if next_state(claim.state, "payer_accept") else None
    if ack.source != "clearinghouse":
        return None
    # A clearinghouse receipt after the payer has spoken confirms nothing
    # new — and from ``stalled`` the table would happily step backwards.
    if claim.payer_accepted_at is not None:
        return None
    return "ch_accept" if next_state(claim.state, "ch_accept") else None


@dataclass(frozen=True)
class Provenance:
    """Where an acknowledgement came from, as the receipt records it."""

    transaction_id: str
    vendor_event_id: str | None = None
    occurred_at: datetime | None = None


def _record_only(
    pipeline: ClaimPipeline,
    claim: Claim,
    detail: dict[str, object],
    provenance: Provenance,
    *,
    heard_from_next_hop: bool,
) -> None:
    record(
        pipeline,
        claim,
        "acknowledged",
        detail=detail,
        vendor_event_id=provenance.vendor_event_id,
        vendor_transaction_id=provenance.transaction_id,
        occurred_at=provenance.occurred_at,
        touches_receipt_clock=heard_from_next_hop,
    )


def apply_acknowledgment(
    pipeline: ClaimPipeline,
    ack: ClaimAcknowledgment,
    provenance: Provenance,
) -> tuple[AcknowledgmentOutcome, Claim | None]:
    """Move the claim ``ack`` names, if the pipeline can see it and it moves.

    Idempotent on both the vendor's event id and its transaction id: a
    redelivered webhook, or a webhook for a 277CA the poll already
    applied, is ``duplicate`` and touches nothing.
    """
    claim = pipeline.claims.get_by_control_number(ack.control_number)
    if claim is None:
        return "unmatched", None
    event_id = provenance.vendor_event_id
    if (
        event_id is not None and pipeline.receipts.vendor_event_seen(event_id)
    ) or pipeline.receipts.vendor_transaction_seen(provenance.transaction_id):
        return "duplicate", claim

    codes = _status_codes(ack)
    detail: dict[str, object] = {
        **codes_detail(codes),
        "source": ack.source,
        "source_name": ack.source_name,
        "batch_number": ack.batch_number,
    }
    updates: dict[str, object] = {}
    if ack.payer_claim_number:
        updates["payer_claim_number"] = ack.payer_claim_number

    outcome = ack.outcome
    if outcome == "rejected" and claim.state not in TERMINAL_STATES:
        if next_state(claim.state, "reject") is None:
            _record_only(pipeline, claim, detail, provenance, heard_from_next_hop=False)
            return "recorded", claim
        if updates:
            claim = pipeline.claims.update(claim.model_copy(update=updates))
        rejected = reject(
            pipeline,
            claim,
            _findings(ack),
            vendor_event_id=provenance.vendor_event_id,
            vendor_transaction_id=provenance.transaction_id,
            occurred_at=provenance.occurred_at,
        )
        return "moved", rejected

    transition = _accepting_transition(ack, claim) if outcome == "accepted" else None
    if transition is None:
        if updates:
            claim = pipeline.claims.update(claim.model_copy(update=updates))
        _record_only(pipeline, claim, detail, provenance, heard_from_next_hop=True)
        return "recorded", claim

    kind: Literal["ch_accepted", "payer_accepted"] = (
        "payer_accepted" if transition == "payer_accept" else "ch_accepted"
    )
    moved = move(
        pipeline,
        claim,
        transition,
        kind=kind,
        detail=detail,
        updates=updates,
        vendor_event_id=provenance.vendor_event_id,
        vendor_transaction_id=provenance.transaction_id,
        occurred_at=provenance.occurred_at,
    )
    logger.info(
        "claim_acknowledged control_number=%s source=%s state=%s transaction=%s",
        moved.control_number,
        ack.source,
        moved.state,
        provenance.transaction_id,
    )
    return "moved", moved


def apply_fetched(
    pipeline: ClaimPipeline,
    fetched: FetchedAcknowledgment,
    *,
    vendor_event_id: str | None = None,
) -> list[tuple[AcknowledgmentOutcome, Claim | None]]:
    """Apply every acknowledgement in a fetched 277CA."""
    provenance = Provenance(
        transaction_id=fetched.transaction_id,
        vendor_event_id=vendor_event_id,
        occurred_at=fetched.processed_at,
    )
    return [apply_acknowledgment(pipeline, ack, provenance) for ack in fetched.acknowledgments]
