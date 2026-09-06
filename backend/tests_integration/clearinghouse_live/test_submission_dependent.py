# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A test-payer claim for a client covered under somebody else's plan.

The claim is built the way a real one is — a stored claim through
``to_submission_request`` — so what the vendor accepts here is the mapping's
own output: the policy holder in ``subscriber``, the client in ``dependent``
with the relationship code. The companion reject blanks the dependent's date
of birth and expects the vendor's edit, after checking that the scrub would
have stopped that claim at ``draft`` and the mapping would have refused to
build it.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest
from app.claims.scrub import blocking, scrub
from app.claims.wire import ClaimMappingError, to_submission_request
from tests.claims_fixtures import claim, line, person, subscriber_snapshot

from .conftest import (
    TEST_PAYER_ID,
    assert_same_shape,
    fixture_shape,
    fresh_control_number,
    fresh_idempotency_key,
)

if TYPE_CHECKING:
    from app.models.claims import Claim, PersonSnapshot
    from app.models.claims_transport import ClaimSubmissionRequest

    from .conftest import LiveClient

_RECORDING = "837p_submission_success_dependent_test_payer.json"
_EDIT_REJECTED = "33"
_CHILD_CODE = "19"


def _policy_holder() -> PersonSnapshot:
    return person(
        first_name="Pat",
        last_name="Anon",
        date_of_birth=date(1975, 5, 5),
        sex="F",
        address_line1="4444 Other Ave",
        postal_code="303021111",
    )


def _child(**overrides: object) -> PersonSnapshot:
    fields: dict[str, object] = {
        "first_name": "Sam",
        "last_name": "Anon",
        "date_of_birth": date(2012, 3, 4),
        "sex": "M",
    }
    fields.update(overrides)
    return person(**fields)


def _dependent_claim(control_number: str, patient: PersonSnapshot) -> Claim:
    return claim(
        control_number=control_number,
        subscriber_snapshot=subscriber_snapshot(
            relationship="child", subscriber=_policy_holder(), patient=patient
        ),
        lines=[line(line_control_number=control_number + "L1")],
    )


def _request(built: Claim) -> ClaimSubmissionRequest:
    return to_submission_request(
        built,
        trading_partner_service_id=TEST_PAYER_ID,
        usage_indicator="T",
        tax_id="84-4459714",
        submitter_identification="0000001",
        receiver_name="Stedi",
    )


def test_the_test_payer_accepts_a_claim_for_a_dependent(live: LiveClient) -> None:
    control_number = fresh_control_number()
    built = _dependent_claim(control_number, _child())
    assert not blocking(scrub(built))

    request = _request(built)
    assert request.dependent is not None
    assert request.dependent.relationshipToSubscriberCode == _CHILD_CODE
    assert request.dependent.memberId is None

    result = live.adapter.submit_claim(request, idempotency_key=fresh_idempotency_key())

    assert result.status == "SUCCESS"
    assert result.claimReference is not None
    assert result.claimReference.patientControlNumber == control_number
    assert result.claimReference.payerId == TEST_PAYER_ID
    assert_same_shape(live.recorder.last_json(), fixture_shape(_RECORDING))


def test_a_dependent_without_a_date_of_birth_is_rejected(live: LiveClient) -> None:
    control_number = fresh_control_number()
    defective = _dependent_claim(control_number, _child(date_of_birth=None))

    stopped = [f for f in blocking(scrub(defective)) if f.field == "patient.date_of_birth"]
    assert [f.code for f in stopped] == ["subscriber_demographics_missing"]
    with pytest.raises(ClaimMappingError) as excinfo:
        _request(defective)
    assert excinfo.value.missing == ["patient.date_of_birth"]

    # Neither gate lets this claim out, so the request is built from the
    # intact claim and the date blanked afterwards — the empty string is what
    # the mapping writes for a missing date, so this is the claim the vendor
    # would see if both gates were skipped. (Leaving the field out entirely
    # never reaches the edits: the vendor's body validator refuses it first,
    # which the adapter raises as ``ClearinghouseValidationError``.)
    request = _request(_dependent_claim(control_number, _child()))
    assert request.dependent is not None
    request.dependent.dateOfBirth = ""

    result = live.adapter.submit_claim(request, idempotency_key=fresh_idempotency_key())

    assert live.recorder.last_status() == 400
    assert result.status == "ERROR"
    assert result.errors, "an edit rejection carries at least one error"
    assert {e.code for e in result.errors} == {_EDIT_REJECTED}
    assert_same_shape(
        live.recorder.last_json(),
        fixture_shape("837p_submission_edit_rejected_subscriber_demographics.json"),
    )
