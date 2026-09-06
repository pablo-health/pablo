# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Building a claim from a session: what gets copied, from where, and when.

The pure ``assemble_draft`` is exercised directly with domain objects; the
loading front-end ``build_claim_from_session`` and the correction / void
builders run against the in-memory repositories.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from app.claims.assembly import (
    AppointmentNotFoundError,
    ClaimSources,
    ClientNotFoundError,
    NoActiveCoverageError,
    assemble_draft,
    build_claim_from_session,
    build_corrected_claim,
    build_void_claim,
    line_control_number,
    new_control_number,
)
from app.claims.scrub import scrub
from app.models import User
from app.models.claims import AddOnService
from app.models.coverage import PatientCoverage, Payer
from app.models.patient import Patient
from app.repositories.clinician_profile import (
    ClinicianProfile,
    InMemoryClinicianProfileRepository,
)
from app.repositories.coverage import InMemoryPatientCoverageRepository, InMemoryPayerRepository
from app.repositories.patient import InMemoryPatientRepository
from app.scheduling_engine.models.appointment import Appointment
from app.scheduling_engine.models.appointment_type import AppointmentType
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.appointment_type import (
    InMemoryAppointmentTypeRepository,
)

from tests.claims_fixtures import APPOINTMENT_ID, COVERAGE_ID, PATIENT_ID, PAYER_ROW_ID, USER_ID

_NOW = datetime(2026, 9, 2, 15, 0, tzinfo=UTC)
_TYPE_ID = "66666666-6666-4666-8666-666666666666"
_EASTERN = ZoneInfo("America/New_York")

# 01:30 UTC on the 2nd is the evening of the 1st in New York.
_START_UTC = datetime(2026, 9, 2, 1, 30, tzinfo=UTC)

_BILLING_PROFILE: dict[str, object] = {
    "legal_name": "Pablo Test Practice",
    "tax_id_last4": "9714",
    "tax_id_type": "ein",
    "billing_npi": None,
    "address_line1": "123 Some St",
    "address_line2": None,
    "city": "Atlanta",
    "state": "GA",
    "postal_code": "303010000",
    "phone": "5553334444",
}


def _user(**overrides: Any) -> User:
    fields: dict[str, Any] = {
        "id": USER_ID,
        "email": "therapist@example.com",
        "name": "Jane Smith",
        "legal_name": "Jane Q. Smith, LCSW",
        "created_at": _NOW,
    }
    fields.update(overrides)
    return User(**fields)


def _appointment(**overrides: Any) -> Appointment:
    fields: dict[str, Any] = {
        "id": APPOINTMENT_ID,
        "user_id": USER_ID,
        "patient_id": PATIENT_ID,
        "title": "Session",
        "start_at": _START_UTC,
        "end_at": _START_UTC,
        "duration_minutes": 53,
        "status": "completed",
        "session_type": "individual",
        "appointment_type_id": _TYPE_ID,
        "video_link": "https://video.example/room",
        "service_code": "90837",
        "modifiers": ["95"],
        "unit_count": 1,
        "place_of_service": "10",
        "diagnosis_codes": ["F41.1", "F33.1"],
    }
    fields.update(overrides)
    return Appointment(**fields)


def _appointment_type(**overrides: Any) -> AppointmentType:
    fields: dict[str, Any] = {
        "id": _TYPE_ID,
        "user_id": USER_ID,
        "name": "Individual",
        "default_fee_cents": 15000,
    }
    fields.update(overrides)
    return AppointmentType(**fields)


def _patient(**overrides: Any) -> Patient:
    fields: dict[str, Any] = {
        "id": PATIENT_ID,
        "first_name": "John",
        "last_name": "Anon",
        "created_at": _NOW,
        "updated_at": _NOW,
        "date_of_birth": "2000-01-01",
        "sex": "M",
        "address_line1": "2222 Random St",
        "city": "Atlanta",
        "state": "GA",
        "postal_code": "303010000",
        "phone": "5551112222",
    }
    fields.update(overrides)
    return Patient(**fields)


def _coverage(**overrides: Any) -> PatientCoverage:
    fields: dict[str, Any] = {
        "id": COVERAGE_ID,
        "patient_id": PATIENT_ID,
        "payer_id": PAYER_ROW_ID,
        "member_id": "123456789",
        "group_number": "3335555",
        "plan_name": "Choice",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    fields.update(overrides)
    return PatientCoverage(**fields)


def _payer(**overrides: Any) -> Payer:
    fields: dict[str, Any] = {
        "id": PAYER_ROW_ID,
        "name": "Stedi Test Payer",
        "payer_id": "STEDI",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    fields.update(overrides)
    return Payer(**fields)


def _profile(**overrides: Any) -> ClinicianProfile:
    fields: dict[str, Any] = {
        "user_id": USER_ID,
        "practice_id": "practice-1",
        "npi_number": "1999999984",
        "taxonomy_code": "101YM0800X",
    }
    fields.update(overrides)
    return ClinicianProfile(**fields)


def _draft(**overrides: Any) -> Any:
    inputs: dict[str, Any] = {
        "appointment": _appointment(),
        "patient": _patient(),
        "coverage": _coverage(),
        "payer": _payer(),
        "appointment_type": _appointment_type(),
        "billing_profile": _BILLING_PROFILE,
        "rendering_provider": _profile(),
        "user": _user(),
        "timezone": _EASTERN,
        "now": _NOW,
    }
    inputs.update(overrides)
    return assemble_draft(**inputs)


# ---------------------------------------------------------------------------
# Control numbers
# ---------------------------------------------------------------------------


def test_control_number_fits_the_wire_and_sorts_by_time() -> None:
    earlier = new_control_number(datetime(2026, 1, 1, tzinfo=UTC))
    later = new_control_number(datetime(2026, 9, 1, tzinfo=UTC))
    assert len(earlier) == len(later) <= 17
    assert earlier < later
    assert earlier.isupper() or earlier.isdigit()


def test_control_numbers_do_not_repeat() -> None:
    numbers = {new_control_number(_NOW) for _ in range(200)}
    assert len(numbers) == 200


def test_line_control_number_derives_from_the_claim() -> None:
    assert line_control_number("ABC123", 2) == "ABC123L2"


# ---------------------------------------------------------------------------
# assemble_draft: the snapshot
# ---------------------------------------------------------------------------


def test_draft_copies_the_visit_codes_and_the_resolved_rate() -> None:
    draft = _draft()
    assert draft.state == "draft"
    assert draft.frequency_code == "1"
    assert draft.parent_claim_id is None
    assert draft.diagnosis_codes == ["F41.1", "F33.1"]
    assert draft.place_of_service == "10"
    assert len(draft.lines) == 1
    visit = draft.lines[0]
    assert visit.appointment_id == APPOINTMENT_ID
    assert visit.cpt == "90837"
    assert visit.modifiers == ["95"]
    assert visit.units == 1
    assert visit.charge_cents == 15000
    assert visit.dx_pointers == [1, 2]
    assert visit.telehealth is True
    assert visit.line_control_number == line_control_number(draft.control_number, 1)
    assert draft.total_charge_cents == 15000
    assert scrub(draft, today=date(2026, 9, 6)) == []


def test_service_date_is_the_practice_local_date() -> None:
    assert _draft().lines[0].service_date == date(2026, 9, 1)
    assert _draft(timezone=UTC).lines[0].service_date == date(2026, 9, 2)


def test_client_rate_override_beats_the_type_default() -> None:
    draft = _draft(patient=_patient(rate_cents=12000))
    assert draft.lines[0].charge_cents == 12000
    assert draft.total_charge_cents == 12000


def test_no_rate_anywhere_writes_zero_and_leaves_it_to_the_scrub() -> None:
    draft = _draft(patient=_patient(rate_cents=None), appointment_type=None)
    assert draft.lines[0].charge_cents == 0
    assert [f.code for f in scrub(draft, today=date(2026, 9, 6))] == ["charge_zero"]


def test_add_on_makes_a_second_line_on_the_same_date() -> None:
    draft = _draft(
        appointment=_appointment(service_code="99214"),
        add_on=AddOnService(cpt="90833", charge_cents=6000),
    )
    assert [(line.line_number, line.cpt, line.charge_cents) for line in draft.lines] == [
        (1, "99214", 15000),
        (2, "90833", 6000),
    ]
    assert draft.lines[1].service_date == draft.lines[0].service_date
    assert draft.lines[1].line_control_number == line_control_number(draft.control_number, 2)
    assert draft.total_charge_cents == 21000


def test_in_person_visit_is_not_marked_telehealth() -> None:
    draft = _draft(appointment=_appointment(video_link=None, place_of_service="11"))
    assert draft.lines[0].telehealth is False


def test_billing_snapshot_carries_the_practice_and_the_clinician() -> None:
    billing = _draft().billing_snapshot
    assert billing.billing_provider.legal_name == "Pablo Test Practice"
    assert billing.billing_provider.tax_id_last4 == "9714"
    assert billing.billing_provider.tax_id_type == "ein"
    # No practice NPI on file: the claim bills under the clinician's.
    assert billing.billing_provider.npi == "1999999984"
    assert billing.rendering_provider.user_id == USER_ID
    assert billing.rendering_provider.first_name == "Jane"
    assert billing.rendering_provider.last_name == "Q. Smith"
    assert billing.rendering_provider.npi == "1999999984"
    assert billing.rendering_provider.taxonomy_code == "101YM0800X"


def test_practice_npi_is_used_when_set() -> None:
    profile = {**_BILLING_PROFILE, "billing_npi": "1234567893"}
    assert _draft(billing_profile=profile).billing_snapshot.billing_provider.npi == "1234567893"


def test_no_clinician_profile_leaves_the_rendering_identifiers_blank() -> None:
    billing = _draft(rendering_provider=None).billing_snapshot
    assert billing.rendering_provider.npi is None
    assert billing.rendering_provider.taxonomy_code is None
    assert billing.billing_provider.npi is None


def test_self_subscriber_snapshot_is_the_client() -> None:
    snapshot = _draft().subscriber_snapshot
    assert snapshot.relationship == "self"
    assert snapshot.member_id == "123456789"
    assert snapshot.group_number == "3335555"
    assert snapshot.payer_id == "STEDI"
    assert snapshot.payer_name == "Stedi Test Payer"
    assert snapshot.coverage_active is True
    assert snapshot.subscriber == snapshot.patient
    assert snapshot.patient.date_of_birth == date(2000, 1, 1)
    assert snapshot.patient.sex == "M"
    assert snapshot.patient.address_line1 == "2222 Random St"


def test_other_subscriber_snapshot_comes_from_the_coverage() -> None:
    coverage = _coverage(
        subscriber_relationship="child",
        subscriber_first_name="Parent",
        subscriber_last_name="Person",
        subscriber_date_of_birth=date(1975, 5, 5),
        subscriber_sex="F",
        subscriber_address_line1="9 Elm St",
        subscriber_city="Atlanta",
        subscriber_state="GA",
        subscriber_postal_code="30301",
    )
    snapshot = _draft(coverage=coverage).subscriber_snapshot
    assert snapshot.relationship == "child"
    assert snapshot.subscriber.first_name == "Parent"
    assert snapshot.subscriber.date_of_birth == date(1975, 5, 5)
    assert snapshot.subscriber.sex == "F"
    assert snapshot.patient.first_name == "John"


def test_unparseable_client_dob_is_left_blank_for_the_scrub() -> None:
    snapshot = _draft(patient=_patient(date_of_birth="unknown")).subscriber_snapshot
    assert snapshot.patient.date_of_birth is None


def test_missing_codes_are_copied_as_absent_not_invented() -> None:
    draft = _draft(
        appointment=_appointment(
            service_code=None,
            modifiers=None,
            unit_count=None,
            place_of_service=None,
            diagnosis_codes=None,
        )
    )
    assert draft.diagnosis_codes == []
    assert draft.place_of_service is None
    assert draft.lines[0].cpt == ""
    assert draft.lines[0].modifiers == []
    assert draft.lines[0].units == 1
    assert draft.lines[0].dx_pointers == []


# ---------------------------------------------------------------------------
# build_claim_from_session: loading through the repositories
# ---------------------------------------------------------------------------


@pytest.fixture
def sources() -> ClaimSources:
    appointments = InMemoryAppointmentRepository()
    appointments.grant_access(PATIENT_ID, USER_ID)
    appointments.create(_appointment())
    appointment_types = InMemoryAppointmentTypeRepository()
    appointment_types.create(_appointment_type())
    patients = InMemoryPatientRepository()
    patients.create(_patient(), USER_ID)
    coverage = InMemoryPatientCoverageRepository()
    coverage.create(_coverage())
    payers = InMemoryPayerRepository()
    payers.create(_payer())
    profiles = InMemoryClinicianProfileRepository()
    profiles.create(_profile())
    return ClaimSources(
        appointments=appointments,
        appointment_types=appointment_types,
        patients=patients,
        coverage=coverage,
        payers=payers,
        clinician_profiles=profiles,
        billing_profile=lambda: _BILLING_PROFILE,
        timezone=_EASTERN,
    )


def test_build_from_session_loads_everything(sources: ClaimSources) -> None:
    claim = build_claim_from_session(APPOINTMENT_ID, _user(), sources, now=_NOW)
    assert claim.patient_id == PATIENT_ID
    assert claim.coverage_id == COVERAGE_ID
    assert claim.payer_id == PAYER_ROW_ID
    assert claim.lines[0].charge_cents == 15000
    assert claim.billing_snapshot.rendering_provider.npi == "1999999984"
    assert scrub(claim, today=date(2026, 9, 6)) == []


def test_build_from_session_refuses_an_unknown_appointment(sources: ClaimSources) -> None:
    with pytest.raises(AppointmentNotFoundError):
        build_claim_from_session("nope", _user(), sources)


def test_build_from_session_refuses_an_ungranted_clinician(sources: ClaimSources) -> None:
    with pytest.raises(AppointmentNotFoundError):
        build_claim_from_session(APPOINTMENT_ID, _user(id="someone-else"), sources)


def test_build_from_session_refuses_a_client_without_coverage(sources: ClaimSources) -> None:
    active = sources.coverage.get_active(PATIENT_ID)
    assert active is not None
    sources.coverage.update(active.model_copy(update={"active": False}))
    with pytest.raises(NoActiveCoverageError):
        build_claim_from_session(APPOINTMENT_ID, _user(), sources)


def test_build_from_session_refuses_a_client_the_clinician_cannot_see(
    sources: ClaimSources,
) -> None:
    sources.appointments.grant_access(PATIENT_ID, "other-clinician")
    with pytest.raises(ClientNotFoundError):
        build_claim_from_session(APPOINTMENT_ID, _user(id="other-clinician"), sources)


# ---------------------------------------------------------------------------
# Corrections and voids
# ---------------------------------------------------------------------------


def test_corrected_claim_is_rebuilt_from_todays_sources(sources: ClaimSources) -> None:
    parent = build_claim_from_session(
        APPOINTMENT_ID, _user(), sources, add_on=AddOnService(cpt="90833", charge_cents=6000)
    ).model_copy(update={"state": "rejected"})
    # The visit's codes were fixed after the parent went out.
    sources.appointments.update(_appointment(service_code="99214", diagnosis_codes=["F33.1"]))

    child = build_corrected_claim(parent, _user(), sources, now=_NOW)
    assert child.frequency_code == "7"
    assert child.parent_claim_id == parent.id
    assert child.state == "draft"
    assert child.id != parent.id
    assert child.control_number != parent.control_number
    assert child.diagnosis_codes == ["F33.1"]
    assert [(line.cpt, line.charge_cents) for line in child.lines] == [
        ("99214", 15000),
        ("90833", 6000),
    ]
    # The parent is untouched.
    assert parent.diagnosis_codes == ["F41.1", "F33.1"]
    assert parent.state == "rejected"


def test_corrected_claim_needs_the_visit_it_came_from(sources: ClaimSources) -> None:
    parent = build_claim_from_session(APPOINTMENT_ID, _user(), sources)
    sources.appointments.delete(APPOINTMENT_ID, USER_ID)
    with pytest.raises(AppointmentNotFoundError):
        build_corrected_claim(parent, _user(), sources)


def test_void_restates_the_parent_under_frequency_8(sources: ClaimSources) -> None:
    parent = build_claim_from_session(APPOINTMENT_ID, _user(), sources).model_copy(
        update={"state": "payer_accepted", "total_paid_cents": 500}
    )
    parent.lines[0].paid_cents = 500

    void = build_void_claim(parent, now=_NOW)
    assert void.frequency_code == "8"
    assert void.parent_claim_id == parent.id
    assert void.state == "draft"
    assert void.control_number != parent.control_number
    assert void.total_paid_cents == 0
    assert void.subscriber_snapshot == parent.subscriber_snapshot
    assert void.billing_snapshot == parent.billing_snapshot
    assert void.diagnosis_codes == parent.diagnosis_codes
    assert len(void.lines) == 1
    copied = void.lines[0]
    assert copied.id != parent.lines[0].id
    assert copied.claim_id == void.id
    assert copied.cpt == parent.lines[0].cpt
    assert copied.charge_cents == parent.lines[0].charge_cents
    assert copied.paid_cents == 0
    assert copied.line_control_number == line_control_number(void.control_number, 1)
