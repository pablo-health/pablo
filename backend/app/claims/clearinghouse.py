# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The clearinghouse operations a practice's own account can perform.

One vendor is implemented today (see ``app.claims.stedi``); this exists as a
``Protocol`` for portability, not because a second vendor is planned. A
deployment that needs a different clearinghouse implements this shape and
wires it in wherever the current implementation is constructed — the same
seam ``app.payments.provider`` uses for credentials.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from ..models.claims_transport import (
        ClaimSubmissionRequest,
        ClaimSubmissionResult,
        EligibilityRequest,
        EligibilityResponse,
        Enrollment,
        EnrollmentFilters,
        EnrollmentRequest,
        Payer,
        ProviderRecord,
        ProviderRegistration,
        TransactionDocument,
        TransactionPage,
    )


class ClearinghouseError(Exception):
    """Base for every typed error the adapter raises."""


class ClearinghouseValidationError(ClearinghouseError):
    """The vendor rejected the request body as malformed (``INVALID_REQUEST_BODY``).

    A bug in the caller's request assembly, not a transient failure — retrying
    unchanged would fail identically.
    """


class ClearinghouseNotProvisionedError(ClearinghouseError):
    """The account is not provisioned for this payer or transaction (``ACCOUNT_NOT_PROVISIONED``).

    Filing with this payer needs an enrollment first (see
    ``create_enrollment``); this is not a request-shape problem.
    """


class ClearinghouseTransactionSettingError(ClearinghouseError):
    """The payer does not support the requested transaction at all.

    The vendor reports this as a 400 whose message names the unsupported
    "transaction setting" rather than a request-field problem — check the
    payer's ``transactionSupport`` (via ``search_payers``) before retrying
    with a different transaction type.
    """


class ClearinghouseRateLimitedError(ClearinghouseError):
    """The vendor answered 429 after the retry budget was exhausted."""


class ClearinghouseRequestChangedError(ClearinghouseError):
    """An idempotency key was reused with a different body (422 ``REQUEST_CHANGED``).

    Within the vendor's replay window the same ``Idempotency-Key`` must carry
    the same request. A corrected claim is a new submission and needs a
    fresh key from the caller; resending unchanged fails identically.
    """


class ClearinghouseAccessDeniedError(ClearinghouseError):
    """The account's key may not use this API at all (403 ``access_denied``).

    Neither transient nor a request-shape problem — the enrollment API, for
    one, refuses test-mode keys outright.
    """


class ClearinghouseInFlightError(ClearinghouseError):
    """A request with this idempotency key is still being processed (409).

    ``retry_after`` is the vendor's ``Retry-After`` hint in seconds when it
    sent one. Re-issue the same key and body after it; the replay answers
    with the original result. The adapter never waits or retries this
    itself — the caller owns that decision.
    """

    def __init__(self, message: str, *, retry_after: float | None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ClearinghouseNotFoundError(ClearinghouseError):
    """The vendor has no such record (404): a transaction id this account
    never produced, or one that belongs to a different account."""


class ClearinghouseUnavailableError(ClearinghouseError):
    """The call could not be completed: a network failure, a timeout, or a
    5xx that survived the retry budget."""


class ClearinghouseClient(Protocol):
    """One practice's clearinghouse account: eligibility, claims, enrollment."""

    def search_payers(self, query: str) -> list[Payer]:
        """Find payers by name, id, or alias."""
        ...

    def check_eligibility(self, req: EligibilityRequest) -> EligibilityResponse:
        """Run a real-time eligibility check (270/271)."""
        ...

    def submit_claim(
        self, req: ClaimSubmissionRequest, *, idempotency_key: str
    ) -> ClaimSubmissionResult:
        """Submit a professional (837P) claim.

        Returns the synchronous accept-or-edit-reject response — the
        initial acknowledgement, not the payer's eventual adjudication.

        ``idempotency_key`` is minted and persisted by the caller before the
        call, one per submission attempt, and is what makes a resend after a
        timeout safe: the vendor answers a repeat of the same key and body
        with the original result instead of filing a second claim.
        """
        ...

    def get_transaction(self, transaction_id: str) -> TransactionDocument:
        """Fetch one transaction (a submitted 837, an inbound 277CA or 835, ...)."""
        ...

    def list_transactions(
        self, *, start: datetime | None = None, page_token: str | None = None
    ) -> TransactionPage:
        """One page of the account's transaction feed, oldest first.

        The first call names ``start`` (which the vendor requires to be at
        least a minute in the past); each following call passes the page
        token the previous page returned.
        """
        ...

    def get_claim_acknowledgment(self, transaction_id: str) -> dict[str, Any]:
        """The 277CA behind an inbound ``277`` transaction, as the vendor's JSON.

        Parsed by ``app.claims.responses.parse_277``; the raw document is
        returned so the parser stays the one place that reads it.
        """
        ...

    def create_provider(self, provider: ProviderRegistration) -> ProviderRecord:
        """Register a billing provider with the clearinghouse."""
        ...

    def create_enrollment(self, enrollment: EnrollmentRequest) -> Enrollment:
        """Enroll a provider for a transaction (e.g. claim payment/835) with a payer."""
        ...

    def list_enrollments(self, filters: EnrollmentFilters) -> list[Enrollment]:
        """List this account's enrollments, optionally filtered."""
        ...
