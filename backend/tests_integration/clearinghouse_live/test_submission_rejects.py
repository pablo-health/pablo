# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Claims the vendor's edits reject on purpose, and the local pre-flight that
would have caught each one before the round trip.

Each test takes the recorded test-payer claim, applies exactly one defect,
and expects the synchronous 400 edit rejection — a business answer the
adapter returns as a result, not an exception. Alongside it, each test
asserts the matching check in ``app.claims.validation`` flags the same
defect, which is what keeps these claims from ever being sent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.claims.validation import dx_at_highest_specificity, dx_pointers_valid, missing_fields
from app.models.claims_transport import ClaimSubmissionRequest, ClaimSubmissionResult, Subscriber

from .conftest import (
    assert_same_shape,
    fixture_shape,
    fresh_control_number,
    fresh_idempotency_key,
    submission_body,
)

if TYPE_CHECKING:
    from .conftest import LiveClient

#: The vendor's edit-rejection code for a claim that fails its front-end edits.
_EDIT_REJECTED = "33"

#: What an 837P needs on the subscriber when the patient is the subscriber.
_SUBSCRIBER_DEMOGRAPHICS = ["dateOfBirth", "gender", "address"]


def _submit_expecting_rejection(
    live: LiveClient, request: ClaimSubmissionRequest
) -> ClaimSubmissionResult:
    result = live.adapter.submit_claim(request, idempotency_key=fresh_idempotency_key())

    assert live.recorder.last_status() == 400
    assert result.status == "ERROR"
    assert result.errors, "an edit rejection carries at least one error"
    assert {e.code for e in result.errors} == {_EDIT_REJECTED}
    return result


def _diagnoses(body: dict[str, Any]) -> list[dict[str, Any]]:
    codes = body["claimInformation"]["healthCareCodeInformation"]
    assert isinstance(codes, list)
    return codes


def test_a_bare_diagnosis_category_is_rejected(live: LiveClient) -> None:
    body = submission_body(fresh_control_number())
    diagnosis = _diagnoses(body)[0]
    category = diagnosis["diagnosisCode"][:3]
    diagnosis["diagnosisCode"] = category

    assert not dx_at_highest_specificity(category), "pre-flight should reject a bare category"
    assert dx_at_highest_specificity(
        fixture_shape("837p_request_test_payer.json")["claimInformation"][
            "healthCareCodeInformation"
        ][0]["diagnosisCode"]
    ), "the recorded claim's code passes"

    _submit_expecting_rejection(live, ClaimSubmissionRequest.model_validate(body))

    assert_same_shape(
        live.recorder.last_json(),
        fixture_shape("837p_submission_edit_rejected_dx_specificity.json"),
    )


def test_a_pointer_to_a_missing_diagnosis_is_rejected(live: LiveClient) -> None:
    body = submission_body(fresh_control_number())
    pointers = ["2"]
    body["claimInformation"]["serviceLines"][0]["professionalService"][
        "compositeDiagnosisCodePointers"
    ]["diagnosisCodePointers"] = pointers

    assert not dx_pointers_valid(pointers, len(_diagnoses(body)))
    assert dx_pointers_valid(["1"], len(_diagnoses(body)))

    _submit_expecting_rejection(live, ClaimSubmissionRequest.model_validate(body))

    assert_same_shape(
        live.recorder.last_json(), fixture_shape("837p_submission_edit_rejected_dx_pointer.json")
    )


def test_missing_subscriber_demographics_are_rejected(live: LiveClient) -> None:
    body = submission_body(fresh_control_number())
    partial = {k: v for k, v in body["subscriber"].items() if k not in _SUBSCRIBER_DEMOGRAPHICS}

    assert missing_fields(partial, _SUBSCRIBER_DEMOGRAPHICS) == _SUBSCRIBER_DEMOGRAPHICS
    assert missing_fields(body["subscriber"], _SUBSCRIBER_DEMOGRAPHICS) == []

    # The wire model requires these fields, so the defective subscriber is
    # built unvalidated — the point is to send the claim the model would
    # otherwise refuse and watch the vendor refuse it too.
    request = ClaimSubmissionRequest.model_validate(body).model_copy(
        update={"subscriber": Subscriber.model_construct(**partial)}
    )
    sent = request.model_dump(exclude_none=True)["subscriber"]
    assert not set(_SUBSCRIBER_DEMOGRAPHICS) & set(sent)

    _submit_expecting_rejection(live, request)

    assert_same_shape(
        live.recorder.last_json(),
        fixture_shape("837p_submission_edit_rejected_subscriber_demographics.json"),
    )
