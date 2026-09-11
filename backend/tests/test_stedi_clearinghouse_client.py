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
    ClearinghouseError,
    ClearinghouseInFlightError,
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
    ManualTaskResponse,
    ProviderContact,
    ProviderRegistration,
    TaskCompletion,
    TaskDocumentRef,
    TaskFieldAnswer,
    TaskFieldValue,
    TaskResponseData,
)
from stedi.models import (
    ClaimRejectionError,
    ConflictException,
    CreateProfessionalClaimSubmissionOutput,
    ForbiddenException,
    InvalidRequestException,
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


class _FakeSdkClient:
    """Stands in for the vendor SDK client on the one call that writes.

    Submission is the only adapter method that does not go over ``httpx``:
    it goes through the vendor's own SDK, so its seam is the SDK client
    rather than a transport. What the test asserts is unchanged — the
    request the adapter built, and how it read the answer.
    """

    def __init__(self, *, answer: object = None, raises: Exception | None = None) -> None:
        self._answer = answer
        self._raises = raises
        self.submitted: object = None

    async def create_professional_claim_submission(self, submission: object) -> object:
        self.submitted = submission
        if self._raises is not None:
            raise self._raises
        return self._answer


def _submitting_client(
    monkeypatch: pytest.MonkeyPatch, *, answer: object = None, raises: Exception | None = None
) -> tuple[StediClearinghouseClient, _FakeSdkClient]:
    sdk = _FakeSdkClient(answer=answer, raises=raises)

    async def _client_for(_credentials: object) -> _FakeSdkClient:
        return sdk

    monkeypatch.setattr("app.claims.stedi.client_for", _client_for)
    credentials = ClearinghouseCredentials(api_key="key_test_fixture", mode="test")
    return StediClearinghouseClient(credentials), sdk


class TestSubmitClaim:
    def test_a_success_response_carries_the_claim_reference(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = _submitting_client(
            monkeypatch,
            answer=CreateProfessionalClaimSubmissionOutput(
                claim_id="clm_01TESTCLAIM", submission_id="sbm_01TESTSUB"
            ),
        )

        result = client.submit_claim(_submission_request(), idempotency_key=_IDEMPOTENCY_KEY)

        assert result.status == "SUCCESS"
        assert result.claimReference is not None
        assert result.claimReference.correlationId == "clm_01TESTCLAIM"

    def test_the_idempotency_key_reaches_the_vendor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """It moved from a header to the body in the switch to the SDK.

        Dropping it would not fail anything visibly — it would file a second
        claim every time a submission was retried after a timeout.
        """
        client, sdk = _submitting_client(
            monkeypatch,
            answer=CreateProfessionalClaimSubmissionOutput(
                claim_id="clm_01TESTCLAIM", submission_id="sbm_01TESTSUB"
            ),
        )

        client.submit_claim(_submission_request(), idempotency_key=_IDEMPOTENCY_KEY)

        assert sdk.submitted is not None
        assert sdk.submitted.idempotency_key == _IDEMPOTENCY_KEY  # type: ignore[attr-defined]

    def test_an_edit_rejection_is_a_result_not_an_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = _submitting_client(
            monkeypatch,
            answer=CreateProfessionalClaimSubmissionOutput(
                claim_id="clm_01TESTCLAIM",
                submission_id="sbm_01TESTSUB",
                errors=[ClaimRejectionError(description="Diagnosis code pointer is invalid")],
            ),
        )

        result = client.submit_claim(_submission_request(), idempotency_key=_IDEMPOTENCY_KEY)

        assert result.status == "ERROR"
        assert result.errors[0].description == "Diagnosis code pointer is invalid"


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


class TestGetEnrollment:
    def test_returns_the_enrollment_with_its_tasks_and_documents(self) -> None:
        fixture = _fixture("enrollment_provider_action_required.json")
        enrollment_id = "01a0746f-2edf-75c0-a780-555b1231c789"

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == f"/2024-09-01/enrollments/{enrollment_id}"
            return _json_response(fixture)

        enrollment = _client_for(handler).get_enrollment(enrollment_id)

        [ours] = enrollment.open_tasks()
        [field] = ours.fields
        assert field.key == "signed_eft_form"
        assert field.fieldType == "DOCUMENT"
        # The second task on the fixture is the vendor's own, and is not ours.
        assert [task.id for task in enrollment.tasks] != [ours.id]


class TestEnrollmentDocuments:
    def test_asking_for_a_slot_names_the_task_it_answers(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/2024-09-01/enrollments/enr-1/documents"
            assert json.loads(request.content) == {
                "name": "signed_eft_form.pdf",
                "taskId": "task-1",
            }
            return _json_response(
                {
                    "enrollmentId": "enr-1",
                    "uploadUrl": "https://s3.example.test/upload",
                    "documentId": "doc-1",
                }
            )

        upload = _client_for(handler).upload_enrollment_document(
            "enr-1", name="signed_eft_form.pdf", task_id="task-1"
        )

        assert upload.uploadUrl == "https://s3.example.test/upload"
        assert upload.documentId == "doc-1"

    def test_the_bytes_go_out_with_no_account_key(self) -> None:
        """The pre-signed URL is at the vendor's storage provider, not its API."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == "https://s3.example.test/upload"
            assert "authorization" not in request.headers
            assert request.headers["content-type"] == "application/pdf"
            assert request.content == b"%PDF-1.4 fake"
            return httpx.Response(200)

        _client_for(handler).put_document("https://s3.example.test/upload", b"%PDF-1.4 fake")

    def test_a_refused_upload_raises_rather_than_reporting_success(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        with pytest.raises(ClearinghouseError):
            _client_for(handler).put_document("https://s3.example.test/upload", b"%PDF-1.4 fake")

    def test_a_download_is_asked_for_by_document_id(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/2024-09-01/documents/doc-1/download"
            assert request.headers["authorization"] == "Key key_test_fixture"
            return _json_response({"downloadUrl": "https://s3.example.test/download"})

        download = _client_for(handler).download_enrollment_document("doc-1")

        assert download.downloadUrl == "https://s3.example.test/download"


class TestResolvingATaskLink:
    def test_follows_the_url_the_vendor_gave_us_verbatim(self) -> None:
        """No path is constructed here — the vendor handed over the whole URL."""
        url = "https://enrollments.us.stedi.com/2024-09-01/documents/doc-1"

        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == url
            assert request.headers["authorization"] == "Key key_test_fixture"
            return _json_response({"downloadUrl": "https://s3.example.test/download"})

        download = _client_for(handler).resolve_enrollment_link(url)

        assert download.downloadUrl == "https://s3.example.test/download"

    def test_a_link_off_the_vendors_api_never_sees_the_key(self) -> None:
        """A task's links come from the payer. The key goes to one host only."""

        def handler(request: httpx.Request) -> httpx.Response:
            msg = f"the key must not have been sent to {request.url}"
            raise AssertionError(msg)

        with pytest.raises(ClearinghouseError):
            _client_for(handler).resolve_enrollment_link("https://payer.example/steal-my-key")

    def test_tells_the_two_kinds_of_link_apart(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response({})

        client = _client_for(handler)

        assert client.hosts_enrollment_documents(
            "https://enrollments.us.stedi.com/2024-09-01/documents/doc-1"
        )
        assert not client.hosts_enrollment_documents("https://payer.example/forms/eft.pdf")


class TestCompletingATask:
    def test_a_task_with_fields_posts_every_value(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/2024-09-01/tasks/task-1"
            assert json.loads(request.content) == {
                "completed": True,
                "responseData": {
                    "manualTask": {
                        "values": [
                            {
                                "key": "signed_eft_form",
                                "value": {"document": {"documentId": "doc-1"}},
                            }
                        ]
                    }
                },
            }
            return _json_response({})

        _client_for(handler).complete_enrollment_task(
            "task-1",
            TaskCompletion(
                responseData=TaskResponseData(
                    manualTask=ManualTaskResponse(
                        values=[
                            TaskFieldAnswer(
                                key="signed_eft_form",
                                value=TaskFieldValue(document=TaskDocumentRef(documentId="doc-1")),
                            )
                        ]
                    )
                )
            ),
        )

    def test_a_task_with_nothing_to_say_still_says_it_is_done(self) -> None:
        """``completed`` is the whole answer when the task asked for nothing."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert json.loads(request.content) == {"completed": True}
            return _json_response({})

        _client_for(handler).complete_enrollment_task("task-2", TaskCompletion())


class TestErrorMapping:
    def test_invalid_request_body_raises_a_validation_error(self) -> None:
        fixture = _fixture("error_invalid_request_body.json")

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(fixture, status_code=400)

        client = _client_for(handler)

        with pytest.raises(ClearinghouseValidationError):
            client.search_payers("anything")

    def test_an_account_that_may_not_file_claims_raises_access_denied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The vendor's native claim API has no "not provisioned" of its own.

        The old endpoint distinguished an unenrolled payer from a key with no
        claim rights; the claim API answers both as a refusal to serve. Both
        stall the claim with a message a human reads, so the practical
        outcome is unchanged — but the finer code is genuinely gone rather
        than being mapped from something that no longer arrives.
        """
        client, _ = _submitting_client(monkeypatch, raises=ForbiddenException("forbidden"))

        with pytest.raises(ClearinghouseAccessDeniedError):
            client.submit_claim(_submission_request(), idempotency_key=_IDEMPOTENCY_KEY)

    def test_rate_limiting_raises_a_typed_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"code": "TOO_MANY_REQUESTS"})

        client = _client_for(handler)

        with pytest.raises(ClearinghouseRateLimitedError):
            client.search_payers("anything")

    def test_a_reused_key_with_a_changed_body_raises_request_changed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verified against the vendor 2026-09-09: the same key with a
        different claim is refused, while the same key with the same claim
        replays the original — which is what makes retrying a submission
        safe."""
        client, _ = _submitting_client(
            monkeypatch,
            raises=InvalidRequestException(
                "This Idempotency-Key was previously used with a different request."
            ),
        )

        with pytest.raises(ClearinghouseRequestChangedError):
            client.submit_claim(_submission_request(), idempotency_key=_IDEMPOTENCY_KEY)

    def test_a_forbidden_api_raises_access_denied(self) -> None:
        fixture = _fixture("error_access_denied.json")

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(fixture, status_code=403)

        client = _client_for(handler)

        with pytest.raises(ClearinghouseAccessDeniedError):
            client.list_enrollments(EnrollmentFilters())

    def test_an_in_flight_key_raises_with_the_retry_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conflict = ConflictException("A request with this key is still in progress.")
        conflict.retry_after = 5.0
        client, _ = _submitting_client(monkeypatch, raises=conflict)

        with pytest.raises(ClearinghouseInFlightError) as raised:
            client.submit_claim(_submission_request(), idempotency_key=_IDEMPOTENCY_KEY)

        assert raised.value.retry_after == 5.0

    def test_an_in_flight_key_without_a_hint_carries_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = _submitting_client(monkeypatch, raises=ConflictException("still in progress"))

        with pytest.raises(ClearinghouseInFlightError) as raised:
            client.submit_claim(_submission_request(), idempotency_key=_IDEMPOTENCY_KEY)

        assert raised.value.retry_after is None

    def test_a_5xx_is_the_only_thing_left_as_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="bad gateway")

        client = _client_for(handler)

        with pytest.raises(ClearinghouseUnavailableError):
            client.search_payers("anything")
