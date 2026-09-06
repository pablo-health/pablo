# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``deadlines_for``: every claim state against every deadline kind.

Pure function, so each case is a claim, a payer, a remittance date (or
none) and a ``today``, and the assertion is which of filing / correction /
appeal applies and how many days are left.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from app.claims.deadlines import deadlines_for
from app.models.coverage import Payer

from tests.claims_fixtures import BUILT_AT, SERVICE_DATE, TODAY, claim, line

_STATES = (
    "draft",
    "validated",
    "submitted",
    "ch_accepted",
    "payer_accepted",
    "paid",
    "partial",
    "denied",
    "rejected",
    "stalled",
)

_REMITTANCE_AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_REMITTANCE_DATE = date(2026, 10, 1)


def _payer(**overrides: Any) -> Payer:
    fields: dict[str, Any] = {
        "id": "33333333-3333-4333-8333-333333333333",
        "name": "Test Payer",
        "payer_id": "STEDI",
        "timely_filing_days": 90,
        "corrected_claim_days": 90,
        "appeal_days": 180,
        "created_at": BUILT_AT,
        "updated_at": BUILT_AT,
    }
    fields.update(overrides)
    return Payer(**fields)


# ---------------------------------------------------------------------------
# The three dates are always computed from what is known
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", _STATES)
def test_filing_date_is_earliest_service_date_plus_payer_window(state: str) -> None:
    later = line(
        id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        line_number=2,
        line_control_number="886598912",
        service_date=SERVICE_DATE + timedelta(days=7),
    )
    result = deadlines_for(claim(state=state, lines=[later, line()]), _payer(), None, today=TODAY)
    assert result.filing == SERVICE_DATE + timedelta(days=90)


@pytest.mark.parametrize("state", _STATES)
def test_correction_and_appeal_dates_follow_the_remittance(state: str) -> None:
    result = deadlines_for(claim(state=state), _payer(), _REMITTANCE_AT, today=TODAY)
    assert result.correction == _REMITTANCE_DATE + timedelta(days=90)
    assert result.appeal == _REMITTANCE_DATE + timedelta(days=180)


@pytest.mark.parametrize("state", _STATES)
def test_no_remittance_means_no_correction_or_appeal_date(state: str) -> None:
    result = deadlines_for(claim(state=state), _payer(), None, today=TODAY)
    assert result.correction is None
    assert result.appeal is None


# ---------------------------------------------------------------------------
# Which one applies, state by state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["draft", "validated", "rejected"])
def test_filing_applies_before_the_payer_has_the_claim(state: str) -> None:
    result = deadlines_for(claim(state=state), _payer(), _REMITTANCE_AT, today=TODAY)
    assert result.applicable == "filing"
    assert result.days_left == (SERVICE_DATE + timedelta(days=90) - TODAY).days


def test_filing_applies_to_a_claim_that_stalled_before_payer_acceptance() -> None:
    result = deadlines_for(claim(state="stalled"), _payer(), None, today=TODAY)
    assert result.applicable == "filing"


def test_nothing_applies_to_a_claim_that_stalled_after_payer_acceptance() -> None:
    stalled = claim(state="stalled", payer_accepted_at=BUILT_AT)
    result = deadlines_for(stalled, _payer(), None, today=TODAY)
    assert result.applicable is None
    assert result.days_left is None


@pytest.mark.parametrize("state", ["submitted", "ch_accepted", "payer_accepted"])
def test_nothing_applies_while_the_claim_is_in_flight(state: str) -> None:
    result = deadlines_for(claim(state=state), _payer(), _REMITTANCE_AT, today=TODAY)
    assert result.applicable is None
    assert result.days_left is None


@pytest.mark.parametrize("state", ["denied", "partial"])
def test_correction_applies_after_a_remittance_when_it_is_sooner(state: str) -> None:
    result = deadlines_for(claim(state=state), _payer(), _REMITTANCE_AT, today=TODAY)
    assert result.applicable == "correction"
    assert result.days_left == (_REMITTANCE_DATE + timedelta(days=90) - TODAY).days


@pytest.mark.parametrize("state", ["denied", "partial"])
def test_appeal_applies_after_a_remittance_when_it_is_sooner(state: str) -> None:
    payer = _payer(corrected_claim_days=180, appeal_days=30)
    result = deadlines_for(claim(state=state), payer, _REMITTANCE_AT, today=TODAY)
    assert result.applicable == "appeal"
    assert result.days_left == (_REMITTANCE_DATE + timedelta(days=30) - TODAY).days


@pytest.mark.parametrize("state", ["denied", "partial"])
def test_correction_wins_a_tie(state: str) -> None:
    payer = _payer(corrected_claim_days=60, appeal_days=60)
    result = deadlines_for(claim(state=state), payer, _REMITTANCE_AT, today=TODAY)
    assert result.applicable == "correction"


@pytest.mark.parametrize("state", ["denied", "partial"])
def test_denied_without_a_remittance_yet_has_nothing_applicable(state: str) -> None:
    result = deadlines_for(claim(state=state), _payer(), None, today=TODAY)
    assert result.applicable is None
    assert result.days_left is None


def test_paid_claim_is_under_no_clock() -> None:
    result = deadlines_for(claim(state="paid"), _payer(), _REMITTANCE_AT, today=TODAY)
    assert result.applicable is None
    assert result.days_left is None


@pytest.mark.parametrize("state", _STATES)
def test_void_is_under_no_clock_in_any_state(state: str) -> None:
    void = claim(state=state, frequency_code="8")
    result = deadlines_for(void, _payer(), _REMITTANCE_AT, today=TODAY)
    assert result.applicable is None
    assert result.days_left is None


# ---------------------------------------------------------------------------
# Edge cases the addendum names
# ---------------------------------------------------------------------------


def test_medicare_files_within_a_year() -> None:
    medicare = _payer(payer_id="MEDICARE-GA", timely_filing_days=365)
    result = deadlines_for(claim(), medicare, None, today=TODAY)
    assert result.filing == SERVICE_DATE + timedelta(days=365)
    assert result.applicable == "filing"
    assert result.days_left == 360


def test_a_claim_with_no_lines_has_no_filing_deadline() -> None:
    result = deadlines_for(claim(lines=[]), _payer(), None, today=TODAY)
    assert result.filing is None
    assert result.applicable is None
    assert result.days_left is None


def test_days_left_goes_negative_once_the_date_has_passed() -> None:
    late = SERVICE_DATE + timedelta(days=100)
    result = deadlines_for(claim(), _payer(), None, today=late)
    assert result.days_left == -10
