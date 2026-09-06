# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The enrollment surface, read-only, against the vendor's test mode.

The account's provider record and its test-payer remittance enrollment
already exist (see the fixtures README) and are never created again here.
What a test key can prove about them is limited: the vendor documents that
transaction enrollment is not available in test mode, and its enrollments
API refuses a test key with ``403 access_denied`` — listing included. So
this lane pins that refusal, which is the answer the adapter has to cope
with; confirming the enrollment is LIVE needs the account's production
credentials, which this lane refuses to run with by design.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.claims.clearinghouse import ClearinghouseUnavailableError
from app.claims.responses import parse_enrollment
from app.models.claims_transport import EnrollmentFilters

from .conftest import TEST_PAYER_ID, assert_same_shape, fixture_shape

if TYPE_CHECKING:
    from .conftest import LiveClient

_HTTP_FORBIDDEN = 403
_ACCESS_DENIED = "access_denied"
_ENROLLMENT_RECORDING = "enrollment_create_enrollment_835.json"


def _platform_provider_id() -> str:
    provider_id = fixture_shape("enrollment_create_provider.json")["id"]
    assert isinstance(provider_id, str)
    return provider_id


def test_the_recorded_enrollment_is_the_test_payers_remittance_enrollment() -> None:
    recorded = fixture_shape(_ENROLLMENT_RECORDING)

    assert recorded["provider"]["id"] == _platform_provider_id()
    assert recorded["payer"]["submittedPayerIdOrAlias"] == TEST_PAYER_ID
    assert parse_enrollment(recorded).transactions_requested == ["claimPayment"]


def test_listing_enrollments_is_refused_in_test_mode(live: LiveClient) -> None:
    filters = EnrollmentFilters(providerId=_platform_provider_id())

    # The adapter has no typed error for the vendor's 403; it surfaces as
    # "unavailable". No enrollment is created or changed by this call.
    with pytest.raises(ClearinghouseUnavailableError):
        live.adapter.list_enrollments(filters)

    assert live.recorder.last_status() == _HTTP_FORBIDDEN
    envelope = live.recorder.last_json()
    assert envelope.get("code") == _ACCESS_DENIED
    assert_same_shape(envelope, fixture_shape("error_account_not_provisioned.json"))
