# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the Stedi clearinghouse adapter.

Every call goes over ``httpx.MockTransport`` — no network — replaying the
recorded (or, where noted in the fixtures' README, constructed) fixtures
under ``tests/fixtures/clearinghouse/``. One test per ``ClearinghouseClient``
protocol method, plus one error-mapping case per vendor status the adapter
names: a generic 400, the unprovisioned-account body, 403, 409, 422 and 429.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from app.claims.clearinghouse import (
    ClearinghouseAccessDeniedError,
    ClearinghouseInFlightError,
    ClearinghouseNotProvisionedError,
    ClearinghouseRateLimitedError,
    ClearinghouseRequestChangedError,
    ClearinghouseUnavailableError,
    ClearinghouseValidationError,
)
from app.claims.credentials import ClearinghouseCredentials
from app.claims.stedi import StediClearinghouseClient
from app.models.claims_transport import (
    ClaimSubmissionRequest,
    EligibilityProvider,
    EligibilityRequest,
    EligibilitySubscriber,
    EnrollmentFilters,
    EnrollmentPayerRef,
    EnrollmentProviderRef,
    EnrollmentRequest,
    EnrollmentTransactions,
    ProviderContact,
    ProviderRegistration,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_FIXTURES = Path(__file__).parent / "fixtures" / "clearinghouse"
_IDEMPOTENCY_KEY = "claim-0001:1:attempt-1"


def _fixture(name: str) -> dict[str, object]:
    data: dict[str, object] = json.loads((_FIXTURES / name).read_text())
    return data


def _client_for(handler: Callable[[httpx.Request], httpx.Response]) -> StediClearinghouseClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    credentials = ClearinghouseCredentials(api_key="key_test_fixture", mode="test")
    return StediClearinghouseClient(credentials, client=http_client)


def _json_response(body: dict[str, object], status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=body)


def _submission_request() -> ClaimSubmissionRequest:
    body = _fixture("837p_request_test_payer.json")
    return ClaimSubmissionRequest.model_validate(body)


class TestSearchPayers:
    def test_returns_the_matching_payers(self) -> None:
        fixture = _fixture("payer_search_test_payer.json")

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/2024-04-01/payers/search"
            assert request.headers["authorization"] == "Key key_test_fixture"
            return _json_response(fixture)

        client = _client_for(handler)

        payers = client.search_payers("Stedi Test Payer")

        assert [p.primaryPayerId for p in payers] == ["STEDI", "DISCOVERY"]
        assert payers[0].displayName == "Stedi Test Payer"


class TestCheckEligibility:
    def test_returns_the_271_response(self) -> None:
        fixture = _fixture("eligibility_271_active.json")

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/2024-04-01/change/medicalnetwork/eligibility/v3"
            return _json_response(fixture)

        client = _client_for(handler)
        req = EligibilityRequest(
            tradingPartnerServiceId="STEDI",
            provider=EligibilityProvider(organizationName="Pablo Test Practice", npi="1999999984"),
            subscriber=EligibilitySubscriber(memberId="123456789"),
        )

        response = client.check_eligibility(req)

        assert response.meta.traceId == "01M1VJ4FKH9T82FWJFGHJ92WEM"
        assert response.planStatus[0].statusCode == "1"
        assert response.errors == []

    def test_a_payer_rejection_arrives_as_errors_not_as_no_coverage(self) -> None:
        fixture = _fixture("eligibility_271_aaa_invalid_member_id.json")

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(fixture)

        client = _client_for(handler)
        req = EligibilityRequest(
            tradingPartnerServiceId="87726",
            provider=EligibilityProvider(organizationName="Pablo Test Practice", npi="1999999984"),
            subscriber=EligibilitySubscriber(memberId="NOSUCHMEMBER000000"),
        )

        response = client.check_eligibility(req)

        assert response.planStatus == []
        assert [e.code for e in response.errors] == ["72"]
        assert response.errors[0].description == "Invalid/Missing Subscriber/Insured ID"
        assert response.errors[0].followupAction == "Please Correct and Resubmit"


class TestSubmitClaim:
    def test_a_success_response_carries_the_claim_reference(self) -> None:
        fixture = _fixture("837p_submission_success_test_payer.json")

        def handler(request: httpx.Request) -> httpx.Response:
            assert (
                request.url.path
                == "/2024-04-01/change/medicalnetwork/professionalclaims/v3/submission"
            )
            assert request.headers["idempotency-key"] == _IDEMPOTENCY_KEY
            return _json_response(fixture)

        client = _client_for(handler)

        result = client.submit_claim(_submission_request(), idempotency_key=_IDEMPOTENCY_KEY)

        assert result.status == "SUCCESS"
        assert result.claimReference is not None
        assert result.claimReference.rhclaimNumber == "01M1T7001FRW15MVE0SSW4FA7G"

    def test_an_edit_rejection_is_a_result_not_an_exception(self) -> None:
        fixture = _fixture("837p_submission_edit_rejected_dx_pointer.json")

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(fixture, status_code=400)

        client = _client_for(handler)

        result = client.submit_claim(_submission_request(), idempotency_key=_IDEMPOTENCY_KEY)

        assert result.status == "ERROR"
        assert result.errors[0].code == "33"
        assert result.errors[0].followupAction == "Please Correct and Resubmit"


class TestGetTransaction:
    def test_returns_the_transaction_document(self) -> None:
        listing = _fixture("polling_transactions_277_and_835.json")
        items = listing["items"]
        assert isinstance(items, list)
        item = items[0]

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == f"/2023-08-01/transactions/{item['transactionId']}"
            return _json_response(item)

        client = _client_for(handler)

        document = client.get_transaction(str(item["transactionId"]))

        assert document.transactionId == item["transactionId"]
        assert document.direction == "OUTBOUND"
        assert document.businessIdentifiers[0].name == "Patient Control Number"


class TestCreateProvider:
    def test_returns_the_registered_provider(self) -> None:
        fixture = _fixture("enrollment_create_provider.json")

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/2024-09-01/providers"
            return _json_response(fixture)

        client = _client_for(handler)
        registration = ProviderRegistration(
            name="Pablo Health Test Provider",
            npi="1999999984",
            taxId="844459714",
            contacts=[
                ProviderContact(
                    organizationName="Pablo Health Test Provider",
                    email="test@example.com",
                    phone="4045550100",
                    streetAddress1="1 Test St",
                    city="Atlanta",
                    zipCode="30301",
                    state="GA",
                )
            ],
        )

        provider = client.create_provider(registration)

        assert provider.npi == "1999999984"
        assert provider.id == "01a0746f-25d4-78a0-bb43-0f95acd218c9"


class TestCreateEnrollment:
    def test_returns_the_enrollment(self) -> None:
        fixture = _fixture("enrollment_create_enrollment_835.json")

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/2024-09-01/enrollments"
            return _json_response(fixture)

        client = _client_for(handler)
        request = EnrollmentRequest(
            provider=EnrollmentProviderRef(id="01a0746f-25d4-78a0-bb43-0f95acd218c9"),
            payer=EnrollmentPayerRef(idOrAlias="STEDI"),
            primaryContact=ProviderContact(
                organizationName="Pablo Health Test Provider",
                email="test@example.com",
                phone="4045550100",
                streetAddress1="1 Test St",
                city="Atlanta",
                zipCode="30301",
                state="GA",
            ),
            transactions=EnrollmentTransactions(),
        )

        enrollment = client.create_enrollment(request)

        assert enrollment.status == "STEDI_ACTION_REQUIRED"
        assert enrollment.payer.submittedPayerIdOrAlias == "STEDI"


class TestListEnrollments:
    def test_returns_the_matching_enrollments(self) -> None:
        fixture = _fixture("enrollment_create_enrollment_835.json")

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/2024-09-01/enrollments"
            assert not request.url.params
            return _json_response({"items": [fixture]})

        client = _client_for(handler)

        page = client.list_enrollments(EnrollmentFilters())

        assert len(page.items) == 1
        assert page.items[0].id == "01a0746f-2edf-75c0-a780-555b1231c789"
        assert page.nextPageToken is None

    def test_sends_each_filter_as_the_vendors_repeated_query_key(self) -> None:
        fixture = _fixture("enrollment_create_enrollment_835.json")

        def handler(request: httpx.Request) -> httpx.Response:
            params = request.url.params
            assert params.get_list("providerIds") == ["prov-1", "prov-2"]
            assert params.get_list("payerIds") == ["FRCPB"]
            assert params.get_list("status") == ["LIVE", "REJECTED"]
            assert params.get_list("pageSize") == ["50"]
            assert params.get_list("pageToken") == ["tok-1"]
            assert set(params.keys()) == {
                "providerIds",
                "payerIds",
                "status",
                "pageSize",
                "pageToken",
            }
            return _json_response({"items": [fixture], "nextPageToken": "tok-2", "totalCount": 2})

        client = _client_for(handler)

        page = client.list_enrollments(
            EnrollmentFilters(
                providerIds=["prov-1", "prov-2"],
                payerIds=["FRCPB"],
                statuses=["LIVE", "REJECTED"],
                pageSize=50,
                pageToken="tok-1",
            )
        )

        assert page.nextPageToken == "tok-2"
        assert page.totalCount == 2


class TestErrorMapping:
    def test_invalid_request_body_raises_a_validation_error(self) -> None:
        fixture = _fixture("error_invalid_request_body.json")

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(fixture, status_code=400)

        client = _client_for(handler)

        with pytest.raises(ClearinghouseValidationError):
            client.search_payers("anything")

    def test_unprovisioned_account_raises_a_typed_error(self) -> None:
        fixture = _fixture("error_account_not_provisioned.json")

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(fixture, status_code=400)

        client = _client_for(handler)

        with pytest.raises(ClearinghouseNotProvisionedError):
            client.submit_claim(_submission_request(), idempotency_key=_IDEMPOTENCY_KEY)

    def test_rate_limiting_raises_a_typed_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"code": "TOO_MANY_REQUESTS"})

        client = _client_for(handler)

        with pytest.raises(ClearinghouseRateLimitedError):
            client.search_payers("anything")

    def test_a_reused_key_with_a_changed_body_raises_request_changed(self) -> None:
        fixture = _fixture("error_request_changed.json")

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(fixture, status_code=422)

        client = _client_for(handler)

        with pytest.raises(ClearinghouseRequestChangedError):
            client.submit_claim(_submission_request(), idempotency_key=_IDEMPOTENCY_KEY)

    def test_a_forbidden_api_raises_access_denied(self) -> None:
        fixture = _fixture("error_access_denied.json")

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(fixture, status_code=403)

        client = _client_for(handler)

        with pytest.raises(ClearinghouseAccessDeniedError):
            client.list_enrollments(EnrollmentFilters())

    def test_an_in_flight_key_raises_with_the_retry_hint(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                409,
                headers={"Retry-After": "5"},
                json={"message": "A request with this idempotency key is still in progress."},
            )

        client = _client_for(handler)

        with pytest.raises(ClearinghouseInFlightError) as raised:
            client.submit_claim(_submission_request(), idempotency_key=_IDEMPOTENCY_KEY)

        assert raised.value.retry_after == 5.0

    def test_an_in_flight_key_without_a_hint_carries_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"message": "still in progress"})

        client = _client_for(handler)

        with pytest.raises(ClearinghouseInFlightError) as raised:
            client.submit_claim(_submission_request(), idempotency_key=_IDEMPOTENCY_KEY)

        assert raised.value.retry_after is None

    def test_a_5xx_is_the_only_thing_left_as_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="bad gateway")

        client = _client_for(handler)

        with pytest.raises(ClearinghouseUnavailableError):
            client.search_payers("anything")
