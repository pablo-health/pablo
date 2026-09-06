# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The clearinghouse operations a practice's own account can perform.

One vendor is implemented today (see ``app.claims.stedi``); this exists as a
``Protocol`` for portability, not because a second vendor is planned. A
deployment that needs a different clearinghouse implements this shape and
wires it in wherever the current implementation is constructed — the same
seam ``app.payments.provider`` uses for credentials.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
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

    def submit_claim(self, req: ClaimSubmissionRequest) -> ClaimSubmissionResult:
        """Submit a professional (837P) claim.

        Returns the synchronous accept-or-edit-reject response — the
        initial acknowledgement, not the payer's eventual adjudication.
        """
        ...

    def get_transaction(self, transaction_id: str) -> TransactionDocument:
        """Fetch one transaction (a submitted 837, an inbound 277CA or 835, ...)."""
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
