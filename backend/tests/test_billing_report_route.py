# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the financial report route (``GET /api/billing/report``).

The arithmetic itself is covered in ``test_billing_reporting.py``; this pins
down the route's own job — wiring the window into each repository read the
same way ``app.routes.billing_export`` does, returning a well-formed empty
report rather than a division by zero, and auditing the read with ids only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.api_errors import register_exception_handlers
from app.auth.service import require_baa_acceptance
from app.models import User
from app.models.payments import PatientCharge
from app.models.user import UserPreferences
from app.repositories import (
    get_claim_receipt_repository,
    get_claim_repository,
    get_patient_payment_repository,
    get_user_repository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.claim_receipts import InMemoryClaimReceiptRepository
from app.repositories.claims import InMemoryClaimRepository
from app.routes import billing_report
from app.services import AuditService, get_audit_service
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.claims_fixtures import PATIENT_ID, USER_ID, claim, line, subscriber_snapshot

if TYPE_CHECKING:
    from collections.abc import Iterator

_NOW = datetime(2026, 9, 15, 17, 0, tzinfo=UTC)
_TZ_NAME = "America/New_York"
_APPOINTMENT_ID = "44444444-4444-4444-8444-444444444444"
_WINDOW = {"from": "2026-09-01", "to": "2026-09-30"}


def _user() -> User:
    return User(
        id=USER_ID,
        email="therapist@example.com",
        name="Jane Smith",
        created_at=_NOW,
        baa_accepted_at=_NOW,
        baa_version="2024-01-01",
    )


class _FakeUsers:
    def get_preferences(self, user_id: str) -> UserPreferences:
        return UserPreferences(timezone=_TZ_NAME)


class _FakePayments:
    """A charge ledger held in a list, read the way the report reads it."""

    def __init__(self, charges: list[PatientCharge] | None = None) -> None:
        self.charges = list(charges or [])

    def list_all_charges(self) -> list[PatientCharge]:
        return sorted(self.charges, key=lambda c: (c.created_at, c.id))

    def iter_ledger_for_period(self, *, start: datetime, end: datetime) -> Iterator[PatientCharge]:
        selected = sorted(
            (c for c in self.charges if start <= c.created_at < end),
            key=lambda c: (c.created_at, c.id),
        )
        yield from selected


def _charge(**overrides: Any) -> PatientCharge:
    fields: dict[str, Any] = {
        "id": "charge-1",
        "patient_id": PATIENT_ID,
        "appointment_id": _APPOINTMENT_ID,
        "kind": "session",
        "amount_cents": 15_000,
        "currency": "usd",
        "status": "pending",
        "created_by_user_id": USER_ID,
        "created_at": datetime(2026, 9, 15, 17, 0, tzinfo=UTC),
    }
    fields.update(overrides)
    return PatientCharge(**fields)


def _harness(
    *,
    charges: list[PatientCharge] | None = None,
    claims: list[Any] | None = None,
    audit: AuditService | None = None,
) -> TestClient:
    claim_repo = InMemoryClaimRepository()
    for one_claim in claims or []:
        claim_repo.create(one_claim)
    receipt_repo = InMemoryClaimReceiptRepository()
    payments = _FakePayments(charges)
    audit_service = audit or AuditService(InMemoryAuditRepository())

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(billing_report.router)
    app.dependency_overrides[require_baa_acceptance] = _user
    app.dependency_overrides[get_claim_repository] = lambda: claim_repo
    app.dependency_overrides[get_claim_receipt_repository] = lambda: receipt_repo
    app.dependency_overrides[get_patient_payment_repository] = lambda: payments
    app.dependency_overrides[get_user_repository] = _FakeUsers
    app.dependency_overrides[get_audit_service] = lambda: audit_service
    return TestClient(app, raise_server_exceptions=False)


def test_an_empty_practice_renders_every_section_with_no_division_by_zero() -> None:
    client = _harness()

    response = client.get("/api/billing/report", params=_WINDOW)

    assert response.status_code == 200
    body = response.json()
    assert [b["cents"] for b in body["aging"]["buckets"]] == [0, 0, 0, 0]
    assert body["payer_mix"]["entries"] == []
    assert body["collections_rate"] == {
        "billed_cents": 0,
        "collected_cents": 0,
        "contractual_adjustment_cents": 0,
        "write_off_cents": 0,
    }
    assert body["claim_payment_lag"]["by_payer"] == []


def test_the_window_ending_before_it_starts_is_refused() -> None:
    client = _harness()

    response = client.get("/api/billing/report", params={"from": "2026-09-30", "to": "2026-09-01"})

    assert response.status_code == 422


def test_a_claim_in_the_window_appears_in_the_payer_mix() -> None:
    one_claim = claim(
        state="submitted",
        payer_id="payer-aetna",
        total_charge_cents=15_000,
        total_paid_cents=10_000,
        subscriber_snapshot=subscriber_snapshot(payer_id="AETNA", payer_name="Aetna"),
        lines=[line(service_date=datetime(2026, 9, 10, tzinfo=UTC).date())],
    )
    client = _harness(claims=[one_claim])

    response = client.get("/api/billing/report", params=_WINDOW)

    entries = response.json()["payer_mix"]["entries"]
    assert entries == [
        {
            "payer_id": "payer-aetna",
            "payer_name": "Aetna",
            "billed_cents": 15_000,
            "collected_cents": 10_000,
        }
    ]


def test_a_charge_outside_the_window_does_not_move_the_collections_rate() -> None:
    outside = _charge(created_at=datetime(2026, 8, 1, tzinfo=UTC), status="succeeded")
    client = _harness(charges=[outside])

    response = client.get("/api/billing/report", params=_WINDOW)

    assert response.json()["collections_rate"]["billed_cents"] == 0


def test_an_outstanding_charge_ages_into_the_report_regardless_of_the_window() -> None:
    """Aging is a live snapshot as of the window's end, not filtered by
    when the row was created — a balance from August is still owed in a
    September report."""
    old = _charge(created_at=datetime(2026, 8, 1, tzinfo=UTC), status="pending")
    client = _harness(charges=[old])

    response = client.get("/api/billing/report", params=_WINDOW)

    total_owed = sum(b["cents"] for b in response.json()["aging"]["buckets"])
    assert total_owed == 15_000


def test_the_read_is_audited_once_with_ids_only() -> None:
    repository = InMemoryAuditRepository()
    one_claim = claim(
        state="submitted",
        payer_id="payer-aetna",
        subscriber_snapshot=subscriber_snapshot(payer_id="AETNA", payer_name="Aetna"),
        lines=[line(service_date=datetime(2026, 9, 10, tzinfo=UTC).date())],
    )
    client = _harness(claims=[one_claim], audit=AuditService(repository))

    client.get("/api/billing/report", params=_WINDOW)

    logged = repository.list_for_user(USER_ID)
    assert [entry.action for entry in logged] == ["billing_report_viewed"]
    changes = logged[0].changes
    assert changes["from"] == "2026-09-01"
    assert changes["to"] == "2026-09-30"
    assert changes["claim_ids"] == [one_claim.id]
    assert changes["payer_ids"] == ["payer-aetna"]
    # Nothing about a named client or a payer's member id ever reaches the row.
    assert "patient_name" not in changes
    assert "member_id" not in changes
