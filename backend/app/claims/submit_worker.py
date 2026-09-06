# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The outbox: file every ``validated`` claim, once.

Confirming a claim writes ``validated`` and returns. This worker, run on a
schedule, is what sends it: a bounded, oldest-first scan of the claims the
principal owns, one submission call each, and the state advanced only on
the clearinghouse's answer.

The one thing this module exists to get right is filing a claim exactly
once. The submission call is keyed: the caller mints an idempotency key
per attempt, and the vendor answers a repeat of the same key and body with
the original result instead of a second claim. So:

1. The key is minted and written on the claim, with a pending timestamp,
   and **committed** before the call is made. A crash anywhere after that
   leaves a claim whose row says an attempt is in flight.
2. The next run finds that claim and reconciles it before touching
   anything else. First it asks the feed whether the claim went out — an
   outbound 837 carrying the claim's control number since the marker was
   written — and if so records the filing without calling submit at all.
   If not, it resends with the **stored** key, which the vendor treats as
   the same attempt: a replay if the first call reached it, a first
   filing if it never did. The key is never re-minted for an attempt in
   flight, and never reused across claims — a corrected claim is a new
   row with its own control number and its own key.
3. A transient failure (timeout, 5xx, rate limit, a 409 for a key still
   being processed) leaves the marker in place for the next run. A refusal
   the vendor documents as permanent — a malformed request, an account
   not provisioned for the payer, a key reused with a different body —
   parks the claim as ``stalled`` with the reason, since sending it again
   unchanged would fail the same way; a person resolves it and files a
   corrected claim.

The synchronous answer is either an accept (the claim becomes
``submitted``, with the vendor's claim id) or an edit rejection (the claim
becomes ``rejected`` with the edits stored on it and announced as a
``rejected`` event, codes only). Nothing else moves a claim here.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

from ..models.claims import SubmissionFinding
from .clearinghouse import (
    ClearinghouseAccessDeniedError,
    ClearinghouseError,
    ClearinghouseInFlightError,
    ClearinghouseNotProvisionedError,
    ClearinghouseRateLimitedError,
    ClearinghouseRequestChangedError,
    ClearinghouseTransactionSettingError,
    ClearinghouseUnavailableError,
    ClearinghouseValidationError,
)
from .receipts import move, owned_by_principal, reject, stall
from .wire import ClaimMappingError, to_submission_request

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from ..models.claims import Claim
    from ..models.claims_transport import ClaimSubmissionRequest, TransactionDocument
    from ..repositories.coverage import PayerRepository
    from .clearinghouse import ClearinghouseClient
    from .receipts import ClaimPipeline

logger = logging.getLogger(__name__)

MAX_SUBMISSIONS_PER_RUN = 50

#: The feed excludes its start instant and buffers the last few seconds, so
#: the reconcile lookup starts a little before the pending marker.
_FEED_LOOKBACK = timedelta(minutes=2)
_FEED_MAX_PAGES = 5
_CLAIM_TRANSACTION_SET = "837"
_CONTROL_NUMBER_ELEMENT = "CLM-01"
_VENDOR_CLAIM_ID_ELEMENT = "BHT-03"

#: A failure the vendor documents as permanent, and the code a person sees.
_PERMANENT_REFUSALS: tuple[tuple[type[ClearinghouseError], str, str], ...] = (
    (ClearinghouseValidationError, "invalid_request", "The clearinghouse refused the request body"),
    (
        ClearinghouseNotProvisionedError,
        "not_provisioned",
        "The account is not enrolled with this payer for claims",
    ),
    (
        ClearinghouseTransactionSettingError,
        "transaction_unsupported",
        "The payer does not accept this transaction",
    ),
    (
        ClearinghouseRequestChangedError,
        "request_changed",
        "The submission key was reused with a different claim",
    ),
    (ClearinghouseAccessDeniedError, "access_denied", "The account's key may not file claims"),
)
_TRANSIENT_FAILURES = (
    ClearinghouseInFlightError,
    ClearinghouseUnavailableError,
    ClearinghouseRateLimitedError,
)


@dataclass(frozen=True)
class SubmissionAccount:
    """The per-account values every 837P from this practice carries."""

    usage_indicator: Literal["T", "P"]
    tax_id: str = field(repr=False)
    submitter_identification: str
    receiver_name: str


@dataclass
class SubmitSummary:
    submitted: int = 0
    reconciled: int = 0
    rejected: int = 0
    deferred: int = 0
    stalled: int = 0


def mint_idempotency_key(claim: Claim) -> str:
    """One key per filing attempt: the control number, the frequency, a nonce."""
    return f"{claim.control_number}:{claim.frequency_code}:{uuid.uuid4()}"


def submit_pending(  # noqa: PLR0913 — the run's collaborators, keyword-only
    pipeline: ClaimPipeline,
    client: ClearinghouseClient,
    account: SubmissionAccount,
    *,
    payers: PayerRepository,
    practice_user_ids: Collection[str],
    commit: Callable[[], None],
    limit: int = MAX_SUBMISSIONS_PER_RUN,
) -> SubmitSummary:
    """File the principal's ``validated`` claims, oldest first, at most ``limit``.

    ``commit`` makes the pending marker durable before each call; the
    session-owning caller passes its own commit. Claims with a marker
    already on them are reconciled, never re-minted.
    """
    summary = SubmitSummary()
    for claim in pipeline.claims.list_by_state(("validated",), limit=limit):
        if not owned_by_principal(pipeline, claim, practice_user_ids):
            continue
        if claim.submission_pending_at is not None:
            _reconcile(pipeline, client, account, claim, payers=payers, summary=summary)
            continue
        key = mint_idempotency_key(claim)
        marked = pipeline.claims.update(
            claim.model_copy(
                update={"submission_idempotency_key": key, "submission_pending_at": pipeline.now()}
            )
        )
        commit()
        _attempt(pipeline, client, account, marked, key, payers=payers, summary=summary)
    return summary


def _reconcile(  # noqa: PLR0913 — keyword-only collaborators
    pipeline: ClaimPipeline,
    client: ClearinghouseClient,
    account: SubmissionAccount,
    claim: Claim,
    *,
    payers: PayerRepository,
    summary: SubmitSummary,
) -> None:
    """A claim whose previous attempt never got its answer written down."""
    pending_at, key = claim.submission_pending_at, claim.submission_idempotency_key
    if pending_at is None or key is None:
        msg = f"claim {claim.control_number} has no pending attempt to reconcile"
        raise ValueError(msg)
    try:
        filed = _filed_in_feed(client, claim, since=pending_at - _FEED_LOOKBACK)
    except ClearinghouseError as exc:
        logger.info(
            "claim_reconcile_deferred control_number=%s error=%s",
            claim.control_number,
            type(exc).__name__,
        )
        summary.deferred += 1
        return
    if filed is not None:
        vendor_claim_ids = filed.identifier_values(_VENDOR_CLAIM_ID_ELEMENT)
        move(
            pipeline,
            claim,
            "submit",
            kind="submitted",
            detail={
                "reconciled": True,
                "correlation_id": vendor_claim_ids[0] if vendor_claim_ids else None,
            },
            vendor_transaction_id=filed.transactionId,
            updates={
                "submission_pending_at": None,
                "vendor_claim_id": vendor_claim_ids[0] if vendor_claim_ids else None,
            },
        )
        logger.info("claim_reconciled_from_feed control_number=%s", claim.control_number)
        summary.reconciled += 1
        return
    _attempt(pipeline, client, account, claim, key, payers=payers, summary=summary)


def _filed_in_feed(
    client: ClearinghouseClient, claim: Claim, *, since: datetime
) -> TransactionDocument | None:
    """The outbound 837 for ``claim`` processed since ``since``, if the vendor has one."""
    wanted = claim.control_number.upper()
    page_token: str | None = None
    for _ in range(_FEED_MAX_PAGES):
        page = client.list_transactions(start=since, page_token=page_token)
        for document in page.items:
            if document.direction != "OUTBOUND":
                continue
            if document.transaction_set != _CLAIM_TRANSACTION_SET:
                continue
            if wanted in {v.upper() for v in document.identifier_values(_CONTROL_NUMBER_ELEMENT)}:
                return document
        if not page.nextPageToken or not page.items:
            break
        page_token = page.nextPageToken
    return None


def _request_for(
    pipeline: ClaimPipeline, claim: Claim, account: SubmissionAccount, payers: PayerRepository
) -> ClaimSubmissionRequest:
    payer = payers.get(claim.payer_id)
    if payer is None:
        raise ClaimMappingError(["payer"])
    payer_claim_number = claim.payer_claim_number
    if payer_claim_number is None and claim.parent_claim_id is not None:
        parent = pipeline.claims.get(claim.parent_claim_id)
        payer_claim_number = parent.payer_claim_number if parent is not None else None
    return to_submission_request(
        claim,
        trading_partner_service_id=payer.clearinghouse_payer_id or payer.payer_id,
        usage_indicator=account.usage_indicator,
        tax_id=account.tax_id,
        submitter_identification=account.submitter_identification,
        receiver_name=account.receiver_name,
        payer_claim_number=payer_claim_number,
    )


def _attempt(  # noqa: PLR0913 — keyword-only collaborators
    pipeline: ClaimPipeline,
    client: ClearinghouseClient,
    account: SubmissionAccount,
    claim: Claim,
    key: str,
    *,
    payers: PayerRepository,
    summary: SubmitSummary,
) -> None:
    """One submission call under ``key``; the claim moves on the answer."""
    try:
        request = _request_for(pipeline, claim, account, payers)
    except ClaimMappingError as exc:
        stall(pipeline, claim, code="claim_incomplete", description=str(exc))
        summary.stalled += 1
        return

    try:
        result = client.submit_claim(request, idempotency_key=key)
    except _TRANSIENT_FAILURES as exc:
        logger.info(
            "claim_submit_deferred control_number=%s error=%s",
            claim.control_number,
            type(exc).__name__,
        )
        summary.deferred += 1
        return
    except ClearinghouseError as exc:
        code, description = _refusal(exc)
        logger.warning("claim_submit_refused control_number=%s code=%s", claim.control_number, code)
        stall(pipeline, claim, code=code, description=description)
        summary.stalled += 1
        return

    reference = result.claimReference
    if result.status == "SUCCESS":
        move(
            pipeline,
            claim,
            "submit",
            kind="submitted",
            detail={
                "reconciled": False,
                "correlation_id": reference.correlationId if reference else None,
                "trace_id": result.meta.traceId,
            },
            updates={
                "submission_pending_at": None,
                "submission_findings": [],
                "vendor_claim_id": reference.correlationId if reference else None,
            },
        )
        summary.submitted += 1
        return

    findings = [
        SubmissionFinding(
            source="edit",
            code=error.code,
            description=error.description,
            followup_action=error.followupAction,
        )
        for error in result.errors
    ]
    reject(pipeline, claim, findings)
    logger.info(
        "claim_submit_rejected control_number=%s edit_codes=%s",
        claim.control_number,
        sorted({f.code for f in findings}),
    )
    summary.rejected += 1


def _refusal(exc: ClearinghouseError) -> tuple[str, str]:
    for kind, code, description in _PERMANENT_REFUSALS:
        if isinstance(exc, kind):
            return code, description
    return "clearinghouse_refused", "The clearinghouse refused the submission"
