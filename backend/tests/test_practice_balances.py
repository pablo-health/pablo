# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the balances view (``app.routes.practice_balances``).

What these pin down:

* a client is on the list only when their ledger totals to something, and the
  figure is the one :func:`app.payments.balance.patient_balance` computes —
  this screen and the chart header read the same arithmetic;
* a client the caller cannot see is left off rather than refused, matching
  the 404 the per-client routes give;
* the order is oldest outstanding first, dated from the visit that still has
  a balance rather than from the client's first ever row;
* a credit is listed, because a refund the practice owes is exactly what a
  debts-only screen would never surface;
* the read is audited once, naming the clients it disclosed.

The repositories are in-process fakes: nothing here needs a database, and
the row policy that decides visibility in production is exercised where it
lives.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.auth.service import require_baa_acceptance
from app.models import User
from app.models.coverage import PatientCoverage, Payer
from app.models.patient import Patient
from app.models.payments import PatientCharge
from app.repositories import (
    get_patient_coverage_repository,
    get_patient_payment_repository,
    get_patient_repository,
    get_payer_repository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.routes import practice_balances
from app.services import AuditService, get_audit_service
from fastapi import FastAPI
from fastapi.testclient import TestClient

_USER_ID = "user-1"
_ALICE = "11111111-1111-4111-8111-111111111111"
_BEN = "22222222-2222-4222-8222-222222222222"
_HIDDEN = "33333333-3333-4333-8333-333333333333"
_VISIT = "44444444-4444-4444-8444-444444444444"
_OTHER_VISIT = "55555555-5555-4555-8555-555555555555"

_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


class _FakePatients:
    """The clients this clinician can see. Everyone else is simply absent."""

    def __init__(self, names: dict[str, str]) -> None:
        self.names = names

    def get_multiple(self, patient_ids: list[str], user_id: str) -> dict[str, Patient]:
        if user_id != _USER_ID:
            return {}
        return {
            patient_id: Patient(
                id=patient_id,
                first_name=self.names[patient_id],
                last_name="Example",
                created_at=_NOW,
                updated_at=_NOW,
            )
            for patient_id in patient_ids
            if patient_id in self.names
        }


class _FakePayments:
    def __init__(self, charges: list[PatientCharge]) -> None:
        self.charges = charges

    def list_all_charges(self) -> list[PatientCharge]:
        return sorted(self.charges, key=lambda c: (c.created_at, c.id))


def _row(patient_id: str, **overrides: Any) -> PatientCharge:
    row: dict[str, Any] = {
        "id": f"charge-{patient_id[:4]}",
        "patient_id": patient_id,
        "appointment_id": _VISIT,
        "kind": "session",
        "amount_cents": 10_000,
        "currency": "usd",
        "status": "pending",
        "created_by_user_id": _USER_ID,
        "created_at": _NOW,
    }
    row.update(overrides)
    return PatientCharge(**row)


def _user() -> User:
    return User(
        id=_USER_ID,
        email="therapist@example.com",
        name="Test Therapist",
        created_at=_NOW,
        baa_accepted_at=_NOW,
        baa_version="2024-01-01",
    )


class _FakeCoverage:
    """Active coverage per client, keyed the way the real repository keys it."""

    def __init__(self, plans: dict[str, PatientCoverage] | None = None) -> None:
        self.plans = plans or {}

    def get_active_for_patients(self, patient_ids: list[str]) -> dict[str, PatientCoverage]:
        return {pid: self.plans[pid] for pid in patient_ids if pid in self.plans}


class _FakePayers:
    """The practice's payers by row id. Empty is the private-pay practice."""

    def __init__(self, payers: dict[str, Payer] | None = None) -> None:
        self.payers = payers or {}

    def get(self, payer_row_id: str) -> Payer | None:
        return self.payers.get(payer_row_id)


def _client(
    charges: list[PatientCharge],
    names: dict[str, str] | None = None,
    *,
    audit: AuditService | None = None,
    coverage: _FakeCoverage | None = None,
    payers: _FakePayers | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(practice_balances.router)
    app.dependency_overrides[require_baa_acceptance] = _user
    app.dependency_overrides[get_patient_payment_repository] = lambda: _FakePayments(charges)
    app.dependency_overrides[get_patient_repository] = lambda: _FakePatients(
        names if names is not None else {_ALICE: "Alice", _BEN: "Ben"}
    )
    app.dependency_overrides[get_patient_coverage_repository] = lambda: coverage or _FakeCoverage()
    app.dependency_overrides[get_payer_repository] = lambda: payers or _FakePayers()
    audit_service = audit or AuditService(InMemoryAuditRepository())
    app.dependency_overrides[get_audit_service] = lambda: audit_service
    return TestClient(app, raise_server_exceptions=False)


def _items(client: TestClient) -> list[dict[str, Any]]:
    response = client.get("/api/billing/balances")
    assert response.status_code == 200
    return response.json()["items"]


def test_an_empty_ledger_lists_nobody() -> None:
    assert _items(_client([])) == []


def test_a_client_who_owes_is_listed_with_their_balance() -> None:
    items = _items(_client([_row(_ALICE, amount_cents=6_200)]))

    assert len(items) == 1
    assert items[0]["patient_id"] == _ALICE
    assert items[0]["patient_name"] == "Alice Example"
    assert items[0]["balance_cents"] == 6_200
    assert items[0]["currency"] == "usd"


def test_a_settled_client_is_not_listed() -> None:
    """A paid session nets to zero, and zero is not a balance."""
    items = _items(_client([_row(_ALICE, status="succeeded")]))

    assert items == []


def test_a_credit_is_listed_rather_than_filtered_out() -> None:
    """It is a refund the practice owes; a debts-only screen would hide it."""
    items = _items(_client([_row(_ALICE, kind="credit", status="succeeded", amount_cents=2_500)]))

    assert [item["balance_cents"] for item in items] == [-2_500]


def test_a_client_the_caller_cannot_see_is_left_off() -> None:
    """Left off rather than refused, matching the 404 the chart routes give."""
    charges = [_row(_ALICE), _row(_HIDDEN, id="charge-hidden")]

    items = _items(_client(charges, {_ALICE: "Alice"}))

    assert [item["patient_id"] for item in items] == [_ALICE]


def test_oldest_outstanding_first() -> None:
    charges = [
        _row(_ALICE, id="a", created_at=_NOW - timedelta(days=2)),
        _row(_BEN, id="b", created_at=_NOW - timedelta(days=30)),
    ]

    items = _items(_client(charges))

    assert [item["patient_id"] for item in items] == [_BEN, _ALICE]


def test_the_age_is_the_unpaid_visits_not_the_clients_first_ever_row() -> None:
    """A client seen for years whose only unpaid visit was last week sorts as
    a week old. Otherwise the list is ordered by tenure, not by debt."""
    long_standing = [
        _row(_ALICE, id="a-old", created_at=_NOW - timedelta(days=400), status="succeeded"),
        _row(
            _ALICE,
            id="a-new",
            appointment_id=_OTHER_VISIT,
            created_at=_NOW - timedelta(days=7),
        ),
    ]
    charges = [*long_standing, _row(_BEN, id="b", created_at=_NOW - timedelta(days=30))]

    items = _items(_client(charges))

    assert [item["patient_id"] for item in items] == [_BEN, _ALICE]


def test_the_read_is_audited_once_naming_the_clients_it_disclosed() -> None:
    repository = InMemoryAuditRepository()
    client = _client([_row(_ALICE), _row(_BEN, id="b")], audit=AuditService(repository))

    client.get("/api/billing/balances")

    logged = repository.list_for_user(_USER_ID)
    assert [entry.action for entry in logged] == ["balances_listed"]
    assert sorted(logged[0].changes["patient_ids"]) == sorted([_ALICE, _BEN])
    assert logged[0].changes["count"] == 2


@pytest.mark.parametrize("field", ["patient_name", "balance_cents", "outstanding_since"])
def test_every_listed_client_carries_the_fields_the_view_renders(field: str) -> None:
    items = _items(_client([_row(_ALICE)]))

    assert items[0][field] is not None


def _payer(*, enroll_remittance: bool) -> Payer:
    return Payer(
        id="payer-1",
        name="Aetna",
        payer_id="60054",
        enroll_remittance=enroll_remittance,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _plan(patient_id: str) -> PatientCoverage:
    return PatientCoverage(
        id=f"cov-{patient_id}",
        patient_id=patient_id,
        payer_id="payer-1",
        member_id="123456789",
        created_at=_NOW,
        updated_at=_NOW,
    )


def test_a_private_pay_balance_is_the_whole_of_what_is_owed() -> None:
    """Nobody else is settling it, so there is nothing missing from it."""
    client = _client([_row(_ALICE, amount_cents=5_000, kind="session")])

    [alice] = _items(client)

    assert alice["outcome_known"] is True


def test_a_balance_under_a_payer_billing_elsewhere_is_only_a_floor() -> None:
    """Chasing a floor as though it were the debt is how a client is asked twice.

    The figure is not wrong — it is short by whatever the 835 would have
    added, and that 835 goes to her billing service.
    """
    client = _client(
        [_row(_ALICE, amount_cents=5_000, kind="session")],
        coverage=_FakeCoverage({_ALICE: _plan(_ALICE)}),
        payers=_FakePayers({"payer-1": _payer(enroll_remittance=False)}),
    )

    [alice] = _items(client)

    assert alice["outcome_known"] is False
    assert alice["balance_cents"] == 5_000


def test_a_payer_whose_remittances_reach_us_is_known() -> None:
    client = _client(
        [_row(_ALICE, amount_cents=5_000, kind="session")],
        coverage=_FakeCoverage({_ALICE: _plan(_ALICE)}),
        payers=_FakePayers({"payer-1": _payer(enroll_remittance=True)}),
    )

    [alice] = _items(client)

    assert alice["outcome_known"] is True
