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

from app.claims.eligibility import REQUESTED_SERVICE_TYPE_CODES, summarize_271
from app.models.claims_transport import (
    EligibilityEncounter,
    EligibilityProvider,
    EligibilityRequest,
    EligibilitySubscriber,
)
from app.utcnow import utc_now

from .conftest import assert_same_shape, fixture_shape

if TYPE_CHECKING:
    from .conftest import LiveClient

_RECORDING = "eligibility_271_active.json"
_AAA_RECORDING = "eligibility_271_aaa_invalid_member_id.json"

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

    # The vendor answers 200 with no plan status at all and the AAA in the
    # top-level ``errors``; the adapter surfaces it rather than answering
    # "no coverage".
    assert live.recorder.last_status() == 200
    assert response.planStatus == []
    assert _AAA_SUBSCRIBER_ID_INVALID in [e.code for e in response.errors]
    assert all(e.followupAction for e in response.errors)
    assert_same_shape(live.recorder.last_json(), fixture_shape(_AAA_RECORDING))


def test_the_mental_health_inquiry_reads_down_to_a_well_formed_summary(live: LiveClient) -> None:
    """The chart's own inquiry — service type ``MH`` — against a live 271,
    read down by the summariser. Proves the vendor still answers the shape
    the summariser reads, and that the reading is coherent: a status, the
    payer's name and id, and every money field either absent or in cents.
    """
    inquiry = _request(_ACTIVE_MEMBER).model_copy(
        update={
            "encounter": EligibilityEncounter(serviceTypeCodes=list(REQUESTED_SERVICE_TYPE_CODES))
        }
    )

    response = live.adapter.check_eligibility(inquiry)
    summary = summarize_271(response, checked_at=utc_now())

    assert summary.status == "active"
    assert summary.aaa_errors == []
    assert summary.payer_name
    assert response.payer is not None
    assert response.payer.payorIdentification == _MOCK_PAYER_ID
    assert summary.plan_name
    assert summary.plan_begin is not None
    assert len(summary.plan_begin) == len("YYYY-MM-DD")
    for cents in (summary.copay_cents, summary.deductible_remaining_cents):
        assert cents is None or (isinstance(cents, int) and cents >= 0)
    assert summary.coinsurance_pct is None or 0 <= summary.coinsurance_pct <= 100
    # The mock member's only carve-out is pharmacy; it must not read as behavioral.
    assert summary.carveout_administrator is None
    assert_same_shape(live.recorder.last_json(), fixture_shape(_RECORDING))
    print(
        "summary:",
        summary.model_dump_json(exclude={"aaa_errors"}, exclude_none=True),
    )
