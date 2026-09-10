# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The one clearinghouse implementation this codebase ships: Stedi.

JSON in, JSON out — no raw X12 assembly and no SFTP drop, because Stedi's
JSON-in/JSON-out API does that translation for us. ``app.claims.clearinghouse``
exists as a ``Protocol`` for portability, not because a second vendor is
planned; this module is the only implementation.

Three hosts, because Stedi splits its healthcare API across them:

* ``healthcare.us.stedi.com`` — eligibility.
* ``payers.us.stedi.com`` — the payer directory search.
* ``core.us.stedi.com`` — the generic transaction-polling API (used to fetch
  the inbound 277CA/835 that follow a submission); the 277CA itself is read
  as JSON from the healthcare host's report endpoint.
* ``enrollments.us.stedi.com`` — provider registration and payer enrollment.

Those four are the default and are what every real deployment talks to. A
deployment that has to be answered by something else — the end-to-end
harness and its stand-in clearinghouse, a recording proxy — says so once, in
the credentials the adapter is constructed with
(``ClearinghouseCredentials.base_url``), and all four are then served from
that one origin under the same version paths. Where to point the adapter is
a property of the deployment's clearinghouse account, which is why it rides
with the credentials rather than on a second configuration path of its own.

Claim submission does not go through the hosts above. It goes to the
vendor's own claim API through its SDK, because that API is the only one
that keeps a per-claim history: a claim filed through the compatibility
endpoint cannot afterwards be asked what happened to it. What comes back is
the vendor's claim id, which stays the same across resubmissions and is what
its acknowledgements and remittances are matched against.

Idempotency: eligibility, payer search, and transaction/enrollment reads are
side-effect-free, so they retry any transient failure (``Idempotency.SAFE``).
Claim submission is deduped server-side by an idempotency key the caller
mints and persists before the call (one per submission attempt), which
travels on the request body. Verified against the vendor 2026-09-09: the
same key with the same claim replays the original answer — the same claim
id, no second claim filed — while the same key with a different claim is
refused. That is what makes a retry after a timeout safe. A repeat while the
original is still in flight surfaces as ``ClearinghouseInFlightError`` and is
deliberately not retried here; the caller decides when to resend. Provider
registration and enrollment creation carry no such key and stay
``Idempotency.UNSAFE``: only a failure that never reached the network (DNS,
connection refused) is retried automatically.

Logging here is limited to what the module docstring for ``app.claims``
allows: claim control numbers, claim/transaction state, payer id, trace id,
and CARC/RARC-style edit codes. Never the API key, never a request or
response body.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING, Any, NoReturn
from urllib.parse import urlsplit

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from .credentials import ClearinghouseCredentials

from ..models.claims_transport import (
    ClaimSubmissionRequest,
    ClaimSubmissionResult,
    EligibilityRequest,
    EligibilityResponse,
    Enrollment,
    EnrollmentFilters,
    EnrollmentPage,
    EnrollmentRequest,
    Payer,
    ProviderRecord,
    ProviderRegistration,
    TransactionDocument,
    TransactionPage,
)
from ..reliability import HTTP_REQUEST, Idempotency, RetryExhaustedError, call_with_retry
from .clearinghouse import (
    ClearinghouseAccessDeniedError,
    ClearinghouseError,
    ClearinghouseInFlightError,
    ClearinghouseNotFoundError,
    ClearinghouseNotProvisionedError,
    ClearinghouseRateLimitedError,
    ClearinghouseRequestChangedError,
    ClearinghouseTransactionSettingError,
    ClearinghouseUnavailableError,
    ClearinghouseValidationError,
)
from .sdk_runtime import run_on_sdk_loop
from .sdk_submission import result_from_sdk, submission_error, to_sdk_submission
from .stedi_sdk import client_for

logger = logging.getLogger(__name__)

HEALTHCARE_API_BASE = "https://healthcare.us.stedi.com/2024-04-01"
PAYERS_API_BASE = "https://payers.us.stedi.com/2024-04-01"
CORE_API_BASE = "https://core.us.stedi.com/2023-08-01"
ENROLLMENTS_API_BASE = "https://enrollments.us.stedi.com/2024-09-01"


@dataclass(frozen=True, slots=True)
class ApiBases:
    """The four bases one adapter instance sends to, one per vendor host."""

    healthcare: str
    payers: str
    core: str
    enrollments: str

    @classmethod
    def resolve(cls, base_url: str | None) -> ApiBases:
        """The vendor's own hosts, or all four served from ``base_url``.

        A configured base URL replaces the hostname only: the version
        segment is part of the API's identity rather than of the host, so a
        stand-in that answers for all four (as the end-to-end harness's
        does) is still addressed at ``/2024-04-01/payers/search`` and
        friends.
        """
        if not base_url:
            return DEFAULT_API_BASES
        root = base_url.rstrip("/")
        return cls(
            healthcare=root + urlsplit(HEALTHCARE_API_BASE).path,
            payers=root + urlsplit(PAYERS_API_BASE).path,
            core=root + urlsplit(CORE_API_BASE).path,
            enrollments=root + urlsplit(ENROLLMENTS_API_BASE).path,
        )


#: What an adapter targets when the deployment configures no base URL.
DEFAULT_API_BASES = ApiBases(
    healthcare=HEALTHCARE_API_BASE,
    payers=PAYERS_API_BASE,
    core=CORE_API_BASE,
    enrollments=ENROLLMENTS_API_BASE,
)

_REQUEST_TIMEOUT_SECONDS = 20.0

#: Comfortably inside the vendor's accepted 10-100 range (below 10 is a 400).
_DEFAULT_PAYER_SEARCH_PAGE_SIZE = 25

_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_BAD_REQUEST = 400
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409
_HTTP_UNPROCESSABLE = 422

#: The feed's page size; the vendor allows up to 500.
_TRANSACTION_PAGE_SIZE = 100

#: Error codes the vendor's JSON error envelope (``{"code": ..., "message": ...}``)
#: uses for the failure modes this adapter has typed exceptions for.
_INVALID_REQUEST_BODY = "INVALID_REQUEST_BODY"
_ACCOUNT_NOT_PROVISIONED = "ACCOUNT_NOT_PROVISIONED"
_REQUEST_CHANGED = "REQUEST_CHANGED"

#: The submitter loop of an 837P names the sender; Stedi assigns no
#: submitter id of its own and echoes whatever is sent (the recorded
#: ``837p_request_test_payer.json`` and its X12 carry this value), and the
#: receiver is always the clearinghouse itself.
SUBMITTER_IDENTIFICATION = "0000001"
RECEIVER_NAME = "Stedi"


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

    Claim submission does not reach this function at all: it goes through
    the vendor's SDK rather than over ``httpx``, and its errors are
    translated in ``app.claims.sdk_submission``.

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
    if response.status_code == _HTTP_NOT_FOUND:
        raise ClearinghouseNotFoundError(message or "not found")
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


class StediClearinghouseClient:
    """``ClearinghouseClient`` backed by Stedi's JSON healthcare API.

    ``client`` is an injectable ``httpx.Client`` so tests can swap in
    ``httpx.MockTransport`` — no network in this module's own test suite.
    The default client is built once per instance, not per call, so
    connection reuse works the same way it would in production.

    Where the calls go is read off ``credentials.base_url`` once here: unset
    (the ordinary case) means the vendor's own four hosts.
    """

    def __init__(
        self,
        credentials: ClearinghouseCredentials,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._credentials = credentials
        self._bases = ApiBases.resolve(credentials.base_url)
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
            f"{self._bases.payers}/payers/search",
            params={"query": query, "pageSize": _DEFAULT_PAYER_SEARCH_PAGE_SIZE},
        )
        if response.status_code != httpx.codes.OK:
            _raise_for_error_envelope(response)
        body = response.json()
        return [Payer.model_validate(item["payer"]) for item in body.get("items", [])]

    def check_eligibility(self, req: EligibilityRequest) -> EligibilityResponse:
        response = self._post(
            f"{self._bases.healthcare}/change/medicalnetwork/eligibility/v3",
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
        """File ``req`` on the vendor's own claim API.

        This is the one write in the adapter, and it goes through the native
        endpoint rather than the X12 compatibility shim for a reason that is
        not cosmetic: the claim-lifecycle API only knows claims filed here.
        A claim submitted through the shim has no timeline, so nothing can
        ever read its acknowledgements or its payments.

        The retry engine is not in this path. Replay safety is the vendor's
        job here — ``idempotency_key`` travels on the body, and the vendor
        answers a repeat with the original claim — and the SDK does its own
        retrying underneath.
        """

        async def file() -> Any:
            client = await client_for(self._credentials)
            return await client.create_professional_claim_submission(
                to_sdk_submission(req, idempotency_key=idempotency_key)
            )

        try:
            output = run_on_sdk_loop(file())
        except ClearinghouseError:
            raise
        except Exception as exc:
            raise submission_error(exc) from exc

        result = result_from_sdk(output, req=req)
        logger.info(
            "clearinghouse_claim_submitted status=%s control_number=%s payer_id=%s",
            result.status,
            result.controlNumber,
            result.payer.payerId,
        )
        return result

    def get_transaction(self, transaction_id: str) -> TransactionDocument:
        response = self._get(f"{self._bases.core}/transactions/{transaction_id}")
        if response.status_code != httpx.codes.OK:
            _raise_for_error_envelope(response)
        return TransactionDocument.model_validate(response.json())

    def list_transactions(
        self, *, start: datetime | None = None, page_token: str | None = None
    ) -> TransactionPage:
        params: dict[str, Any] = {"pageSize": _TRANSACTION_PAGE_SIZE}
        if page_token:
            params["pageToken"] = page_token
        elif start is not None:
            params["startDateTime"] = start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        response = self._get(f"{self._bases.core}/polling/transactions", params=params)
        if response.status_code != httpx.codes.OK:
            _raise_for_error_envelope(response)
        return TransactionPage.model_validate(response.json())

    def get_claim_acknowledgment(self, transaction_id: str) -> dict[str, Any]:
        response = self._get(
            f"{self._bases.healthcare}/change/medicalnetwork/reports/v2/{transaction_id}/277"
        )
        if response.status_code != httpx.codes.OK:
            _raise_for_error_envelope(response)
        body = response.json()
        return body if isinstance(body, dict) else {}

    def create_provider(self, provider: ProviderRegistration) -> ProviderRecord:
        response = self._post(
            f"{self._bases.enrollments}/providers",
            json=provider.model_dump(exclude_none=True),
            idempotency=Idempotency.UNSAFE,
        )
        if response.status_code != httpx.codes.OK:
            _raise_for_error_envelope(response)
        return ProviderRecord.model_validate(response.json())

    def create_enrollment(self, enrollment: EnrollmentRequest) -> Enrollment:
        response = self._post(
            f"{self._bases.enrollments}/enrollments",
            json=enrollment.model_dump(exclude_none=True),
            idempotency=Idempotency.UNSAFE,
        )
        if response.status_code != httpx.codes.OK:
            _raise_for_error_envelope(response)
        return Enrollment.model_validate(response.json())

    def list_enrollments(self, filters: EnrollmentFilters) -> EnrollmentPage:
        response = self._get(
            f"{self._bases.enrollments}/enrollments", params=filters.query_params()
        )
        if response.status_code != httpx.codes.OK:
            _raise_for_error_envelope(response)
        return EnrollmentPage.model_validate(response.json())
