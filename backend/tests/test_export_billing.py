# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The billing record in an export: the ledger, coverage and claims read
through their repositories and laid out in ``patient.json``, the statement
and the superbill beside the chart, and nothing about the card on file
anywhere in the bytes.

The Postgres harness test builds the same record from real rows; this one
pins the mapping and the documents over the in-memory repositories.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from app.models import Patient
from app.models.claims import ClaimReceipt
from app.models.coverage import PatientCoverage
from app.models.payments import CardOnFile, PatientCharge
from app.payments.statement import PracticeBlock
from app.repositories.claim_receipts import InMemoryClaimReceiptRepository
from app.repositories.claims import InMemoryClaimRepository
from app.repositories.clinician_profile import ClinicianProfile
from app.repositories.coverage import InMemoryPatientCoverageRepository, InMemoryPayerRepository
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services import ExportService
from app.services.coverage_intake import new_payer
from app.services.export_billing import BillingRecordSource
from jsonschema import Draft202012Validator

from tests.claims_fixtures import APPOINTMENT_ID, CLAIM_ID, PATIENT_ID, USER_ID, claim, line

_T0 = datetime(2026, 9, 1, 15, 0, tzinfo=UTC)
_EXPORTED_AT = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
_TAX_ID = "844459714"

#: The card on file, which the export must never read or carry.
_CARD = CardOnFile(
    id="pm-row-1",
    patient_id=PATIENT_ID,
    stripe_customer_id="cus_CARDSENTINEL1",
    stripe_payment_method_id="pm_CARDSENTINEL2",
    card_brand="visa",
    card_last4="4242",
    card_exp_month=12,
    card_exp_year=2030,
)

_PRACTICE = PracticeBlock(
    name="Harbor Light Counseling",
    address_line1="1 Test St",
    address_line2=None,
    city="Atlanta",
    state="GA",
    postal_code="30301",
    phone="4045550100",
)

_PROFILE = ClinicianProfile(
    user_id=USER_ID,
    practice_id="practice-1",
    npi_number="1999999984",
    license_number="LCSW-4321",
    license_state="GA",
)


def _charge(**overrides: Any) -> PatientCharge:
    fields: dict[str, Any] = {
        "id": "charge-session",
        "patient_id": PATIENT_ID,
        "appointment_id": APPOINTMENT_ID,
        "kind": "session",
        "amount_cents": 15000,
        "currency": "usd",
        "status": "succeeded",
        "method": "card",
        "stripe_payment_intent_id": "pi_CARDSENTINEL3",
        "created_by_user_id": USER_ID,
        "created_at": _T0,
    }
    fields.update(overrides)
    return PatientCharge(**fields)


@pytest.fixture
def payments() -> Mock:
    repo = Mock()
    repo.get_card_on_file.return_value = _CARD
    # Newest first, as the repository lists them.
    repo.list_charges.return_value = [
        _charge(
            id="charge-payment",
            appointment_id=None,
            kind="payment",
            amount_cents=4000,
            method="check",
            payment_reference="1042",
            stripe_payment_intent_id=None,
            created_at=_T0.replace(day=3),
        ),
        _charge(),
    ]
    return repo


def _source(payments: Mock, *, tax_id: str | None = _TAX_ID) -> BillingRecordSource:
    payers = InMemoryPayerRepository()
    payer = payers.create(new_payer(name="Stedi Test Payer", payer_id="STEDI"))
    old_payer = payers.create(new_payer(name="Former Plan Co", payer_id="OLDCO"))

    coverage = InMemoryPatientCoverageRepository()
    for plan_id, plan_payer, active, day in (
        ("cov-old", old_payer, False, 1),
        ("cov-live", payer, True, 2),
    ):
        coverage.create(
            PatientCoverage(
                id=plan_id,
                patient_id=PATIENT_ID,
                payer_id=plan_payer.id,
                member_id="MEM123",
                group_number="G1",
                active=active,
                created_at=_T0.replace(day=day),
                updated_at=_T0.replace(day=day),
            )
        )

    claims = InMemoryClaimRepository()
    claims.create(
        claim(
            payer_id=payer.id,
            coverage_id="cov-live",
            diagnosis_codes=["F41.1", "F33.1"],
            lines=[
                line(
                    dx_pointers=[2, 1],
                    adjustments=[{"group_code": "CO", "reason_code": "45", "amount_cents": 2000}],
                )
            ],
        )
    )
    receipts = InMemoryClaimReceiptRepository()
    receipts.add(
        ClaimReceipt(
            id="receipt-1",
            claim_id=CLAIM_ID,
            kind="submitted",
            from_state="validated",
            to_state="submitted",
            occurred_at=_T0.replace(day=2),
        )
    )

    appointments = InMemoryAppointmentRepository()
    appointments.grant_access(PATIENT_ID, USER_ID)

    return BillingRecordSource(
        payments=payments,
        coverage=coverage,
        payers=payers,
        claims=claims,
        receipts=receipts,
        appointments=appointments,
        practice=lambda: _PRACTICE,
        tax_id=lambda: tax_id,
        license_for=lambda user_id: _PROFILE if user_id == USER_ID else None,
        timezone=lambda _user_id: ZoneInfo("America/New_York"),
    )


def _patient() -> Patient:
    return Patient(
        id=PATIENT_ID, first_name="Robin", last_name="Ash", created_at=_T0, updated_at=_T0
    )


def test_each_list_is_read_and_mapped(payments: Mock) -> None:
    record = _source(payments).read(_patient(), USER_ID, _EXPORTED_AT)

    assert [c.id for c in record.charges] == ["charge-session", "charge-payment"], "oldest first"
    assert record.charges[1].model_dump() == {
        "id": "charge-payment",
        "kind": "payment",
        "appointment_id": None,
        "claim_id": None,
        "description": None,
        "amount_cents": 4000,
        "currency": "usd",
        "status": "succeeded",
        "method": "check",
        "payment_reference": "1042",
        "write_off_reason": None,
        "settled_by_charge_id": None,
        "recorded_at": _T0.replace(day=3),
        "updated_at": None,
    }

    assert [(p.id, p.active, p.payer_name) for p in record.coverage] == [
        ("cov-live", True, "Stedi Test Payer"),
        ("cov-old", False, "Former Plan Co"),
    ], "every plan, newest first"
    assert record.coverage[0].payer_id == "STEDI"
    assert record.coverage[0].subscriber is None, "the client holds the plan"

    [exported] = record.claims
    assert exported.control_number == "88659891"
    assert exported.payer_name == "Stedi Test Payer"
    assert exported.member_id == "123456789"
    [service] = exported.lines
    assert service.diagnosis_codes == ["F33.1", "F41.1"], "pointers resolved, in pointer order"
    assert [a.model_dump() for a in service.adjustments] == [
        {"group_code": "CO", "reason_code": "45", "amount_cents": 2000}
    ]
    assert [(e.kind, e.from_state, e.to_state) for e in exported.events] == [
        ("submitted", "validated", "submitted")
    ]

    assert (record.statement or b"").startswith(b"%PDF")
    assert (record.superbill or b"").startswith(b"%PDF")
    assert record.balance_cents == -4000, "nothing owed on the visit, a cheque on account"
    payments.get_card_on_file.assert_not_called()


def test_the_superbill_is_left_out_where_the_route_would_refuse(payments: Mock) -> None:
    record = _source(payments, tax_id=None).read(_patient(), USER_ID, _EXPORTED_AT)

    assert record.superbill is None
    assert record.statement is not None, "a statement never refuses"


def _archive(payments: Mock, **kwargs: Any) -> tuple[dict[str, Any], dict[str, bytes]]:
    patients, sessions, notes = Mock(), Mock(), Mock()
    patients.get.return_value = _patient()
    sessions.list_by_patient.return_value = []
    notes.list_by_patient.return_value = []
    service = ExportService(
        patients, sessions, notes, billing_record=_source(payments, **kwargs).read
    )
    result = service.get_patient_export_data(PATIENT_ID, USER_ID, "zip")
    with zipfile.ZipFile(io.BytesIO(result["content"])) as archive:
        return result, {name: archive.read(name) for name in archive.namelist()}


def test_the_archive_carries_the_lists_and_the_documents(payments: Mock) -> None:
    result, files = _archive(payments)

    document = json.loads(files["patient.json"])
    Draft202012Validator(json.loads(files["schema.json"])).validate(document)
    assert document["schema_version"] == "1.4"
    assert {key: len(document[key]) for key in ("charges", "coverage", "claims")} == {
        "charges": 2,
        "coverage": 2,
        "claims": 1,
    }
    assert files["billing/statement.pdf"].startswith(b"%PDF")
    assert files["billing/superbill.pdf"].startswith(b"%PDF")
    kinds = {f["path"]: f["kind"] for f in json.loads(files["manifest.json"])["files"]}
    assert kinds["billing/statement.pdf"] == "statement"
    assert kinds["billing/superbill.pdf"] == "superbill"

    # What the route records, by identifier.
    assert result["charge_ids"] == ["charge-session", "charge-payment"]
    assert result["coverage_ids"] == ["cov-live", "cov-old"]
    assert result["claim_ids"] == [CLAIM_ID]
    assert result["statement"] is True
    assert result["superbill"] is True
    assert result["balance_cents"] == -4000


def test_nothing_about_the_card_is_anywhere_in_the_archive(payments: Mock) -> None:
    _, files = _archive(payments)

    for name, data in files.items():
        text = data.decode("latin-1")
        assert "CARDSENTINEL" not in text, name
        if not name.endswith(".pdf"):
            # A PDF's compressed streams and offsets can spell four digits by chance.
            for sentinel in ("4242", "visa", "cus_", "pm_", "pi_"):
                assert sentinel not in text, (name, sentinel)
    # Control: the ledger row the card paid is in the same copy, as a category.
    assert "charge-session" in files["patient.json"].decode()
    assert json.loads(files["patient.json"])["charges"][0]["method"] == "card"


def test_a_bare_service_has_no_billing_and_no_billing_files() -> None:
    patients, sessions, notes = Mock(), Mock(), Mock()
    patients.get.return_value = _patient()
    sessions.list_by_patient.return_value = []
    notes.list_by_patient.return_value = []
    result = ExportService(patients, sessions, notes).get_patient_export_data(
        PATIENT_ID, USER_ID, "zip"
    )
    with zipfile.ZipFile(io.BytesIO(result["content"])) as archive:
        names = archive.namelist()
        document = json.loads(archive.read("patient.json"))

    assert not [name for name in names if name.startswith("billing/")]
    assert (document["charges"], document["coverage"], document["claims"]) == ([], [], [])
    assert result["statement"] is False
    assert result["superbill"] is False


def test_the_pdf_on_its_own_carries_the_billing_section(payments: Mock) -> None:
    patients, sessions, notes = Mock(), Mock(), Mock()
    patients.get.return_value = _patient()
    sessions.list_by_patient.return_value = []
    notes.list_by_patient.return_value = []
    service = ExportService(patients, sessions, notes, billing_record=_source(payments).read)

    result = service.get_patient_export_data(PATIENT_ID, USER_ID, "pdf")

    assert result["content"].startswith(b"%PDF")
    assert result["claim_ids"] == [CLAIM_ID]
    assert result["statement"] is True
