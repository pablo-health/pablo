# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Moving a claim on a receipt, and writing the receipt down.

Everything that advances a claim after it was validated — the outbox
worker, the acknowledgement paths, the watchdog — goes through the three
verbs here so the claim row, its receipt ledger and the person who needs
to know all move together:

* :func:`move` runs the state table (:mod:`app.claims.transitions`),
  writes the claim back and records the hop as a :class:`ClaimReceipt`;
* :func:`record` writes a receipt that moved nothing — an acknowledgement
  that confirmed where the claim already was, a status check that found
  nothing new, a deadline alert;
* :func:`announce` hands a :class:`~app.claims.events.ClaimEvent` to the
  listeners, addressed to the principal the pipeline runs as.

A :class:`ClaimPipeline` is the bundle those verbs need: the two
repositories, the session the event listeners write in, the clinician the
session is armed as, and a clock. The principal matters twice — the row
policies on ``claims`` and ``claim_events`` decide what the pipeline can
see, and the default event listener writes its reminder under that
person's own policy — so a worker opens one pipeline per clinician and
handles only the claims they own (see :func:`owned_by_principal`).

Receipt details carry codes and vendor identifiers only. Never a member
id, a diagnosis or a name; the claim row holds those.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..models.claims import ClaimReceipt, SubmissionFinding
from ..utcnow import utc_now
from .events import ClaimEvent, ClaimEventDetail, ClaimEventKind, CodeRef, emit
from .transitions import advance

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence
    from datetime import datetime

    from sqlalchemy.orm import Session

    from ..models.claims import Claim, ClaimReceiptKind
    from ..repositories.claim_receipts import ClaimReceiptRepository
    from ..repositories.claims import ClaimRepository
    from .events import DeadlineKind
    from .transitions import ClaimEvent as Transition


@dataclass
class ClaimPipeline:
    """What every claims worker needs, bound to one clinician's session."""

    claims: ClaimRepository
    receipts: ClaimReceiptRepository
    session: Session
    principal_user_id: str
    now: Callable[[], datetime] = field(default=utc_now)


def owned_by_principal(
    pipeline: ClaimPipeline, claim: Claim, practice_user_ids: Collection[str]
) -> bool:
    """Is this claim the pipeline's to handle?

    The owner (the rendering clinician) handles their own claims, so the
    reminder a rejection writes lands on the right dashboard. A claim whose
    owner is no longer a member of the practice is handled by whichever
    clinician still sees it, rather than never.
    """
    owner = claim.owner_user_id
    return owner == pipeline.principal_user_id or owner not in practice_user_ids


def codes_detail(codes: Sequence[CodeRef]) -> dict[str, object]:
    """The code-only view of ``codes`` a receipt's ``detail`` carries."""
    return {"codes": [{"system": code.system, "code": code.code} for code in codes]}


def finding_codes(findings: Sequence[SubmissionFinding]) -> tuple[CodeRef, ...]:
    """The findings as code references — codes only, no descriptions.

    The description of an edit can quote the value at fault, which is why
    it stays on the claim row and out of everything a listener may forward.
    """
    return tuple(CodeRef(system=f.source, code=f.code) for f in findings)


def move(  # noqa: PLR0913 — the receipt's fields, keyword-only
    pipeline: ClaimPipeline,
    claim: Claim,
    transition: Transition,
    *,
    kind: ClaimReceiptKind,
    detail: dict[str, object] | None = None,
    vendor_event_id: str | None = None,
    vendor_transaction_id: str | None = None,
    occurred_at: datetime | None = None,
    updates: dict[str, object] | None = None,
) -> Claim:
    """Advance the claim by ``transition``, save it, and record the hop.

    ``updates`` are extra columns the receipt carries onto the row (the
    vendor's claim id, the pending marker being cleared). Raises
    :class:`~app.claims.transitions.InvalidTransitionError` when the state
    table refuses; nothing is written then.
    """
    now = pipeline.now()
    moved = advance(claim, transition, now=now)
    moved = moved.model_copy(update={"last_receipt_at": occurred_at or now, **(updates or {})})
    saved = pipeline.claims.update(moved)
    pipeline.receipts.add(
        ClaimReceipt(
            id=str(uuid.uuid4()),
            claim_id=saved.id,
            kind=kind,
            from_state=claim.state,
            to_state=saved.state,
            vendor_event_id=vendor_event_id,
            vendor_transaction_id=vendor_transaction_id,
            detail=detail or {},
            occurred_at=occurred_at or now,
        )
    )
    return saved


def record(  # noqa: PLR0913 — the receipt's fields, keyword-only
    pipeline: ClaimPipeline,
    claim: Claim,
    kind: ClaimReceiptKind,
    *,
    detail: dict[str, object] | None = None,
    deadline_kind: DeadlineKind | None = None,
    rung: int | None = None,
    vendor_event_id: str | None = None,
    vendor_transaction_id: str | None = None,
    occurred_at: datetime | None = None,
    touches_receipt_clock: bool = False,
) -> ClaimReceipt:
    """Record a receipt that moves the claim nowhere.

    ``touches_receipt_clock`` says whether this counts as hearing from the
    next hop (an acknowledgement does; a status check that found nothing
    and a deadline alert do not), which is what the watchdog's timeouts
    read.
    """
    now = pipeline.now()
    if touches_receipt_clock:
        pipeline.claims.update(claim.model_copy(update={"last_receipt_at": occurred_at or now}))
    return pipeline.receipts.add(
        ClaimReceipt(
            id=str(uuid.uuid4()),
            claim_id=claim.id,
            kind=kind,
            from_state=claim.state,
            to_state=claim.state,
            deadline_kind=deadline_kind,
            rung=rung,
            vendor_event_id=vendor_event_id,
            vendor_transaction_id=vendor_transaction_id,
            detail=detail or {},
            occurred_at=occurred_at or now,
        )
    )


def announce(  # noqa: PLR0913 — the event's fields, keyword-only
    pipeline: ClaimPipeline,
    claim: Claim,
    kind: ClaimEventKind,
    *,
    codes: Sequence[CodeRef] = (),
    deadline_kind: DeadlineKind | None = None,
    deadline_date: datetime | None = None,
    days_left: int | None = None,
) -> None:
    """Tell the listeners something about ``claim`` needs a person.

    Addressed to the pipeline's principal — the clinician whose session
    this is — since the default listener writes its reminder under that
    person's row policy and nobody else's.
    """
    emit(
        pipeline.session,
        ClaimEvent(
            kind=kind,
            control_number=claim.control_number,
            claim_id=claim.id,
            user_id=pipeline.principal_user_id,
            payer_id=claim.subscriber_snapshot.payer_id,
            payer_name=claim.subscriber_snapshot.payer_name,
            state=claim.state,
            occurred_at=pipeline.now(),
            detail=ClaimEventDetail(
                codes=tuple(codes),
                deadline_kind=deadline_kind,
                deadline_date=deadline_date.date() if deadline_date else None,
                days_left=days_left,
            ),
        ),
    )


def reject(  # noqa: PLR0913 — the receipt's provenance, keyword-only
    pipeline: ClaimPipeline,
    claim: Claim,
    findings: Sequence[SubmissionFinding],
    *,
    vendor_event_id: str | None = None,
    vendor_transaction_id: str | None = None,
    occurred_at: datetime | None = None,
) -> Claim:
    """The clearinghouse or the payer refused the claim: store why, move, announce."""
    codes = finding_codes(findings)
    rejected = move(
        pipeline,
        claim,
        "reject",
        kind="rejected",
        detail=codes_detail(codes),
        vendor_event_id=vendor_event_id,
        vendor_transaction_id=vendor_transaction_id,
        occurred_at=occurred_at,
        updates={"submission_findings": list(findings), "submission_pending_at": None},
    )
    announce(pipeline, rejected, "rejected", codes=codes)
    return rejected


def stall(pipeline: ClaimPipeline, claim: Claim, *, code: str, description: str) -> Claim:
    """Nobody has heard from the next hop in time: park the claim and say so.

    ``description`` is the state and the age in words — never an
    identifier — and is what the reminder shows.
    """
    codes = (CodeRef(system="status", code=code, description=description),)
    stalled = move(
        pipeline,
        claim,
        "stall",
        kind="stalled",
        detail={**codes_detail(codes), "reason": description},
        updates={"submission_pending_at": None},
    )
    announce(pipeline, stalled, "stalled", codes=codes)
    return stalled
