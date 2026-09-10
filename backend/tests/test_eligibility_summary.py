# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading a 271 down to the chart's answer (``app.claims.eligibility``).

Each recorded (or constructed — see the fixtures README) 271 must produce
the summary the chart renders: active, inactive, a behavioral carve-out
naming its administrator, and a payer's AAA refusal as its own state rather
than "no coverage". Plus the 270 side: what the inquiry asks, for a client
who is the subscriber and for one who is a dependent.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from app.claims.eligibility import (
    BillingIdentity,
    EligibilityNotPossibleError,
    _raise_enrollment_task_if_needed,
    build_270,
    identity_matches_on_file,
    next_retry_delay_seconds,
    reject_disposition,
    strip_card_issuer_prefix,
    summarize_271,
    summary_for_coverage,
)
from app.claims.events import (
    ClaimEvent,
    clear_claim_event_listeners,
    compliance_reminder_listener,
    register_claim_event_listener,
)
from app.models.claims_transport import (
    EligibilityBenefit,
    EligibilityRelatedEntity,
    EligibilityResponse,
    EligibilitySubscriber,
)
from app.models.coverage import PatientCoverage, Payer
from app.models.eligibility import AaaError, EligibilitySummary
from app.models.patient import Patient

if TYPE_CHECKING:
    from collections.abc import Iterator

_FIXTURES = Path(__file__).parent / "fixtures" / "clearinghouse"
_CHECKED_AT = datetime(2026, 9, 6, 15, 0, tzinfo=UTC)


def _response(name: str) -> EligibilityResponse:
    return EligibilityResponse.model_validate(json.loads((_FIXTURES / name).read_text()))


def _line(**fields: object) -> EligibilityBenefit:
    return EligibilityBenefit.model_validate(fields)


def _behavioral_active_line(response: EligibilityResponse) -> EligibilityBenefit:
    """The recorded member's active line that names ``MH``, with its
    pharmacy carve-out line dropped so only this line can name an entity."""
    response.benefitsInformation = [b for b in response.benefitsInformation if b.code != "U"]
    line = next(
        b for b in response.benefitsInformation if b.code == "1" and "MH" in b.serviceTypeCodes
    )
    line.benefitsRelatedEntities = []
    return line


class TestActive:
    def test_plan_active_with_the_door_price_and_deductible(self) -> None:
        summary = summarize_271(_response("eligibility_271_active.json"), checked_at=_CHECKED_AT)

        assert summary.status == "active"
        assert summary.checked_at == _CHECKED_AT
        assert summary.payer_name == "UNITEDHEALTHCARE"
        assert summary.plan_name == "Gold Plan HMO"
        assert summary.plan_begin == "2024-01-01"
        # The mock prices other service types (physical therapy, specialist
        # visits) but not mental health, so no copay is read off them: the
        # only figure that applies is the plan-level remaining deductible.
        assert summary.copay_cents is None
        assert summary.coinsurance_pct is None
        assert summary.deductible_remaining_cents == 0
        assert summary.visit_limit is None
        assert summary.carveout_administrator is None
        assert summary.aaa_errors == []

    def test_behavioral_prices_win_over_plan_level_ones(self) -> None:
        response = _response("eligibility_271_active.json")
        response.benefitsInformation.extend(
            [
                _line(
                    code="B", serviceTypeCodes=["MH"], timeQualifierCode="27", benefitAmount="25"
                ),
                _line(
                    code="B", serviceTypeCodes=["30"], timeQualifierCode="27", benefitAmount="40"
                ),
                _line(code="A", serviceTypeCodes=["A4"], benefitPercent="0.2"),
                _line(
                    code="C",
                    serviceTypeCodes=["MH"],
                    timeQualifierCode="29",
                    coverageLevelCode="IND",
                    benefitAmount="312.50",
                ),
            ]
        )

        summary = summarize_271(response, checked_at=_CHECKED_AT)

        assert summary.copay_cents == 2500
        assert summary.coinsurance_pct == 20.0
        assert summary.deductible_remaining_cents == 31250

    def test_the_mock_pharmacy_carveout_is_not_read_as_behavioral(self) -> None:
        # The recorded member has a code-U line for pharmacy (service type 88,
        # OPTUMRX). That is somebody else's benefit, not this specialty's.
        summary = summarize_271(_response("eligibility_271_active.json"), checked_at=_CHECKED_AT)
        assert summary.carveout_administrator is None


class TestInactive:
    def test_plan_inactive(self) -> None:
        summary = summarize_271(_response("eligibility_271_inactive.json"), checked_at=_CHECKED_AT)

        assert summary.status == "inactive"
        assert summary.payer_name == "UNITEDHEALTHCARE"
        assert summary.copay_cents is None
        assert summary.deductible_remaining_cents is None
        assert summary.aaa_errors == []


class TestCarveout:
    def test_names_the_administrator_and_its_payer_id(self) -> None:
        summary = summarize_271(
            _response("eligibility_271_carveout_behavioral.json"), checked_at=_CHECKED_AT
        )

        assert summary.status == "active"
        assert summary.carveout_administrator is not None
        assert summary.carveout_administrator.name == "EXAMPLE BEHAVIORAL HEALTH"
        assert summary.carveout_administrator.payer_id == "EXBH1"

    def test_an_active_behavioral_line_from_another_payer_is_a_carveout(self) -> None:
        response = _response("eligibility_271_active.json")
        _behavioral_active_line(response).benefitsRelatedEntity = EligibilityRelatedEntity(
            entityIdentifier="Third-Party Administrator",
            entityName="OTHER ADMINISTRATOR",
            entityIdentification="PI",
            entityIdentificationValue="OTH99",
        )

        summary = summarize_271(response, checked_at=_CHECKED_AT)

        assert summary.carveout_administrator is not None
        assert summary.carveout_administrator.name == "OTHER ADMINISTRATOR"
        assert summary.carveout_administrator.payer_id == "OTH99"

    def test_the_responding_payer_on_a_behavioral_line_is_not_a_carveout(self) -> None:
        response = _response("eligibility_271_active.json")
        _behavioral_active_line(response).benefitsRelatedEntity = EligibilityRelatedEntity(
            entityIdentifier="Payer",
            entityName="UNITEDHEALTHCARE",
            entityIdentification="PI",
            entityIdentificationValue="87726",
        )

        assert summarize_271(response, checked_at=_CHECKED_AT).carveout_administrator is None


class TestAaaError:
    def test_a_refusal_is_its_own_state_with_the_resolution_text(self) -> None:
        summary = summarize_271(
            _response("eligibility_271_aaa_invalid_member_id.json"), checked_at=_CHECKED_AT
        )

        assert summary.status == "error"
        assert summary.copay_cents is None
        assert [e.code for e in summary.aaa_errors] == ["72"]
        error = summary.aaa_errors[0]
        assert error.description == "Invalid/Missing Subscriber/Insured ID"
        assert error.followup_action == "Please Correct and Resubmit"
        assert error.resolution is not None
        assert "member ID" in error.resolution


class TestStatusRules:
    def test_a_behavioral_answer_outranks_the_plan_level_one(self) -> None:
        response = _response("eligibility_271_active.json")
        # The plan stays active (code 1 on service type 30); only the
        # behavioral benefit lapses, in both places the vendor reports it.
        _behavioral_active_line(response).code = "6"
        for status in response.planStatus:
            if "MH" in status.serviceTypeCodes:
                status.statusCode = "6"

        assert summarize_271(response, checked_at=_CHECKED_AT).status == "inactive"

    def test_no_answer_either_way_is_unknown(self) -> None:
        response = _response("eligibility_271_active.json")
        response.planStatus = []
        response.benefitsInformation = [
            b for b in response.benefitsInformation if b.code not in ("1", "6")
        ]

        assert summarize_271(response, checked_at=_CHECKED_AT).status == "unknown"

    def test_visit_limits_and_authorization_are_read_from_behavioral_lines(self) -> None:
        response = _response("eligibility_271_active.json")
        response.benefitsInformation.extend(
            [
                _line(
                    code="F",
                    serviceTypeCodes=["MH"],
                    timeQualifierCode="23",
                    benefitQuantity="30",
                    quantityQualifierCode="VS",
                ),
                _line(
                    code="F",
                    serviceTypeCodes=["MH"],
                    timeQualifierCode="29",
                    benefitQuantity="12",
                    quantityQualifierCode="VS",
                ),
                _line(code="1", serviceTypeCodes=["A4"], authOrCertIndicator="Y"),
            ]
        )

        summary = summarize_271(response, checked_at=_CHECKED_AT)

        assert summary.visit_limit is not None
        assert summary.visit_limit.total == 30
        assert summary.visit_limit.remaining == 12
        assert summary.requires_authorization is True


class TestStoredOnTheRow:
    def test_summary_is_rebuilt_from_the_stored_271(self) -> None:
        response = _response("eligibility_271_active.json")
        coverage = _coverage(
            last_271=response.model_dump(mode="json", exclude_none=True),
            verified_at=_CHECKED_AT,
        )

        summary = summary_for_coverage(coverage)

        assert summary is not None
        assert summary.status == "active"
        assert summary.checked_at == _CHECKED_AT

    def test_nothing_before_a_check(self) -> None:
        assert summary_for_coverage(_coverage()) is None


# ---------------------------------------------------------------------------
# The 270 side
# ---------------------------------------------------------------------------


_NOW = datetime(2026, 9, 1, tzinfo=UTC)


def _payer(**overrides: object) -> Payer:
    fields: dict[str, object] = {
        "id": "payer-row",
        "name": "UnitedHealthcare",
        "payer_id": "87726",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    fields.update(overrides)
    return Payer(**fields)  # type: ignore[arg-type] — test factory over a dict of overrides


def _coverage(**overrides: object) -> PatientCoverage:
    fields: dict[str, object] = {
        "id": "cov-1",
        "patient_id": "patient-1",
        "payer_id": "payer-row",
        "member_id": "UHC123456",
        "group_number": "111222",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    fields.update(overrides)
    return PatientCoverage(**fields)  # type: ignore[arg-type] — test factory over a dict of overrides


def _patient() -> Patient:
    return Patient(
        id="patient-1",
        first_name="Jane",
        last_name="Doe",
        date_of_birth="1971-01-01",
        sex="F",
        created_at=_NOW,
        updated_at=_NOW,
    )


_IDENTITY = BillingIdentity(npi="1999999984", organization_name="Example Practice")


class TestBuild270:
    def test_asks_about_mental_health_for_the_client_as_subscriber(self) -> None:
        inquiry = build_270(_coverage(), _payer(), _patient(), _IDENTITY)

        assert inquiry.tradingPartnerServiceId == "87726"
        assert inquiry.encounter is not None
        assert inquiry.encounter.serviceTypeCodes == ["MH"]
        assert inquiry.provider.npi == "1999999984"
        assert inquiry.provider.organizationName == "Example Practice"
        assert inquiry.subscriber.memberId == "UHC123456"
        assert inquiry.subscriber.firstName == "Jane"
        assert inquiry.subscriber.dateOfBirth == "19710101"
        assert inquiry.subscriber.gender == "F"
        assert inquiry.subscriber.groupNumber == "111222"
        # Not an empty list: the vendor refuses ``dependents: []``.
        assert inquiry.dependents is None
        assert "dependents" not in inquiry.model_dump(exclude_none=True)

    def test_a_dependent_client_rides_under_the_subscriber(self) -> None:
        coverage = _coverage(
            subscriber_relationship="child",
            subscriber_first_name="Parent",
            subscriber_last_name="Doe",
            subscriber_date_of_birth=date(1945, 5, 5),
            subscriber_sex="M",
        )

        inquiry = build_270(coverage, _payer(), _patient(), _IDENTITY)

        assert inquiry.subscriber.firstName == "Parent"
        assert inquiry.subscriber.dateOfBirth == "19450505"
        assert inquiry.subscriber.gender == "M"
        assert inquiry.dependents is not None
        assert [d.firstName for d in inquiry.dependents] == ["Jane"]
        assert inquiry.dependents[0].dateOfBirth == "19710101"

    def test_prefers_the_clearinghouse_payer_id_when_known(self) -> None:
        inquiry = build_270(
            _coverage(), _payer(clearinghouse_payer_id="UHC-CH"), _patient(), _IDENTITY
        )
        assert inquiry.tradingPartnerServiceId == "UHC-CH"

    def test_a_payer_without_an_electronic_id_cannot_be_asked(self) -> None:
        with pytest.raises(EligibilityNotPossibleError):
            build_270(_coverage(), _payer(payer_id="UNKNOWN"), _patient(), _IDENTITY)

    def test_a_clinician_identity_is_sent_as_a_person(self) -> None:
        identity = BillingIdentity(npi="1234567893", first_name="Sam", last_name="Clinician")
        inquiry = build_270(_coverage(), _payer(), _patient(), identity)

        assert inquiry.provider.organizationName is None
        assert inquiry.provider.firstName == "Sam"
        assert inquiry.provider.lastName == "Clinician"


# ---------------------------------------------------------------------------
# What an AAA rejection means
# ---------------------------------------------------------------------------


class TestRejectDisposition:
    @pytest.mark.parametrize(
        ("codes", "expected"),
        [
            (["42"], "retry_as_is"),
            (["80"], "retry_as_is"),
            (["79", "42"], "retry_as_is"),
            (["79"], "stop_manual"),
            (["75"], "fix_then_retry"),
            (["72"], "fix_then_retry"),
            (["73"], "fix_then_retry"),
            (["65"], "fix_then_retry"),
            (["67"], "fix_then_retry"),
            (["56"], "fix_then_retry"),
            (["57"], "fix_then_retry"),
            (["58"], "fix_then_retry"),
            (["41"], "stop_enrollment"),
            (["43"], "stop_enrollment"),
            (["51"], "stop_enrollment"),
            (["99"], "stop_manual"),
        ],
    )
    def test_each_listed_code_gets_its_disposition(self, codes: list[str], expected: str) -> None:
        assert reject_disposition(codes) == expected

    def test_an_http_429_retries_even_with_no_aaa_code_at_all(self) -> None:
        assert reject_disposition([], http_status=429) == "retry_as_is"

    def test_an_enrollment_gap_wins_over_a_retryable_code(self) -> None:
        assert reject_disposition(["42", "43"]) == "stop_enrollment"

    def test_a_lone_79_never_gets_a_retry_disposition(self) -> None:
        assert reject_disposition(["79"]) != "retry_as_is"


class TestRetryBounds:
    def test_realtime_retries_immediately_within_the_two_minute_budget(self) -> None:
        assert next_retry_delay_seconds(attempt=1, elapsed_seconds=0, realtime=True) == 0.0
        assert next_retry_delay_seconds(attempt=5, elapsed_seconds=119, realtime=True) == 0.0

    def test_realtime_gives_up_once_the_budget_is_spent(self) -> None:
        assert next_retry_delay_seconds(attempt=6, elapsed_seconds=121, realtime=True) is None

    def test_scheduled_backs_off_from_one_minute_capped_at_thirty(self) -> None:
        assert next_retry_delay_seconds(attempt=1, elapsed_seconds=0, realtime=False) == 60.0
        assert next_retry_delay_seconds(attempt=2, elapsed_seconds=0, realtime=False) == 120.0
        assert next_retry_delay_seconds(attempt=3, elapsed_seconds=0, realtime=False) == 240.0
        assert next_retry_delay_seconds(attempt=10, elapsed_seconds=0, realtime=False) == 1800.0


class TestCardIssuerPrefix:
    def test_strips_the_80840_prefix(self) -> None:
        assert strip_card_issuer_prefix("80840123456789") == "123456789"

    def test_leaves_a_member_id_without_the_prefix_alone(self) -> None:
        assert strip_card_issuer_prefix("UHC123456") == "UHC123456"

    def test_build_270_never_sends_a_member_id_with_the_prefix(self) -> None:
        coverage = _coverage(member_id="80840UHC123456")

        inquiry = build_270(coverage, _payer(), _patient(), _IDENTITY)

        assert inquiry.subscriber.memberId == "UHC123456"


class TestIdentityRelaxingRetrySafety:
    """The safety guard for a 72/75 retry that drops the member id or varies
    the name: the retry may relax how the client is described, but the
    answer it comes back with must still be about that same client."""

    def test_matching_demographics_are_accepted(self) -> None:
        returned = EligibilitySubscriber(
            memberId="anything", lastName="Doe", dateOfBirth="19710101"
        )
        assert identity_matches_on_file(returned, patient=_patient(), coverage=_coverage())

    def test_a_mismatched_date_of_birth_is_never_accepted(self) -> None:
        returned = EligibilitySubscriber(
            memberId="anything", lastName="Doe", dateOfBirth="19800101"
        )
        assert not identity_matches_on_file(returned, patient=_patient(), coverage=_coverage())

    def test_a_mismatched_name_is_never_accepted(self) -> None:
        returned = EligibilitySubscriber(
            memberId="anything", lastName="Smith", dateOfBirth="19710101"
        )
        assert not identity_matches_on_file(returned, patient=_patient(), coverage=_coverage())

    def test_a_hyphen_or_case_difference_is_not_a_mismatch(self) -> None:
        patient = _patient()
        patient.last_name = "Anne-Marie"
        returned = EligibilitySubscriber(
            memberId="anything", lastName="annemarie", dateOfBirth="19710101"
        )
        assert identity_matches_on_file(returned, patient=patient, coverage=_coverage())

    def test_a_payer_that_echoes_no_demographics_is_not_blocked(self) -> None:
        returned = EligibilitySubscriber(memberId="anything")
        assert identity_matches_on_file(returned, patient=_patient(), coverage=_coverage())

    def test_a_dependents_retry_checks_the_subscriber_not_the_client(self) -> None:
        coverage = _coverage(
            subscriber_relationship="child",
            subscriber_first_name="Parent",
            subscriber_last_name="Doe",
            subscriber_date_of_birth=date(1945, 5, 5),
            subscriber_sex="M",
        )
        # Matches the dependent client, not the subscriber the retry is
        # actually asking the payer to confirm — must not be accepted.
        returned = EligibilitySubscriber(
            memberId="anything", lastName="Doe", dateOfBirth="19710101"
        )
        assert not identity_matches_on_file(returned, patient=_patient(), coverage=coverage)


@pytest.fixture
def listeners() -> Iterator[None]:
    """Start each test with no listeners; put the default back afterwards."""
    clear_claim_event_listeners()
    yield
    clear_claim_event_listeners()
    register_claim_event_listener(compliance_reminder_listener)


@pytest.mark.usefixtures("listeners")
class TestEnrollmentTask:
    """41/43/51 mean the practice isn't enrolled with the payer; that is an
    enrollment task raised through the claim-events surface, not a note
    rendered to the clinician as a failed eligibility check."""

    def _summary(self, code: str) -> EligibilitySummary:
        return EligibilitySummary(
            status="error",
            checked_at=_CHECKED_AT,
            aaa_errors=[AaaError(code=code, description="d", followup_action="f")],
        )

    def test_provider_not_enrolled_raises_a_claim_event(self) -> None:
        events: list[ClaimEvent] = []
        register_claim_event_listener(lambda _session, event: events.append(event))

        _raise_enrollment_task_if_needed(
            session=object(),
            coverage=_coverage(),
            payer=_payer(),
            user_id="user-1",
            summary=self._summary("43"),
            checked_at=_CHECKED_AT,
        )

        assert len(events) == 1
        assert events[0].kind == "enrollment_action_required"
        assert events[0].control_number == "cov-1"
        assert events[0].payer_id == "87726"

    def test_a_retryable_code_does_not_raise_an_enrollment_task(self) -> None:
        events: list[ClaimEvent] = []
        register_claim_event_listener(lambda _session, event: events.append(event))

        _raise_enrollment_task_if_needed(
            session=object(),
            coverage=_coverage(),
            payer=_payer(),
            user_id="user-1",
            summary=self._summary("42"),
            checked_at=_CHECKED_AT,
        )

        assert events == []

    def test_no_session_means_no_event(self) -> None:
        events: list[ClaimEvent] = []
        register_claim_event_listener(lambda _session, event: events.append(event))

        _raise_enrollment_task_if_needed(
            session=None,
            coverage=_coverage(),
            payer=_payer(),
            user_id="user-1",
            summary=self._summary("43"),
            checked_at=_CHECKED_AT,
        )

        assert events == []
