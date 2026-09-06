# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The stored claim to the clearinghouse's request body.

The baseline test claim mirrors the recorded ``837p_request_test_payer.json``
value for value, so the mapping's output is compared against the fixture as
the vendor accepted it — same money strings, same date format, same
dotless diagnosis code, same control numbers on the claim and the line.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from app.claims.wire import ClaimMappingError, to_submission_request
from app.models.claims_transport import Address, ClaimSubmissionRequest, Dependent

from tests.claims_fixtures import billing_snapshot, claim, line, person, subscriber_snapshot

_FIXTURE = Path(__file__).parent / "fixtures" / "clearinghouse" / "837p_request_test_payer.json"


def _build(**overrides: object) -> ClaimSubmissionRequest:
    return to_submission_request(
        claim(**overrides),
        trading_partner_service_id="STEDI",
        usage_indicator="T",
        tax_id="84-4459714",
        submitter_identification="0000001",
        receiver_name="Stedi",
    )


def test_matches_the_recorded_request_the_vendor_accepted() -> None:
    expected = ClaimSubmissionRequest.model_validate_json(_FIXTURE.read_text())
    built = _build()
    # The recorded request was made with a separate submitter phone; the
    # mapping uses the practice's billing phone for both contacts.
    expected.submitter.contactInformation.phoneNumber = (
        built.submitter.contactInformation.phoneNumber
    )
    assert built.model_dump() == expected.model_dump()


def test_money_dates_and_codes_cross_to_the_wire_format() -> None:
    built = _build(
        diagnosis_codes=["F41.1", "F33.1"],
        lines=[line(charge_cents=15050, units=2, dx_pointers=[1, 2], modifiers=[])],
        total_charge_cents=15050,
    )
    info = built.claimInformation
    assert info.claimChargeAmount == "150.50"
    assert [(d.diagnosisTypeCode, d.diagnosisCode) for d in info.healthCareCodeInformation] == [
        ("ABK", "F411"),
        ("ABF", "F331"),
    ]
    service = info.serviceLines[0]
    assert service.serviceDate == "20260901"
    assert service.professionalService.lineItemChargeAmount == "150.50"
    assert service.professionalService.serviceUnitCount == "2"
    assert service.professionalService.procedureModifiers == []
    assert service.professionalService.compositeDiagnosisCodePointers.diagnosisCodePointers == [
        "1",
        "2",
    ]
    assert built.subscriber.dateOfBirth == "20000101"
    assert built.billing.employerId == "844459714"


def test_lines_go_out_in_line_order() -> None:
    second = line(
        id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        line_number=2,
        line_control_number="886598912",
        cpt="90833",
        charge_cents=6000,
    )
    first = line(cpt="99214")
    built = _build(lines=[second, first], total_charge_cents=21000)
    assert [s.providerControlNumber for s in built.claimInformation.serviceLines] == [
        "886598911",
        "886598912",
    ]


def test_frequency_code_is_carried_for_a_correction() -> None:
    built = _build(frequency_code="7", parent_claim_id="parent")
    assert built.claimInformation.claimFrequencyCode == "7"


def test_phone_and_postal_code_are_digits_only() -> None:
    built = _build(
        billing_snapshot=billing_snapshot(phone="(555) 333-4444", postal_code="30301-0000")
    )
    assert built.billing.contactInformation.phoneNumber == "5553334444"
    assert built.billing.address.postalCode == "303010000"


def test_missing_wire_fields_are_named_together() -> None:
    with pytest.raises(ClaimMappingError) as excinfo:
        _build(
            billing_snapshot=billing_snapshot(phone=None, taxonomy_code=None),
            subscriber_snapshot=subscriber_snapshot(
                subscriber=person(sex=None), patient=person(sex=None)
            ),
        )
    assert excinfo.value.missing == [
        "billing_provider.phone",
        "rendering_provider.taxonomy_code",
        "subscriber.sex",
    ]


def test_a_self_subscriber_claim_carries_no_dependent_loop() -> None:
    assert _build().dependent is None
    assert "dependent" not in _build().model_dump(exclude_none=True)


def test_a_dependent_claim_puts_the_policy_holder_in_subscriber_and_the_client_in_dependent() -> (
    None
):
    parent = person(
        first_name="Pat",
        last_name="Anon",
        date_of_birth=date(1975, 5, 5),
        sex="F",
        address_line1="4444 Other Ave",
        postal_code="30302-1111",
    )
    child = person(first_name="Sam", date_of_birth=date(2012, 3, 4))
    built = _build(
        subscriber_snapshot=subscriber_snapshot(
            relationship="child", subscriber=parent, patient=child
        )
    )

    expected = ClaimSubmissionRequest.model_validate_json(_FIXTURE.read_text())
    expected.submitter.contactInformation.phoneNumber = (
        built.submitter.contactInformation.phoneNumber
    )
    expected.subscriber.firstName = "Pat"
    expected.subscriber.lastName = "Anon"
    expected.subscriber.gender = "F"
    expected.subscriber.dateOfBirth = "19750505"
    expected.subscriber.address = Address(
        address1="4444 Other Ave", city="Atlanta", state="GA", postalCode="303021111"
    )
    expected.dependent = Dependent(
        relationshipToSubscriberCode="19",
        firstName="Sam",
        lastName="Anon",
        gender="M",
        dateOfBirth="20120304",
        address=Address(
            address1="2222 Random St", city="Atlanta", state="GA", postalCode="303010000"
        ),
    )
    assert built.model_dump() == expected.model_dump()
    # The plan is the policy holder's: the member id stays on the subscriber.
    assert built.subscriber.memberId == "123456789"
    assert built.dependent is not None
    assert built.dependent.memberId is None


@pytest.mark.parametrize(
    ("relationship", "code"), [("spouse", "01"), ("child", "19"), ("other", "G8")]
)
def test_the_coverage_relationship_becomes_the_vendor_code(relationship: str, code: str) -> None:
    parent = person(first_name="Pat", date_of_birth=date(1975, 5, 5))
    built = _build(
        subscriber_snapshot=subscriber_snapshot(relationship=relationship, subscriber=parent)
    )
    assert built.dependent is not None
    assert built.dependent.relationshipToSubscriberCode == code


def test_missing_dependent_fields_are_named_with_the_patient_prefix() -> None:
    parent = person(first_name="Pat", date_of_birth=date(1975, 5, 5))
    with pytest.raises(ClaimMappingError) as excinfo:
        _build(
            subscriber_snapshot=subscriber_snapshot(
                relationship="spouse",
                subscriber=parent,
                patient=person(date_of_birth=None, postal_code=None),
            )
        )
    assert excinfo.value.missing == ["patient.date_of_birth", "patient.postal_code"]
