# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The one clearinghouse implementation this codebase ships: Stedi.

JSON in, JSON out — no raw X12 assembly and no SFTP drop, because Stedi's
JSON-in/JSON-out API does that translation for us. ``app.claims.clearinghouse``
exists as a ``Protocol`` for portability, not because a second vendor is
planned; this module is the only implementation.

Three hosts, because Stedi splits its healthcare API across them:

* ``healthcare.us.stedi.com`` — eligibility and claim submission.
* ``payers.us.stedi.com`` — the payer directory search.
* ``core.us.stedi.com`` — the generic transaction-polling API (used to fetch
  the inbound 277CA/835 that follow a submission).
* ``enrollments.us.stedi.com`` — provider registration and payer enrollment.

Idempotency: eligibility, payer search, and transaction/enrollment reads are
side-effect-free, so they retry any transient failure
(``Idempotency.SAFE``). Claim submission is deduped server-side by the
``Idempotency-Key`` header, which the caller mints and persists before the
call (one key per submission attempt) and ``submit_claim`` sends. For 24
hours after the first request the vendor keys on it: the same key with the
same body replays the original response (same ``correlationId``, no second
claim filed); the same key with a different body is refused with ``422
REQUEST_CHANGED``; a repeat while the original is still being processed is
``409`` with a ``Retry-After``. That contract is what lets submission run as
``Idempotency.KEYED`` — a timeout or 5xx that might have reached Stedi is
retried with the same key, and the replay is safe. The 409 is deliberately
not retried here; it surfaces as ``ClearinghouseInFlightError`` and the
caller decides when to resend. Provider registration and enrollment creation
carry no such key and stay ``Idempotency.UNSAFE``: only a failure that never
reached the network (DNS, connection refused) is retried automatically.

Logging here is limited to what the module docstring for ``app.claims``
allows: claim control numbers, claim/transaction state, payer id, trace id,
and CARC/RARC-style edit codes. Never the API key, never a request or
response body.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NoReturn

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

    from .credentials import ClearinghouseCredentials

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
from ..reliability import HTTP_REQUEST, Idempotency, RetryExhaustedError, call_with_retry
from .clearinghouse import (
    ClearinghouseAccessDeniedError,
    ClearinghouseInFlightError,
    ClearinghouseNotProvisionedError,
    ClearinghouseRateLimitedError,
    ClearinghouseRequestChangedError,
    ClearinghouseTransactionSettingError,
    ClearinghouseUnavailableError,
    ClearinghouseValidationError,
)

logger = logging.getLogger(__name__)

HEALTHCARE_API_BASE = "https://healthcare.us.stedi.com/2024-04-01"
PAYERS_API_BASE = "https://payers.us.stedi.com/2024-04-01"
CORE_API_BASE = "https://core.us.stedi.com/2023-08-01"
ENROLLMENTS_API_BASE = "https://enrollments.us.stedi.com/2024-09-01"

_REQUEST_TIMEOUT_SECONDS = 20.0

#: Comfortably inside the vendor's accepted 10-100 range (below 10 is a 400).
_DEFAULT_PAYER_SEARCH_PAGE_SIZE = 25

_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_BAD_REQUEST = 400
_HTTP_FORBIDDEN = 403
_HTTP_CONFLICT = 409
_HTTP_UNPROCESSABLE = 422

#: Error codes the vendor's JSON error envelope (``{"code": ..., "message": ...}``)
#: uses for the failure modes this adapter has typed exceptions for.
_INVALID_REQUEST_BODY = "INVALID_REQUEST_BODY"
_ACCOUNT_NOT_PROVISIONED = "ACCOUNT_NOT_PROVISIONED"
_REQUEST_CHANGED = "REQUEST_CHANGED"

_IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """The vendor's ``Retry-After`` hint, seconds only (the HTTP-date form is not used)."""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _raise_for_error_envelope(response: httpx.Response) -> NoReturn:
    """Translate a non-2xx response into one of this module's typed exceptions.

    A claim submission's 400 edit-rejection is deliberately NOT handled here
    — it has its own well-formed ``ClaimSubmissionResult`` shape (``status:
    "ERROR"``, an ``errors`` array) and is a business answer, not a transport
    failure. Callers of ``submit_claim`` parse that shape directly and never
    reach this function for it.

    Only a 5xx (or a status this function has no name for) becomes
    ``ClearinghouseUnavailableError``; every 4xx the vendor documents has its
    own type so a caller can tell "fix the request" from "wait" from "this
    key can't do that".
    """
    if response.status_code == _HTTP_TOO_MANY_REQUESTS:
        raise ClearinghouseRateLimitedError(f"rate limited: {response.status_code}")

    body: dict[str, Any] | None = None
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            body = parsed
    except ValueError:
        body = None

    code = body.get("code") if body else None
    message = str(body.get("message", "")) if body else ""

    if code == _ACCOUNT_NOT_PROVISIONED:
        raise ClearinghouseNotProvisionedError(message or "account not provisioned")
    if code == _INVALID_REQUEST_BODY:
        raise ClearinghouseValidationError(message or "invalid request body")
    if response.status_code == _HTTP_UNPROCESSABLE or code == _REQUEST_CHANGED:
        raise ClearinghouseRequestChangedError(
            message or "idempotency key reused with a different request"
        )
    if response.status_code == _HTTP_FORBIDDEN:
        raise ClearinghouseAccessDeniedError(message or "access denied")
    if response.status_code == _HTTP_CONFLICT:
        raise ClearinghouseInFlightError(
            message or "a request with this idempotency key is still in flight",
            retry_after=_retry_after_seconds(response),
        )
    if response.status_code == _HTTP_BAD_REQUEST and "transaction setting" in message.lower():
        raise ClearinghouseTransactionSettingError(message)
    if response.status_code == _HTTP_BAD_REQUEST:
        raise ClearinghouseValidationError(message or "bad request")

    raise ClearinghouseUnavailableError(
        f"unexpected clearinghouse response: {response.status_code}"
    )


def _is_claim_result_envelope(body: object) -> bool:
    """Is this a claim submission's own accept/edit-reject shape?

    Distinguishes a business answer (parse it) from a generic vendor error
    envelope (raise a typed exception) on a 400 — both are plausible bodies
    for the submission endpoint's non-2xx responses.
    """
    return (
        isinstance(body, dict)
        and body.get("status") in ("SUCCESS", "ERROR")
        and "meta" in body
        and "payer" in body
    )


class StediClearinghouseClient:
    """``ClearinghouseClient`` backed by Stedi's JSON healthcare API.

    ``client`` is an injectable ``httpx.Client`` so tests can swap in
    ``httpx.MockTransport`` — no network in this module's own test suite.
    The default client is built once per instance, not per call, so
    connection reuse works the same way it would in production.
    """

    def __init__(
        self,
        credentials: ClearinghouseCredentials,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._credentials = credentials
        self._client = client or httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Key {self._credentials.api_key}"}

    def _send(
        self, do_call: Callable[[], httpx.Response], *, idempotency: Idempotency, url: str
    ) -> httpx.Response:
        """Run one request through the retry engine.

        Always returns a ``Response`` — a 2xx from a call that succeeded (on
        the first attempt or a retry), or the final non-2xx response from a
        call that exhausted its retry budget or hit a status the policy
        doesn't retry (a 400 is never retried). Callers inspect
        ``status_code`` themselves rather than this helper deciding what
        counts as failure, because a 400 means different things on different
        endpoints (see ``submit_claim``'s edit-rejection handling).
        """

        def _call() -> httpx.Response:
            response = do_call()
            response.raise_for_status()
            return response

        try:
            return call_with_retry(_call, policy=HTTP_REQUEST, idempotency=idempotency)
        except RetryExhaustedError as exc:
            if isinstance(exc.last_exc, httpx.HTTPStatusError):
                return exc.last_exc.response
            logger.error("clearinghouse_unreachable url=%s err=%s", url, exc.last_exc)
            raise ClearinghouseUnavailableError(str(exc.last_exc)) from exc
        except httpx.HTTPStatusError as exc:
            return exc.response
        except httpx.RequestError as exc:
            logger.error("clearinghouse_request_failed url=%s err=%s", url, exc)
            raise ClearinghouseUnavailableError(str(exc)) from exc

    def _get(self, url: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        return self._send(
            lambda: self._client.get(url, params=params, headers=self._headers()),
            idempotency=Idempotency.SAFE,
            url=url,
        )

    def _post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        idempotency: Idempotency,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        merged = {**self._headers(), **(headers or {})}
        return self._send(
            lambda: self._client.post(url, json=json, headers=merged),
            idempotency=idempotency,
            url=url,
        )

    def search_payers(self, query: str) -> list[Payer]:
        response = self._get(
            f"{PAYERS_API_BASE}/payers/search",
            params={"query": query, "pageSize": _DEFAULT_PAYER_SEARCH_PAGE_SIZE},
        )
        if response.status_code != httpx.codes.OK:
            _raise_for_error_envelope(response)
        body = response.json()
        return [Payer.model_validate(item["payer"]) for item in body.get("items", [])]

    def check_eligibility(self, req: EligibilityRequest) -> EligibilityResponse:
        response = self._post(
            f"{HEALTHCARE_API_BASE}/change/medicalnetwork/eligibility/v3",
            json=req.model_dump(exclude_none=True),
            idempotency=Idempotency.SAFE,
        )
        if response.status_code != httpx.codes.OK:
            _raise_for_error_envelope(response)
        logger.info(
            "clearinghouse_eligibility_checked payer_id=%s",
            req.tradingPartnerServiceId,
        )
        return EligibilityResponse.model_validate(response.json())

    def submit_claim(
        self, req: ClaimSubmissionRequest, *, idempotency_key: str
    ) -> ClaimSubmissionResult:
        response = self._post(
            f"{HEALTHCARE_API_BASE}/change/medicalnetwork/professionalclaims/v3/submission",
            json=req.model_dump(exclude_none=True),
            idempotency=Idempotency.KEYED,
            headers={_IDEMPOTENCY_KEY_HEADER: idempotency_key},
        )
        body = None
        try:
            parsed = response.json()
            body = parsed if _is_claim_result_envelope(parsed) else None
        except ValueError:
            body = None

        if body is None:
            _raise_for_error_envelope(response)

        result = ClaimSubmissionResult.model_validate(body)
        logger.info(
            "clearinghouse_claim_submitted status=%s control_number=%s payer_id=%s",
            result.status,
            result.controlNumber,
            result.payer.payerId,
        )
        return result

    def get_transaction(self, transaction_id: str) -> TransactionDocument:
        response = self._get(f"{CORE_API_BASE}/transactions/{transaction_id}")
        if response.status_code != httpx.codes.OK:
            _raise_for_error_envelope(response)
        return TransactionDocument.model_validate(response.json())

    def create_provider(self, provider: ProviderRegistration) -> ProviderRecord:
        response = self._post(
            f"{ENROLLMENTS_API_BASE}/providers",
            json=provider.model_dump(exclude_none=True),
            idempotency=Idempotency.UNSAFE,
        )
        if response.status_code != httpx.codes.OK:
            _raise_for_error_envelope(response)
        return ProviderRecord.model_validate(response.json())

    def create_enrollment(self, enrollment: EnrollmentRequest) -> Enrollment:
        response = self._post(
            f"{ENROLLMENTS_API_BASE}/enrollments",
            json=enrollment.model_dump(exclude_none=True),
            idempotency=Idempotency.UNSAFE,
        )
        if response.status_code != httpx.codes.OK:
            _raise_for_error_envelope(response)
        return Enrollment.model_validate(response.json())

    def list_enrollments(self, filters: EnrollmentFilters) -> list[Enrollment]:
        response = self._get(
            f"{ENROLLMENTS_API_BASE}/enrollments",
            params=filters.model_dump(exclude_none=True),
        )
        if response.status_code != httpx.codes.OK:
            _raise_for_error_envelope(response)
        return [Enrollment.model_validate(item) for item in response.json().get("items", [])]
