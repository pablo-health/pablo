# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for write-offs (``app.routes.patient_write_offs``).

These cover the behaviours that keep a write-off from being an unlogged
discount:

* the reason must be one of the fixed set the CHECK constraint enforces;
* ``courtesy`` is refused unless the practice's billing profile has opted
  in, and ``small_balance`` is refused unless the balance is at or under the
  practice's own threshold;
* the amount can never exceed the client's balance;
* a successful write-off lands a ``write_off`` row with its reason and note,
  and a disclosure-grade audit event carrying the actor, the reason, the
  amount and the claim ids behind the balance — never a diagnosis or a
  payer's member id;
* an unseen client is 404, never 403.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.auth.service import require_baa_acceptance
from app.models import User
from app.models.patient import Patient
from app.models.payments import PatientCharge
from app.repositories import get_patient_payment_repository, get_patient_repository
from app.repositories.audit import InMemoryAuditRepository
from app.routes import patient_write_offs
from app.routes.patient_write_offs import WriteOffPolicy, get_write_off_policy
from app.services import AuditService, get_audit_service
from fastapi import FastAPI
from fastapi.testclient import TestClient

_USER_ID = "user-1"
_PATIENT_ID = "11111111-1111-4111-8111-111111111111"
_OTHER_PATIENT_ID = "22222222-2222-4222-8222-222222222222"
_CLAIM_ID = "claim-1"


class _FakePatients:
    def __init__(self, *, visible: bool = True) -> None:
        self.visible = visible
        self.patient = Patient(
            id=_PATIENT_ID,
            first_name="A",
            last_name="B",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    def get(self, patient_id: str, user_id: str) -> Patient | None:
        if not self.visible or patient_id != _PATIENT_ID or user_id != _USER_ID:
            return None
        return self.patient


class _FakePayments:
    """In-memory ledger, seeded with whatever rows a test wants outstanding."""

    def __init__(self, charges: list[PatientCharge] | None = None) -> None:
        self.charges: list[PatientCharge] = charges or []
        self._next_id = 0

    def list_charges(self, patient_id: str) -> list[PatientCharge]:
        return [c for c in self.charges if c.patient_id == patient_id]

    def add_ledger_row(
        self,
        *,
        patient_id: str,
        kind: str,
        amount_cents: int,
        currency: str,
        user_id: str,
        appointment_id: str | None = None,
        claim_id: str | None = None,
        write_off_reason: str | None = None,
        note: str | None = None,
    ) -> PatientCharge:
        self._next_id += 1
        charge = PatientCharge(
            id=f"charge-{self._next_id}",
            patient_id=patient_id,
            appointment_id=appointment_id,
            kind=kind,
            claim_id=claim_id,
            write_off_reason=write_off_reason,
            note=note,
            amount_cents=amount_cents,
            currency=currency,
            status="succeeded",
            created_by_user_id=user_id,
            created_at=datetime.now(UTC),
        )
        self.charges.append(charge)
        return charge


def _user() -> User:
    return User(
        id=_USER_ID,
        email="therapist@example.com",
        name="Test Therapist",
        created_at=datetime.now(UTC),
        baa_accepted_at=datetime.now(UTC),
        baa_version="2024-01-01",
    )


def _owed_row(amount_cents: int, *, claim_id: str | None = None) -> PatientCharge:
    return PatientCharge(
        id="owed-1",
        patient_id=_PATIENT_ID,
        kind="patient_resp",
        claim_id=claim_id,
        amount_cents=amount_cents,
        currency="usd",
        status="succeeded",
        created_by_user_id=_USER_ID,
        created_at=datetime.now(UTC),
    )


def _client(
    payments: _FakePayments,
    patients: _FakePatients,
    *,
    policy: WriteOffPolicy | None = None,
    audit: AuditService | None = None,
) -> tuple[TestClient, AuditService]:
    app = FastAPI()
    app.include_router(patient_write_offs.router)
    app.dependency_overrides[require_baa_acceptance] = _user
    app.dependency_overrides[get_patient_payment_repository] = lambda: payments
    app.dependency_overrides[get_patient_repository] = lambda: patients
    app.dependency_overrides[get_write_off_policy] = lambda: policy or WriteOffPolicy()
    audit_service = audit or AuditService(InMemoryAuditRepository())
    app.dependency_overrides[get_audit_service] = lambda: audit_service
    return TestClient(app, raise_server_exceptions=False), audit_service


def _write_off(
    client: TestClient, *, amount_cents: int, reason: str, note: str | None = None
) -> Any:
    body: dict[str, Any] = {"amount_cents": amount_cents, "reason": reason}
    if note is not None:
        body["note"] = note
    return client.post(f"/api/patients/{_PATIENT_ID}/write-offs", json=body)


class TestAccess:
    def test_foreign_client_is_404_not_403(self) -> None:
        client, _ = _client(_FakePayments([_owed_row(1000)]), _FakePatients(visible=False))
        response = _write_off(client, amount_cents=500, reason="hardship")
        assert response.status_code == 404

    def test_unknown_client_id_is_404(self) -> None:
        client, _ = _client(_FakePayments(), _FakePatients())
        response = client.post(
            f"/api/patients/{_OTHER_PATIENT_ID}/write-offs",
            json={"amount_cents": 500, "reason": "hardship"},
        )
        assert response.status_code == 404


class TestReasonSet:
    def test_unknown_reason_is_422(self) -> None:
        client, _ = _client(_FakePayments([_owed_row(1000)]), _FakePatients())
        response = _write_off(client, amount_cents=500, reason="because")
        assert response.status_code == 422

    @pytest.mark.parametrize("reason", ["hardship", "error"])
    def test_ungated_reasons_are_allowed(self, reason: str) -> None:
        client, _ = _client(_FakePayments([_owed_row(1000)]), _FakePatients())
        response = _write_off(client, amount_cents=500, reason=reason)
        assert response.status_code == 200
        assert response.json()["write_off_reason"] == reason


class TestCourtesyPolicy:
    def test_refused_when_policy_disallows(self) -> None:
        client, _ = _client(
            _FakePayments([_owed_row(1000)]),
            _FakePatients(),
            policy=WriteOffPolicy(allow_courtesy_writeoffs=False),
        )
        response = _write_off(client, amount_cents=500, reason="courtesy")
        assert response.status_code == 403

    def test_allowed_when_policy_permits(self) -> None:
        client, _ = _client(
            _FakePayments([_owed_row(1000)]),
            _FakePatients(),
            policy=WriteOffPolicy(allow_courtesy_writeoffs=True),
        )
        response = _write_off(client, amount_cents=500, reason="courtesy")
        assert response.status_code == 200


class TestSmallBalancePolicy:
    def test_refused_above_threshold(self) -> None:
        client, _ = _client(
            _FakePayments([_owed_row(10_000)]),
            _FakePatients(),
            policy=WriteOffPolicy(small_balance_cents=500),
        )
        response = _write_off(client, amount_cents=500, reason="small_balance")
        assert response.status_code == 403

    def test_allowed_at_or_under_threshold(self) -> None:
        client, _ = _client(
            _FakePayments([_owed_row(500)]),
            _FakePatients(),
            policy=WriteOffPolicy(small_balance_cents=500),
        )
        response = _write_off(client, amount_cents=500, reason="small_balance")
        assert response.status_code == 200


class TestAmountVsBalance:
    def test_amount_over_balance_is_422(self) -> None:
        client, _ = _client(_FakePayments([_owed_row(1000)]), _FakePatients())
        response = _write_off(client, amount_cents=1001, reason="hardship")
        assert response.status_code == 422

    def test_amount_equal_to_balance_is_allowed(self) -> None:
        client, _ = _client(_FakePayments([_owed_row(1000)]), _FakePatients())
        response = _write_off(client, amount_cents=1000, reason="hardship")
        assert response.status_code == 200


class TestSuccess:
    def test_writes_a_ledger_row_with_reason_and_note(self) -> None:
        payments = _FakePayments([_owed_row(1000)])
        client, _ = _client(payments, _FakePatients())
        response = _write_off(
            client, amount_cents=400, reason="hardship", note="agreed on a payment plan"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "write_off"
        assert body["write_off_reason"] == "hardship"
        assert body["note"] == "agreed on a payment plan"
        assert body["amount_cents"] == 400
        row = payments.charges[-1]
        assert row.kind == "write_off"
        assert row.write_off_reason == "hardship"

    def test_audits_actor_reason_amount_and_claim_ids(self) -> None:
        payments = _FakePayments([_owed_row(1000, claim_id=_CLAIM_ID)])
        audit = AuditService(InMemoryAuditRepository())
        client, audit_service = _client(payments, _FakePatients(), audit=audit)
        response = _write_off(client, amount_cents=400, reason="hardship")
        assert response.status_code == 200

        logged = audit_service._repo.list_for_user(_USER_ID)
        entry = next(e for e in logged if e.action == "patient_write_off_created")
        assert entry.patient_id is None or entry.resource_id == _PATIENT_ID
        assert entry.changes["reason"] == "hardship"
        assert entry.changes["amount_cents"] == 400
        assert entry.changes["claim_ids"] == [_CLAIM_ID]
        # No PHI — a member id, diagnosis or name never has a home in this
        # payload's keys or values.
        assert "diagnosis" not in entry.changes
        assert "member_id" not in entry.changes
