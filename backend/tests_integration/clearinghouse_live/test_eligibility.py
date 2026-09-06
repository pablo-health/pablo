# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Mock eligibility checks (270/271) against the vendor's test mode.

The vendor answers a test key with canned 271s for the exact subscriber
values on its "mock requests" reference page; anything else comes back as an
AAA error. The three members below are that page's UnitedHealthcare
examples (active, inactive) plus a made-up id for the error path. All of it
is the vendor's synthetic data, none of it a person.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from app.models.claims_transport import (
    EligibilityProvider,
    EligibilityRequest,
    EligibilityResponse,
    EligibilitySubscriber,
)

from .conftest import assert_same_shape, fixture_shape

if TYPE_CHECKING:
    from .conftest import LiveClient

_RECORDING = "eligibility_271_active.json"

#: The vendor's mock payer for these examples (its UnitedHealthcare id).
_MOCK_PAYER_ID = "87726"
_PROVIDER = EligibilityProvider(organizationName="Pablo Test Practice", npi="1999999984")

_ACTIVE_MEMBER = EligibilitySubscriber(
    memberId="UHC123456", firstName="Jane", lastName="Doe", dateOfBirth="19710101"
)
_INACTIVE_MEMBER = EligibilitySubscriber(
    memberId="UHCINACTIVE", firstName="Jane", lastName="Doe", dateOfBirth="19710101"
)

_ACTIVE_COVERAGE = "1"
_INACTIVE_COVERAGE = "6"
#: AAA 72: "Invalid/Missing Subscriber/Insured ID".
_AAA_SUBSCRIBER_ID_INVALID = "72"


def _request(subscriber: EligibilitySubscriber) -> EligibilityRequest:
    return EligibilityRequest(
        tradingPartnerServiceId=_MOCK_PAYER_ID, provider=_PROVIDER, subscriber=subscriber
    )


def test_an_active_member_reports_active_coverage(live: LiveClient) -> None:
    response = live.adapter.check_eligibility(_request(_ACTIVE_MEMBER))

    assert [s.statusCode for s in response.planStatus][:1] == [_ACTIVE_COVERAGE]
    assert response.meta.traceId
    assert_same_shape(live.recorder.last_json(), fixture_shape(_RECORDING))


def test_an_inactive_member_reports_inactive_coverage(live: LiveClient) -> None:
    response = live.adapter.check_eligibility(_request(_INACTIVE_MEMBER))

    codes = [s.statusCode for s in response.planStatus]
    assert _INACTIVE_COVERAGE in codes
    assert _ACTIVE_COVERAGE not in codes


def test_an_unknown_member_id_is_an_aaa_error_not_a_transport_failure(live: LiveClient) -> None:
    unknown = _ACTIVE_MEMBER.model_copy(
        update={"memberId": "NOSUCHMEMBER" + secrets.token_hex(3).upper()}
    )

    response = live.adapter.check_eligibility(_request(unknown))

    # The vendor answers 200 with an empty plan status and the AAA in
    # ``errors``; the adapter's model does not surface ``errors`` yet, so the
    # error code is read off the raw body.
    assert live.recorder.last_status() == 200
    assert response.planStatus == []
    raw = live.recorder.last_json()
    assert _AAA_SUBSCRIBER_ID_INVALID in [e.get("code") for e in raw.get("errors", [])]
    assert EligibilityResponse.model_validate(raw).planStatus == []
