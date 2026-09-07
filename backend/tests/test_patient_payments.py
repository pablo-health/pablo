# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for self-pay card payments (``app.routes.patient_payments``).

These cover the behaviours that actually protect money and privacy:

* setup persists the processor **customer** id, and completing setup persists
  the **payment-method** id plus the display triple — and nothing card-shaped
  beyond it, because no such column exists.
* charging writes a ``pending`` ledger row FIRST and then transitions it, on
  success and on a decline, where the row must land ``failed`` carrying the
  decline code and stay that way.
* the PaymentIntent id reaches the ledger row BEFORE the intent is confirmed,
  so money can never move against an intent that was not written down.
* an unknown or foreign client is 404, never 403 — no existence leak.
* a deployment with no card processing configured is 503, not a half-working
  charge.
* the amount comes from the client's own rate, falling back to the appointment
  type's default fee, and refuses rather than guessing when neither exists.
* the PaymentIntent carries opaque ids only — a ledger id, a clinician id and a
  practice id — and never a client identifier or clinical content.

Hermetic on two levels: the repositories are in-process fakes, and the
processor calls go through a fake ``httpx`` transport so the real decline
parsing runs against a real 402 response.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from app.auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from app.db.models import DEFAULT_CHARGE_CURRENCY
from app.models import User
from app.models.coverage import PatientCoverage
from app.models.patient import Patient
from app.models.payments import CardOnFile, PatientCharge
from app.payments import stripe_api
from app.payments.provider import PaymentCredentials, register_payment_credential_provider
from app.repositories import (
    get_appointment_repository,
    get_appointment_type_repository,
    get_claim_repository,
    get_patient_coverage_repository,
    get_patient_payment_repository,
    get_patient_repository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.routes import patient_payments
from app.scheduling_engine.models.appointment import Appointment
from app.scheduling_engine.models.appointment_type import AppointmentType
from app.services import AuditService, get_audit_service
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.claims_fixtures import claim as claim_fixture
from tests.claims_fixtures import line as line_fixture

if TYPE_CHECKING:
    from app.models.claims import Claim

_USER_ID = "user-1"
_PRACTICE_ID = "practice-1"
_PATIENT_ID = "11111111-1111-4111-8111-111111111111"
_OTHER_PATIENT_ID = "22222222-2222-4222-8222-222222222222"
_APPOINTMENT_ID = "33333333-3333-4333-8333-333333333333"
_TYPE_ID = "44444444-4444-4444-8444-444444444444"
_PI_ID = "pi_created"
# Deliberately not shaped like real credentials: the fake transport never
# parses any of these, and a fixture imitating a credential would be
# indistinguishable from a leaked one to a secret scanner.
_SECRET_KEY = "secret-key-for-tests"
_PUBLISHABLE_KEY = "publishable-key-for-tests"
_CLIENT_SECRET = "setup-intent-client-secret-for-tests"
_SECOND_CLIENT_SECRET = "another-setup-intent-client-secret"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakePatients:
    """Just enough patient repository: one visible client, everyone else absent."""

    def __init__(self, *, rate_cents: int | None = None, visible: bool = True) -> None:
        self.visible = visible
        self.patient = Patient(
            id=_PATIENT_ID,
            first_name="A",
            last_name="B",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            rate_cents=rate_cents,
        )

    def get(self, patient_id: str, user_id: str) -> Patient | None:
        if not self.visible or patient_id != _PATIENT_ID or user_id != _USER_ID:
            return None
        return self.patient


class _FakePayments:
    """In-memory card-on-file and ledger, recording each commit boundary."""

    def __init__(self, card: CardOnFile | None = None) -> None:
        self.card = card
        self.charges: list[PatientCharge] = []
        self.commits = 0
        self._next_id = 0

    def get_card_on_file(self, patient_id: str) -> CardOnFile | None:
        return self.card

    def start_card_setup(
        self, *, patient_id: str, stripe_customer_id: str, user_id: str
    ) -> CardOnFile:
        self.card = CardOnFile(
            id="card-row-1",
            patient_id=patient_id,
            stripe_customer_id=stripe_customer_id,
        )
        self.created_by = user_id
        self.commits += 1
        return self.card

    def complete_card_setup(
        self,
        *,
        patient_id: str,
        stripe_payment_method_id: str,
        brand: str | None,
        last4: str | None,
        exp_month: int | None,
        exp_year: int | None,
        user_id: str,
    ) -> CardOnFile | None:
        if self.card is None:
            return None
        self.card = self.card.model_copy(
            update={
                "patient_id": patient_id,
                "stripe_payment_method_id": stripe_payment_method_id,
                "card_brand": brand,
                "card_last4": last4,
                "card_exp_month": exp_month,
                "card_exp_year": exp_year,
            }
        )
        self.commits += 1
        return self.card

    def stage_charge(
        self,
        *,
        patient_id: str,
        appointment_id: str | None,
        amount_cents: int,
        currency: str,
        user_id: str,
        kind: str = "session",
        claim_id: str | None = None,
    ) -> PatientCharge:
        self._next_id += 1
        charge = PatientCharge(
            id=f"charge-{self._next_id}",
            patient_id=patient_id,
            appointment_id=appointment_id,
            kind=kind,
            claim_id=claim_id,
            amount_cents=amount_cents,
            currency=currency,
            status="pending",
            created_by_user_id=user_id,
            created_at=datetime.now(UTC),
        )
        self.charges.append(charge)
        return charge

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
        self.commits += 1
        return charge

    def record_settlement(self, charge_id: str, *, settled_by_charge_id: str) -> None:
        self._replace(charge_id, settled_by_charge_id=settled_by_charge_id)
        self.commits += 1

    def commit(self) -> None:
        self.commits += 1

    def _replace(self, charge_id: str, **updates: Any) -> PatientCharge:
        index = next(i for i, c in enumerate(self.charges) if c.id == charge_id)
        updated = self.charges[index].model_copy(update=updates)
        self.charges[index] = updated
        return updated

    def record_payment_intent(self, charge_id: str, payment_intent_id: str) -> None:
        self._replace(charge_id, stripe_payment_intent_id=payment_intent_id)
        self.commits += 1

    def close_charge(
        self, charge_id: str, *, status: str, status_detail: str | None
    ) -> PatientCharge:
        updated = self._replace(charge_id, status=status, status_detail=status_detail)
        self.commits += 1
        return updated

    def list_charges(self, patient_id: str) -> list[PatientCharge]:
        return [c for c in self.charges if c.patient_id == patient_id]


class _FakeAppointments:
    def __init__(self, appointment: Appointment | None = None) -> None:
        self.appointment = appointment

    def get(self, appointment_id: str, user_id: str) -> Appointment | None:
        if self.appointment is not None and self.appointment.id == appointment_id:
            return self.appointment
        return None


class _FakeCoverage:
    """One client's active coverage, or none at all."""

    def __init__(self, coverage: PatientCoverage | None = None) -> None:
        self.coverage = coverage

    def get_active(self, patient_id: str) -> PatientCoverage | None:
        if self.coverage is not None and self.coverage.patient_id == patient_id:
            return self.coverage
        return None


class _FakeClaims:
    """The newest claim on a visit, keyed the way the real repository keys it."""

    def __init__(self, claim: Claim | None = None) -> None:
        self.claim = claim
        self.asked_for: list[list[str]] = []

    def latest_by_appointment(self, appointment_ids: list[str]) -> dict[str, Claim]:
        self.asked_for.append(appointment_ids)
        if self.claim is None:
            return {}
        return {
            line.appointment_id: self.claim
            for line in self.claim.lines
            if line.appointment_id in appointment_ids
        }


class _FakeAppointmentTypes:
    def __init__(self, appointment_type: AppointmentType | None = None) -> None:
        self.appointment_type = appointment_type

    def get(self, appointment_type_id: str, user_id: str) -> AppointmentType | None:
        if self.appointment_type is not None and self.appointment_type.id == appointment_type_id:
            return self.appointment_type
        return None


class _FixedProvider:
    def __init__(self, credentials: PaymentCredentials | None) -> None:
        self.credentials = credentials
        self.asked_for: list[str | None] = []

    def credentials_for_practice(self, practice_id: str | None) -> PaymentCredentials | None:
        self.asked_for.append(practice_id)
        return self.credentials


def _user() -> User:
    return User(
        id=_USER_ID,
        email="therapist@example.com",
        name="Test Therapist",
        created_at=datetime.now(UTC),
        baa_accepted_at=datetime.now(UTC),
        baa_version="2024-01-01",
    )


def _stored_card() -> CardOnFile:
    return CardOnFile(
        id="card-row-1",
        patient_id=_PATIENT_ID,
        stripe_customer_id="cus_123",
        stripe_payment_method_id="pm_123",
        card_brand="visa",
        card_last4="4242",
        card_exp_month=4,
        card_exp_year=2030,
    )


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _default_provider() -> Any:
    """Every test installs its own provider; put the default back afterwards."""
    yield
    register_payment_credential_provider(None)


def _client(
    payments: _FakePayments,
    patients: _FakePatients,
    *,
    appointments: _FakeAppointments | None = None,
    appointment_types: _FakeAppointmentTypes | None = None,
    coverage: _FakeCoverage | None = None,
    claims: _FakeClaims | None = None,
    credentials: PaymentCredentials | None = PaymentCredentials(
        secret_key=_SECRET_KEY, publishable_key=_PUBLISHABLE_KEY
    ),
    practice_id: str | None = _PRACTICE_ID,
    audit: AuditService | None = None,
) -> TestClient:
    register_payment_credential_provider(_FixedProvider(credentials))

    app = FastAPI()
    app.include_router(patient_payments.router)
    app.dependency_overrides[require_baa_acceptance] = _user
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id=_USER_ID, practice_id=practice_id, practice_schema="practice_x"
    )
    app.dependency_overrides[get_patient_payment_repository] = lambda: payments
    app.dependency_overrides[get_patient_repository] = lambda: patients
    app.dependency_overrides[get_appointment_repository] = lambda: (
        appointments or _FakeAppointments()
    )
    app.dependency_overrides[get_appointment_type_repository] = lambda: (
        appointment_types or _FakeAppointmentTypes()
    )
    app.dependency_overrides[get_patient_coverage_repository] = lambda: coverage or _FakeCoverage()
    app.dependency_overrides[get_claim_repository] = lambda: claims or _FakeClaims()
    # The routes MUST audit; the unit suite has no Postgres to write those to.
    audit_service = audit or AuditService(InMemoryAuditRepository())
    app.dependency_overrides[get_audit_service] = lambda: audit_service
    return TestClient(app, raise_server_exceptions=False)


def _install_stripe(monkeypatch: pytest.MonkeyPatch, responder: Any) -> list[dict[str, Any]]:
    """Record every processor call and answer it with ``responder``."""
    seen: list[dict[str, Any]] = []

    def _fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        seen.append({"method": method, "url": url, **kwargs})
        status_code, body = responder(method, url)
        return httpx.Response(
            status_code,
            content=json.dumps(body).encode(),
            headers={"content-type": "application/json"},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(stripe_api.httpx, "request", _fake_request)
    return seen


def _charge_transport(
    monkeypatch: pytest.MonkeyPatch,
    confirm_status: int,
    confirm_body: dict[str, Any],
    *,
    create_body: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """A two-step-aware transport for the charge path.

    The route creates an UNCONFIRMED PaymentIntent, records its id, and only
    then confirms — so this answers ``/confirm`` with the caller's canned
    outcome and every other PaymentIntent POST with a bare created intent.
    """
    created = create_body or {"id": _PI_ID, "status": "requires_confirmation"}

    def _responder(method: str, url: str) -> tuple[int, dict[str, Any]]:
        if url.endswith("/confirm"):
            return confirm_status, confirm_body
        return 200, created

    return _install_stripe(monkeypatch, _responder)


def _confirm_call(seen: list[dict[str, Any]]) -> dict[str, Any]:
    return next(c for c in seen if c["url"].endswith("/confirm"))


def _create_call(seen: list[dict[str, Any]]) -> dict[str, Any]:
    return next(c for c in seen if not c["url"].endswith("/confirm"))


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------


class TestPreconditions:
    def test_unconfigured_deployment_is_503(self) -> None:
        payments = _FakePayments(_stored_card())
        client = _client(payments, _FakePatients(), credentials=None)

        response = client.post(f"/api/patients/{_PATIENT_ID}/charges", json={"amount_cents": 15000})

        assert response.status_code == 503
        # Nothing was written for a charge that could never start.
        assert payments.charges == []

    def test_provider_is_asked_about_the_callers_practice(self) -> None:
        provider = _FixedProvider(
            PaymentCredentials(secret_key=_SECRET_KEY, publishable_key=_PUBLISHABLE_KEY)
        )
        register_payment_credential_provider(provider)
        payments = _FakePayments(_stored_card())
        client = _client(payments, _FakePatients())
        register_payment_credential_provider(provider)

        client.get(f"/api/patients/{_PATIENT_ID}/charges")

        assert provider.asked_for == [_PRACTICE_ID]

    def test_foreign_client_is_404_not_403(self) -> None:
        client = _client(_FakePayments(_stored_card()), _FakePatients(visible=False))

        for method, path, body in (
            ("POST", f"/api/patients/{_PATIENT_ID}/charges", {"amount_cents": 100}),
            ("GET", f"/api/patients/{_PATIENT_ID}/charges", None),
            ("GET", f"/api/patients/{_PATIENT_ID}/payment-method", None),
            ("POST", f"/api/patients/{_PATIENT_ID}/payment-method/setup", None),
        ):
            response = client.request(method, path, json=body)
            assert response.status_code == 404, path

    def test_unknown_client_id_is_404(self) -> None:
        client = _client(_FakePayments(_stored_card()), _FakePatients())
        response = client.get(f"/api/patients/{_OTHER_PATIENT_ID}/charges")
        assert response.status_code == 404

    def test_charge_without_card_on_file_is_409(self) -> None:
        payments = _FakePayments(card=None)
        client = _client(payments, _FakePatients())

        response = client.post(f"/api/patients/{_PATIENT_ID}/charges", json={"amount_cents": 15000})

        assert response.status_code == 409
        assert payments.charges == []

    def test_unconfirmed_card_is_not_chargeable(self) -> None:
        """A setup that was started and never confirmed has no payment method,
        so it is "no card on file" rather than a chargeable row."""
        started = CardOnFile(id="card-row-1", patient_id=_PATIENT_ID, stripe_customer_id="cus_123")
        payments = _FakePayments(started)
        client = _client(payments, _FakePatients())

        assert client.get(f"/api/patients/{_PATIENT_ID}/payment-method").status_code == 404
        charge = client.post(f"/api/patients/{_PATIENT_ID}/charges", json={"amount_cents": 100})
        assert charge.status_code == 409


# ---------------------------------------------------------------------------
# Card setup
# ---------------------------------------------------------------------------


class TestCardSetup:
    def test_setup_creates_a_customer_and_persists_its_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payments = _FakePayments(card=None)
        client = _client(payments, _FakePatients())

        def _responder(method: str, url: str) -> tuple[int, dict[str, Any]]:
            if url.endswith("/v1/customers"):
                return 200, {"id": "cus_new"}
            return 200, {"client_secret": _CLIENT_SECRET}

        seen = _install_stripe(monkeypatch, _responder)

        response = client.post(f"/api/patients/{_PATIENT_ID}/payment-method/setup")

        assert response.status_code == 200
        assert response.json() == {
            "client_secret": _CLIENT_SECRET,
            "publishable_key": _PUBLISHABLE_KEY,
            "stripe_account_id": None,
        }
        assert payments.card is not None
        assert payments.card.stripe_customer_id == "cus_new"
        # Not chargeable yet: the payment-method id only exists once the
        # browser confirms.
        assert payments.card.stripe_payment_method_id is None
        # Customer creation is keyed on the client so a double-click cannot
        # mint two customers.
        customer_call = next(c for c in seen if c["url"].endswith("/v1/customers"))
        assert (
            customer_call["headers"]["Idempotency-Key"] == f"patient-customer-create:{_PATIENT_ID}"
        )
        # Default configuration charges directly: no on-behalf-of header.
        assert all("Stripe-Account" not in c["headers"] for c in seen)

    def test_setup_reuses_an_existing_customer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payments = _FakePayments(_stored_card())
        client = _client(payments, _FakePatients())
        seen = _install_stripe(
            monkeypatch, lambda *_: (200, {"client_secret": _SECOND_CLIENT_SECRET})
        )

        response = client.post(f"/api/patients/{_PATIENT_ID}/payment-method/setup")

        assert response.status_code == 200
        assert [c["url"] for c in seen] == ["https://api.stripe.com/v1/setup_intents"]

    def test_setup_asks_for_cards_and_nothing_else(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Card-on-file is charged off-session, so the SetupIntent must name
        `card` explicitly.

        Omitting the type does not default to cards — it defers to whatever the
        account has enabled, which included redirect-based methods. Stripe then
        refuses to confirm without a return_url, and a saved redirect method
        could not be charged later with nobody present anyway.
        """
        payments = _FakePayments(_stored_card())
        client = _client(payments, _FakePatients())
        seen = _install_stripe(monkeypatch, lambda *_: (200, {"client_secret": _CLIENT_SECRET}))

        response = client.post(f"/api/patients/{_PATIENT_ID}/payment-method/setup")

        assert response.status_code == 200
        assert seen[0]["data"]["payment_method_types[0]"] == "card"
        assert "automatic_payment_methods[enabled]" not in seen[0]["data"]

    def test_configured_account_id_is_sent_as_a_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A deployment whose provider names an account has every call made for
        that account, and the browser is told which one to initialise against."""
        payments = _FakePayments(_stored_card())
        client = _client(
            payments,
            _FakePatients(),
            credentials=PaymentCredentials(
                secret_key=_SECRET_KEY,
                publishable_key=_PUBLISHABLE_KEY,
                account_id="acct_x",
            ),
        )
        seen = _install_stripe(
            monkeypatch, lambda *_: (200, {"client_secret": _SECOND_CLIENT_SECRET})
        )

        response = client.post(f"/api/patients/{_PATIENT_ID}/payment-method/setup")

        assert response.json()["stripe_account_id"] == "acct_x"
        assert {c["headers"]["Stripe-Account"] for c in seen} == {"acct_x"}

    def test_setup_hands_the_browser_the_key_it_must_initialise_with(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The publishable key travels with the client secret it belongs to.

        The browser needs the key, the account and the client secret to agree
        with the secret key the resulting card will be charged with. Resolving
        all of them from the same credentials is what makes that true by
        construction rather than by matching configuration on two containers.
        """
        client = _client(
            _FakePayments(_stored_card()),
            _FakePatients(),
            credentials=PaymentCredentials(
                secret_key=_SECRET_KEY,
                publishable_key=_PUBLISHABLE_KEY,
                account_id="acct_x",
            ),
        )
        _install_stripe(monkeypatch, lambda *_: (200, {"client_secret": _SECOND_CLIENT_SECRET}))

        body = client.post(f"/api/patients/{_PATIENT_ID}/payment-method/setup").json()

        assert body["publishable_key"] == _PUBLISHABLE_KEY
        assert body["stripe_account_id"] == "acct_x"

    def test_setup_without_a_publishable_key_is_503_and_creates_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No key means the browser cannot collect a card, so nothing is minted.

        Half-working is the failure to avoid: without this the deployment would
        create a customer and a SetupIntent in the practice's Stripe account for
        a flow that could never finish.
        """
        payments = _FakePayments(card=None)
        client = _client(
            payments,
            _FakePatients(),
            credentials=PaymentCredentials(secret_key=_SECRET_KEY),
        )
        seen = _install_stripe(monkeypatch, lambda *_: (200, {"client_secret": _CLIENT_SECRET}))

        response = client.post(f"/api/patients/{_PATIENT_ID}/payment-method/setup")

        assert response.status_code == 503
        assert payments.card is None
        assert seen == []

    def test_complete_setup_persists_the_payment_method_and_display_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        started = CardOnFile(id="card-row-1", patient_id=_PATIENT_ID, stripe_customer_id="cus_123")
        payments = _FakePayments(started)
        client = _client(payments, _FakePatients())

        def _responder(method: str, url: str) -> tuple[int, dict[str, Any]]:
            if "/v1/setup_intents/" in url:
                return 200, {
                    "status": "succeeded",
                    "payment_method": "pm_new",
                    "customer": "cus_123",
                }
            return 200, {
                "card": {
                    "brand": "mastercard",
                    "last4": "4444",
                    "exp_month": 12,
                    "exp_year": 2031,
                }
            }

        _install_stripe(monkeypatch, _responder)

        response = client.post(
            f"/api/patients/{_PATIENT_ID}/payment-method", json={"setup_intent_id": "seti_1"}
        )

        assert response.status_code == 200
        assert response.json() == {
            "brand": "mastercard",
            "last4": "4444",
            "exp_month": 12,
            "exp_year": 2031,
            "chargeable": True,
        }
        assert payments.card is not None
        assert payments.card.stripe_payment_method_id == "pm_new"
        # Display fields only — there is nowhere on the model a card number
        # could be written.
        assert "card_number" not in CardOnFile.model_fields

    def test_complete_setup_rejects_another_clients_setup_intent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A SetupIntent belonging to a different customer must not attach its
        card to this client's row."""
        started = CardOnFile(id="card-row-1", patient_id=_PATIENT_ID, stripe_customer_id="cus_123")
        payments = _FakePayments(started)
        client = _client(payments, _FakePatients())
        _install_stripe(
            monkeypatch,
            lambda *_: (
                200,
                {
                    "status": "succeeded",
                    "payment_method": "pm_other",
                    "customer": "cus_someone_else",
                },
            ),
        )

        response = client.post(
            f"/api/patients/{_PATIENT_ID}/payment-method", json={"setup_intent_id": "seti_other"}
        )

        assert response.status_code == 404
        assert payments.card is not None
        assert payments.card.stripe_payment_method_id is None

    def test_complete_setup_is_409_when_the_intent_has_not_succeeded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        started = CardOnFile(id="card-row-1", patient_id=_PATIENT_ID, stripe_customer_id="cus_123")
        payments = _FakePayments(started)
        client = _client(payments, _FakePatients())
        _install_stripe(
            monkeypatch,
            lambda *_: (
                200,
                {
                    "status": "requires_payment_method",
                    "payment_method": None,
                    "customer": "cus_123",
                },
            ),
        )

        response = client.post(
            f"/api/patients/{_PATIENT_ID}/payment-method", json={"setup_intent_id": "seti_1"}
        )

        assert response.status_code == 409
        assert payments.card is not None
        assert payments.card.stripe_payment_method_id is None


# ---------------------------------------------------------------------------
# Charging
# ---------------------------------------------------------------------------


class TestCharge:
    def test_success_writes_a_pending_row_then_flips_it_to_succeeded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payments = _FakePayments(_stored_card())
        client = _client(payments, _FakePatients())
        seen = _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        response = client.post(f"/api/patients/{_PATIENT_ID}/charges", json={"amount_cents": 15000})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "succeeded"
        assert body["amount_cents"] == 15000
        assert body["currency"] == "usd"
        assert body["status_detail"] is None

        # Three commits: the pending row (with its audit entry), the
        # PaymentIntent id before confirming, then the outcome.
        assert len(payments.charges) == 1
        assert payments.commits == 3
        assert payments.charges[0].stripe_payment_intent_id == _PI_ID

        sent = _create_call(seen)["data"]
        assert sent["currency"] == "usd"
        assert sent["customer"] == "cus_123"
        assert sent["payment_method"] == "pm_123"
        # Created UNCONFIRMED — confirming is a separate call, after the id has
        # been written down.
        assert "confirm" not in sent

        # Opaque ids only: the ledger row, the acting clinician, the practice.
        # No client id, no appointment id, no clinical content.
        assert sorted(k for k in sent if k.startswith("metadata[")) == [
            "metadata[pablo_charge_id]",
            "metadata[pablo_practice_id]",
            "metadata[pablo_user_id]",
        ]
        assert sent["metadata[pablo_charge_id]"] == payments.charges[0].id
        assert sent["metadata[pablo_user_id]"] == _USER_ID
        assert sent["metadata[pablo_practice_id]"] == _PRACTICE_ID

        # Both calls are idempotency-keyed on our own ledger id, so a retried
        # request cannot charge the card twice.
        charge_id = payments.charges[0].id
        assert (
            _create_call(seen)["headers"]["Idempotency-Key"] == f"patient-charge-create:{charge_id}"
        )
        assert (
            _confirm_call(seen)["headers"]["Idempotency-Key"]
            == f"patient-charge-confirm:{charge_id}"
        )

    def test_off_session_is_named_on_the_confirm_not_the_create(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stripe rejects ``off_session`` on a create that does not confirm.

        The whole point of the two-step flow is a create that does *not*
        confirm, so naming ``off_session`` there is a 400 — which this route
        turns into a 502, and which no fake transport can notice, because the
        rule lives in Stripe's validation rather than in our code. It cost a
        deployed e2e run to find. This pins both halves of the move.
        """
        payments = _FakePayments(_stored_card())
        client = _client(payments, _FakePatients())
        seen = _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        response = client.post(f"/api/patients/{_PATIENT_ID}/charges", json={"amount_cents": 15000})

        assert response.status_code == 200
        assert "off_session" not in _create_call(seen)["data"]
        assert _confirm_call(seen)["data"]["off_session"] == "true"

    def test_the_payment_intent_id_is_recorded_before_the_confirm_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """By the time money can move, the PaymentIntent id has been written
        down. Confirming inside the create call would let a timeout leave the
        processor holding a completed payment whose id we never learned."""
        payments = _FakePayments(_stored_card())
        client = _client(payments, _FakePatients())
        at_confirm: dict[str, Any] = {}

        def _responder(method: str, url: str) -> tuple[int, dict[str, Any]]:
            if url.endswith("/confirm"):
                at_confirm["intent_on_row"] = payments.charges[0].stripe_payment_intent_id
                at_confirm["commits"] = payments.commits
                return 200, {"id": _PI_ID, "status": "succeeded"}
            return 200, {"id": _PI_ID, "status": "requires_confirmation"}

        _install_stripe(monkeypatch, _responder)

        response = client.post(f"/api/patients/{_PATIENT_ID}/charges", json={"amount_cents": 15000})

        assert response.status_code == 200
        assert at_confirm["intent_on_row"] == _PI_ID
        assert at_confirm["commits"] == 2

    def test_a_decline_lands_failed_with_the_decline_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payments = _FakePayments(_stored_card())
        client = _client(payments, _FakePatients())
        _charge_transport(
            monkeypatch,
            402,
            {
                "error": {
                    "code": "card_declined",
                    "decline_code": "insufficient_funds",
                    "payment_intent": {"id": _PI_ID, "status": "requires_payment_method"},
                }
            },
        )

        response = client.post(f"/api/patients/{_PATIENT_ID}/charges", json={"amount_cents": 15000})

        # A decline is an answer, not an error: the clinician gets the ledger
        # row and the reason rather than an exception that discards both.
        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        assert response.json()["status_detail"] == "insufficient_funds"
        # The id came from the create call, not from the error envelope.
        assert payments.charges[0].stripe_payment_intent_id == _PI_ID

    def test_a_decline_without_a_decline_code_falls_back_to_the_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payments = _FakePayments(_stored_card())
        client = _client(payments, _FakePatients())
        _charge_transport(
            monkeypatch,
            402,
            {
                "error": {
                    "code": "authentication_required",
                    "payment_intent": {"id": _PI_ID, "status": "requires_action"},
                }
            },
        )

        response = client.post(f"/api/patients/{_PATIENT_ID}/charges", json={"amount_cents": 15000})

        assert response.json()["status_detail"] == "authentication_required"

    def test_a_non_succeeded_intent_is_failed_with_the_intent_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payments = _FakePayments(_stored_card())
        client = _client(payments, _FakePatients())
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "processing"})

        response = client.post(f"/api/patients/{_PATIENT_ID}/charges", json={"amount_cents": 15000})

        assert response.json()["status"] == "failed"
        assert response.json()["status_detail"] == "processing"

    def test_an_unreachable_processor_leaves_the_row_pending(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point of the ledger-first ordering: an attempt that never
        got an answer is still on the books, as ``pending``, to reconcile."""
        payments = _FakePayments(_stored_card())
        client = _client(payments, _FakePatients())

        def _boom(method: str, url: str, **kwargs: Any) -> httpx.Response:
            raise httpx.ConnectError("no route to the processor")

        monkeypatch.setattr(stripe_api.httpx, "request", _boom)

        response = client.post(f"/api/patients/{_PATIENT_ID}/charges", json={"amount_cents": 15000})

        assert response.status_code == 502
        assert len(payments.charges) == 1
        assert payments.charges[0].status == "pending"

    def test_the_amount_must_be_positive_and_bounded(self) -> None:
        payments = _FakePayments(_stored_card())
        client = _client(payments, _FakePatients())

        for amount in (0, -100, 10_000_000):
            response = client.post(
                f"/api/patients/{_PATIENT_ID}/charges", json={"amount_cents": amount}
            )
            assert response.status_code == 422, amount
        assert payments.charges == []


# ---------------------------------------------------------------------------
# Where the amount comes from
# ---------------------------------------------------------------------------


def _appointment() -> Appointment:
    now = datetime.now(UTC)
    return Appointment(
        id=_APPOINTMENT_ID,
        user_id=_USER_ID,
        patient_id=_PATIENT_ID,
        title="Session",
        start_at=now,
        end_at=now,
        duration_minutes=50,
        status="scheduled",
        session_type="Standard",
        appointment_type_id=_TYPE_ID,
    )


def _coverage(**overrides: Any) -> PatientCoverage:
    now = datetime.now(UTC)
    fields: dict[str, Any] = {
        "id": "cov-1",
        "patient_id": _PATIENT_ID,
        "payer_id": "payer-1",
        "member_id": "123456789",
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    return PatientCoverage(**fields)


def _271_with_copay(dollars: str) -> dict[str, Any]:
    """A stored eligibility response carrying one behavioral copay line."""
    return {
        "meta": {"traceId": "trace-1"},
        "benefitsInformation": [
            {
                "code": "B",
                "serviceTypeCodes": ["MH"],
                "timeQualifierCode": "27",
                "benefitAmount": dollars,
            }
        ],
    }


class TestCopay:
    """The copay a covered client pays at the door, on the card on file.

    Same route, same card, same ledger — what makes it a copay is the row's
    kind, which is what lets a later remittance net it out instead of
    counting the visit as paid twice.
    """

    def test_the_override_is_charged_and_the_row_says_it_was_a_copay(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payments = _FakePayments(_stored_card())
        client = _client(
            payments,
            _FakePatients(rate_cents=15000),
            coverage=_FakeCoverage(_coverage(copay_override_cents=3000)),
        )
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        response = client.post(
            f"/api/patients/{_PATIENT_ID}/charges",
            json={"kind": "copay", "appointment_id": _APPOINTMENT_ID},
        )

        assert response.status_code == 200
        body = response.json()
        # The copay, not the client's $150 rate.
        assert body["amount_cents"] == 3000
        assert body["kind"] == "copay"
        assert body["appointment_id"] == _APPOINTMENT_ID
        assert payments.charges[0].kind == "copay"

    def test_the_payers_answer_is_the_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        coverage = _coverage(
            last_271=_271_with_copay("25"), verified_at=datetime(2026, 9, 1, tzinfo=UTC)
        )
        client = _client(
            _FakePayments(_stored_card()),
            _FakePatients(rate_cents=15000),
            coverage=_FakeCoverage(coverage),
        )
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        response = client.post(f"/api/patients/{_PATIENT_ID}/charges", json={"kind": "copay"})

        assert response.json()["amount_cents"] == 2500

    def test_a_typed_amount_is_charged_when_nothing_is_on_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(
            _FakePayments(_stored_card()),
            _FakePatients(rate_cents=15000),
            coverage=_FakeCoverage(_coverage()),
        )
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        response = client.post(
            f"/api/patients/{_PATIENT_ID}/charges", json={"kind": "copay", "amount_cents": 2000}
        )

        assert response.json()["amount_cents"] == 2000

    def test_no_copay_anywhere_refuses_rather_than_charging_the_full_rate(self) -> None:
        payments = _FakePayments(_stored_card())
        client = _client(
            payments, _FakePatients(rate_cents=15000), coverage=_FakeCoverage(_coverage())
        )

        response = client.post(f"/api/patients/{_PATIENT_ID}/charges", json={"kind": "copay"})

        assert response.status_code == 422
        # The session rate is exactly the wrong guess, so nothing was staged.
        assert payments.charges == []

    def test_an_uncovered_client_has_no_copay_to_charge(self) -> None:
        client = _client(_FakePayments(_stored_card()), _FakePatients(rate_cents=15000))

        response = client.post(f"/api/patients/{_PATIENT_ID}/charges", json={"kind": "copay"})

        assert response.status_code == 422

    def test_the_row_is_linked_to_the_claim_already_on_the_visit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payments = _FakePayments(_stored_card())
        filed = claim_fixture(lines=[line_fixture(appointment_id=_APPOINTMENT_ID)])
        client = _client(
            payments,
            _FakePatients(rate_cents=15000),
            coverage=_FakeCoverage(_coverage(copay_override_cents=3000)),
            claims=_FakeClaims(filed),
        )
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        response = client.post(
            f"/api/patients/{_PATIENT_ID}/charges",
            json={"kind": "copay", "appointment_id": _APPOINTMENT_ID},
        )

        assert response.json()["claim_id"] == filed.id

    def test_a_visit_with_no_claim_yet_leaves_the_link_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The ordinary case: the copay is taken at the visit and the claim is
        # filed afterwards.
        client = _client(
            _FakePayments(_stored_card()),
            _FakePatients(rate_cents=15000),
            coverage=_FakeCoverage(_coverage(copay_override_cents=3000)),
        )
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        response = client.post(
            f"/api/patients/{_PATIENT_ID}/charges",
            json={"kind": "copay", "appointment_id": _APPOINTMENT_ID},
        )

        assert response.json()["claim_id"] is None

    def test_a_full_rate_charge_is_never_linked_to_a_claim(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        claims = _FakeClaims(claim_fixture(lines=[line_fixture(appointment_id=_APPOINTMENT_ID)]))
        client = _client(
            _FakePayments(_stored_card()), _FakePatients(rate_cents=15000), claims=claims
        )
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        response = client.post(
            f"/api/patients/{_PATIENT_ID}/charges", json={"appointment_id": _APPOINTMENT_ID}
        )

        assert response.json()["kind"] == "session"
        assert response.json()["claim_id"] is None
        # Not even asked: a self-pay charge has no claim behind it.
        assert claims.asked_for == []

    def test_a_ledger_only_kind_cannot_be_raised_as_a_card_charge(self) -> None:
        payments = _FakePayments(_stored_card())
        client = _client(payments, _FakePatients(rate_cents=15000))

        response = client.post(
            f"/api/patients/{_PATIENT_ID}/charges",
            json={"kind": "write_off", "amount_cents": 5000},
        )

        assert response.status_code == 422
        assert payments.charges == []


class TestAmountResolution:
    def test_the_clients_own_rate_is_used_when_no_amount_is_sent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payments = _FakePayments(_stored_card())
        client = _client(payments, _FakePatients(rate_cents=17500))
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        response = client.post(f"/api/patients/{_PATIENT_ID}/charges", json={})

        assert response.status_code == 200
        assert response.json()["amount_cents"] == 17500

    def test_the_appointment_types_default_fee_is_the_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payments = _FakePayments(_stored_card())
        client = _client(
            payments,
            _FakePatients(rate_cents=None),
            appointments=_FakeAppointments(_appointment()),
            appointment_types=_FakeAppointmentTypes(
                AppointmentType(
                    id=_TYPE_ID, user_id=_USER_ID, name="Standard", default_fee_cents=12000
                )
            ),
        )
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        response = client.post(
            f"/api/patients/{_PATIENT_ID}/charges", json={"appointment_id": _APPOINTMENT_ID}
        )

        assert response.status_code == 200
        assert response.json()["amount_cents"] == 12000

    def test_the_clients_rate_wins_over_the_types_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payments = _FakePayments(_stored_card())
        client = _client(
            payments,
            _FakePatients(rate_cents=15000),
            appointments=_FakeAppointments(_appointment()),
            appointment_types=_FakeAppointmentTypes(
                AppointmentType(
                    id=_TYPE_ID, user_id=_USER_ID, name="Standard", default_fee_cents=12000
                )
            ),
        )
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        response = client.post(
            f"/api/patients/{_PATIENT_ID}/charges", json={"appointment_id": _APPOINTMENT_ID}
        )

        assert response.json()["amount_cents"] == 15000

    def test_an_explicit_amount_overrides_the_resolved_rate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payments = _FakePayments(_stored_card())
        client = _client(payments, _FakePatients(rate_cents=15000))
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        response = client.post(f"/api/patients/{_PATIENT_ID}/charges", json={"amount_cents": 5000})

        assert response.json()["amount_cents"] == 5000

    def test_no_rate_anywhere_refuses_rather_than_guessing(self) -> None:
        payments = _FakePayments(_stored_card())
        client = _client(payments, _FakePatients(rate_cents=None))

        response = client.post(f"/api/patients/{_PATIENT_ID}/charges", json={})

        assert response.status_code == 422
        assert payments.charges == []


# ---------------------------------------------------------------------------
# Reading the ledger
# ---------------------------------------------------------------------------


class TestLedgerRead:
    def test_lists_this_clients_charges(self) -> None:
        payments = _FakePayments(_stored_card())
        payments.charges.append(
            PatientCharge(
                id="c1",
                patient_id=_PATIENT_ID,
                amount_cents=15000,
                currency="usd",
                status="succeeded",
                stripe_payment_intent_id="pi_1",
                created_by_user_id=_USER_ID,
                created_at=datetime.now(UTC),
            )
        )
        client = _client(payments, _FakePatients())

        response = client.get(f"/api/patients/{_PATIENT_ID}/charges")

        assert response.status_code == 200
        rows = response.json()
        assert len(rows) == 1
        assert rows[0]["id"] == "c1"
        # The ledger response is amounts, kinds and statuses: no processor
        # customer or payment-method id, no card data.
        assert set(rows[0]) == {
            "id",
            "amount_cents",
            "currency",
            "status",
            "status_detail",
            "appointment_id",
            "kind",
            "claim_id",
            "write_off_reason",
            "note",
            "settled_by_charge_id",
            "created_at",
            "updated_at",
        }

    def test_a_row_written_before_kinds_existed_reads_as_a_session_charge(self) -> None:
        payments = _FakePayments()
        payments.charges.append(
            PatientCharge(
                id="c1",
                patient_id=_PATIENT_ID,
                amount_cents=15000,
                currency="usd",
                status="succeeded",
                created_by_user_id=_USER_ID,
                created_at=datetime.now(UTC),
            )
        )
        client = _client(payments, _FakePatients())

        rows = client.get(f"/api/patients/{_PATIENT_ID}/charges").json()

        assert rows[0]["kind"] == "session"

    def test_no_card_on_file_is_404(self) -> None:
        client = _client(_FakePayments(card=None), _FakePatients())
        assert client.get(f"/api/patients/{_PATIENT_ID}/payment-method").status_code == 404


# ---------------------------------------------------------------------------
# What the clinician is shown before charging
# ---------------------------------------------------------------------------


class TestChargeAmountPreview:
    def test_previews_the_clients_own_rate(self) -> None:
        client = _client(_FakePayments(_stored_card()), _FakePatients(rate_cents=17500))

        response = client.get(f"/api/patients/{_PATIENT_ID}/charge-amount")

        assert response.status_code == 200
        assert response.json() == {"amount_cents": 17500, "currency": DEFAULT_CHARGE_CURRENCY}

    def test_previews_the_appointment_types_default_fee(self) -> None:
        client = _client(
            _FakePayments(_stored_card()),
            _FakePatients(rate_cents=None),
            appointments=_FakeAppointments(_appointment()),
            appointment_types=_FakeAppointmentTypes(
                AppointmentType(
                    id=_TYPE_ID, user_id=_USER_ID, name="Standard", default_fee_cents=12000
                )
            ),
        )

        response = client.get(
            f"/api/patients/{_PATIENT_ID}/charge-amount",
            params={"appointment_id": _APPOINTMENT_ID},
        )

        assert response.json()["amount_cents"] == 12000

    def test_the_preview_is_the_amount_the_charge_would_use(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Preview and charge must agree — a figure shown and then not charged
        is worse than no figure at all."""
        payments = _FakePayments(_stored_card())
        client = _client(payments, _FakePatients(rate_cents=15000))
        previewed = client.get(f"/api/patients/{_PATIENT_ID}/charge-amount").json()["amount_cents"]
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        charged = client.post(f"/api/patients/{_PATIENT_ID}/charges", json={}).json()

        assert charged["amount_cents"] == previewed

    def test_no_rate_anywhere_previews_as_unset_not_zero(self) -> None:
        """``None``, so the UI asks for an amount rather than offering to
        charge nothing."""
        client = _client(_FakePayments(_stored_card()), _FakePatients(rate_cents=None))

        response = client.get(f"/api/patients/{_PATIENT_ID}/charge-amount")

        assert response.status_code == 200
        assert response.json()["amount_cents"] is None

    def test_foreign_client_is_404(self) -> None:
        client = _client(_FakePayments(_stored_card()), _FakePatients(visible=False))
        assert client.get(f"/api/patients/{_PATIENT_ID}/charge-amount").status_code == 404

    def test_unconfigured_deployment_is_503(self) -> None:
        client = _client(_FakePayments(_stored_card()), _FakePatients(), credentials=None)
        assert client.get(f"/api/patients/{_PATIENT_ID}/charge-amount").status_code == 503


class TestPatientBalance:
    """``GET /api/patients/{id}/balance`` — the ledger totalled, never stored."""

    def _ledger(self, *charges: PatientCharge) -> _FakePayments:
        payments = _FakePayments()
        payments.charges.extend(charges)
        return payments

    def _row(self, **overrides: Any) -> PatientCharge:
        row: dict[str, Any] = {
            "id": "c1",
            "patient_id": _PATIENT_ID,
            "amount_cents": 10_000,
            "currency": "usd",
            "status": "pending",
            "created_by_user_id": _USER_ID,
            "created_at": datetime.now(UTC),
        }
        row.update(overrides)
        return PatientCharge(**row)

    def test_totals_the_clients_ledger(self) -> None:
        payments = self._ledger(
            self._row(id="c1", kind="session", status="pending", amount_cents=10_000),
            self._row(id="c2", kind="copay", status="succeeded", amount_cents=2_500),
        )
        client = _client(payments, _FakePatients())

        response = client.get(f"/api/patients/{_PATIENT_ID}/balance")

        assert response.status_code == 200
        body = response.json()
        assert body["owed_cents"] == 10_000
        assert body["collected_cents"] == 2_500
        assert body["balance_cents"] == 7_500

    def test_a_credit_is_reported_as_a_negative_balance(self) -> None:
        """The practice owes the client; clamping to zero would hide a refund."""
        payments = self._ledger(
            self._row(id="c1", kind="copay", status="succeeded", amount_cents=5_000),
        )
        client = _client(payments, _FakePatients())

        assert client.get(f"/api/patients/{_PATIENT_ID}/balance").json()["balance_cents"] == -5_000

    def test_a_practice_with_no_card_processor_still_gets_a_balance(self) -> None:
        """A practice that only bills insurance has owed rows and no Stripe."""
        payments = self._ledger(
            self._row(id="c1", kind="patient_resp", status="pending", amount_cents=4_000),
        )
        client = _client(payments, _FakePatients(), credentials=None)

        response = client.get(f"/api/patients/{_PATIENT_ID}/balance")

        assert response.status_code == 200
        assert response.json()["balance_cents"] == 4_000

    def test_foreign_client_is_404(self) -> None:
        client = _client(_FakePayments(), _FakePatients(visible=False))
        assert client.get(f"/api/patients/{_PATIENT_ID}/balance").status_code == 404

    def test_the_read_is_audited(self) -> None:
        payments = self._ledger(self._row(id="c1", kind="session", status="pending"))
        repository = InMemoryAuditRepository()
        client = _client(payments, _FakePatients(), audit=AuditService(repository))

        client.get(f"/api/patients/{_PATIENT_ID}/balance")

        logged = repository.list_for_user(_USER_ID)
        assert [entry.resource_id for entry in logged] == [_PATIENT_ID]


class TestChargeBalance:
    """``POST /api/patients/{id}/charge-balance`` — collect the whole balance."""

    def _ledger(self, *charges: PatientCharge) -> _FakePayments:
        payments = _FakePayments(_stored_card())
        payments.charges.extend(charges)
        return payments

    def _row(self, **overrides: Any) -> PatientCharge:
        row: dict[str, Any] = {
            "id": "c1",
            "patient_id": _PATIENT_ID,
            "amount_cents": 4_000,
            "currency": "usd",
            "status": "pending",
            "kind": "patient_resp",
            "created_by_user_id": _USER_ID,
            "created_at": datetime.now(UTC),
        }
        row.update(overrides)
        return PatientCharge(**row)

    def _post(self, client: TestClient) -> Any:
        return client.post(f"/api/patients/{_PATIENT_ID}/charge-balance")

    def test_it_charges_what_the_ledger_says_is_owed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The amount is read here, not sent: a figure the browser saw before a
        remittance landed must not be the figure that gets charged."""
        payments = self._ledger(self._row(amount_cents=6_200))
        client = _client(payments, _FakePatients())
        seen = _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        response = self._post(client)

        assert response.status_code == 200
        assert response.json()["amount_cents"] == 6_200
        assert _create_call(seen)["data"]["amount"] == 6_200

    def test_the_row_is_a_payment_not_a_session_charge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A session row is itself a bill, so paying a balance with one would
        re-bill the very amount it settles — which is what ``payment`` is for."""
        payments = self._ledger(self._row())
        client = _client(payments, _FakePatients())
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        assert self._post(client).json()["kind"] == "payment"

    def test_the_balance_reads_zero_afterwards(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The point of the whole action, checked through the balance route so
        it is the real arithmetic and not a restatement of it."""
        payments = self._ledger(self._row(amount_cents=4_000))
        client = _client(payments, _FakePatients())
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        assert client.get(f"/api/patients/{_PATIENT_ID}/balance").json()["balance_cents"] == 4_000

        self._post(client)

        assert client.get(f"/api/patients/{_PATIENT_ID}/balance").json()["balance_cents"] == 0

    def test_it_stamps_the_bills_it_cleared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Provenance for the statement: which payment cleared which bill."""
        payments = self._ledger(self._row(id="resp-1"))
        client = _client(payments, _FakePatients())
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        payment_id = self._post(client).json()["id"]

        cleared = next(c for c in payments.charges if c.id == "resp-1")
        assert cleared.settled_by_charge_id == payment_id

    def test_a_decline_stamps_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No money arrived, so no bill was cleared — and the balance stands."""
        payments = self._ledger(self._row(id="resp-1"))
        client = _client(payments, _FakePatients())
        _charge_transport(
            monkeypatch,
            402,
            {"error": {"decline_code": "insufficient_funds", "payment_intent": {"id": _PI_ID}}},
        )

        response = self._post(client)

        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        assert next(c for c in payments.charges if c.id == "resp-1").settled_by_charge_id is None
        assert client.get(f"/api/patients/{_PATIENT_ID}/balance").json()["balance_cents"] == 4_000

    def test_a_session_row_is_not_stamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """It is its own payment attempt, so "which charge paid it" is already
        answered by its own status; a declined one is retried from the note."""
        payments = self._ledger(self._row(id="sess-1", kind="session", status="failed"))
        client = _client(payments, _FakePatients())
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        self._post(client)

        assert next(c for c in payments.charges if c.id == "sess-1").settled_by_charge_id is None

    def test_nothing_owed_is_409(self) -> None:
        payments = self._ledger()
        client = _client(payments, _FakePatients())

        response = self._post(client)

        assert response.status_code == 409
        assert payments.charges == []

    def test_a_payment_already_in_flight_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The case a double-click actually produces: two requests at once.

        The zero-balance check does NOT cover this. A staged row is ``pending``,
        and ``pending`` is not a status the balance counts as collected — so
        while the first request is still at the processor the balance is
        unchanged, and without this guard the second request would read the full
        amount and charge the card again.
        """
        payments = self._ledger(
            self._row(id="resp-1"),
            self._row(id="in-flight", kind="payment", status="pending"),
        )
        client = _client(payments, _FakePatients())
        seen = _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        response = self._post(client)

        assert response.status_code == 409
        # Nothing staged and nothing sent: the card was not touched a second
        # time, which is the whole point.
        assert [row.id for row in payments.charges] == ["resp-1", "in-flight"]
        assert seen == []

    def test_a_pending_session_charge_does_not_block_a_payment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only a payment in flight blocks a payment. A session charge is a
        different act on a different row, and blocking on one would strand
        collection behind an unrelated attempt."""
        payments = self._ledger(
            self._row(id="sess-1", kind="session", status="pending", amount_cents=4_000),
        )
        client = _client(payments, _FakePatients())
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        assert self._post(client).status_code == 200

    def test_a_failed_payment_does_not_block_a_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A decline is terminal, and retrying is a fresh charge somebody asked
        for — not an attempt still in flight."""
        payments = self._ledger(
            self._row(id="resp-1"),
            self._row(id="declined", kind="payment", status="failed"),
        )
        client = _client(payments, _FakePatients())
        _charge_transport(monkeypatch, 200, {"id": _PI_ID, "status": "succeeded"})

        assert self._post(client).status_code == 200

    def test_a_credit_balance_is_409_rather_than_a_negative_charge(self) -> None:
        payments = self._ledger(self._row(kind="credit", status="succeeded"))
        client = _client(payments, _FakePatients())

        assert self._post(client).status_code == 409

    def test_an_implausible_balance_is_refused(self) -> None:
        """The same blast-radius cap the per-charge route applies, which a
        balance built from many rows can reach with nobody typing it."""
        payments = self._ledger(self._row(amount_cents=1_000_001))
        client = _client(payments, _FakePatients())

        assert self._post(client).status_code == 422
        # Refused before anything was staged: the only row is the bill itself.
        assert [row.id for row in payments.charges] == ["c1"]

    def test_no_card_on_file_is_409(self) -> None:
        payments = _FakePayments(card=None)
        payments.charges.append(self._row())
        client = _client(payments, _FakePatients())

        assert self._post(client).status_code == 409

    def test_foreign_client_is_404(self) -> None:
        client = _client(self._ledger(self._row()), _FakePatients(visible=False))

        assert self._post(client).status_code == 404

    def test_the_charge_is_audited_before_the_processor_is_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The § 164.312(b) event is "this clinician asked to charge this
        client", true the moment the row exists — not only when it succeeds."""
        payments = self._ledger(self._row())
        repository = InMemoryAuditRepository()
        client = _client(payments, _FakePatients(), audit=AuditService(repository))
        _charge_transport(
            monkeypatch,
            402,
            {"error": {"decline_code": "card_declined", "payment_intent": {"id": _PI_ID}}},
        )

        self._post(client)

        logged = repository.list_for_user(_USER_ID)
        assert [entry.action for entry in logged] == ["patient_charge_created"]
        assert logged[0].resource_id == _PATIENT_ID
