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
from typing import Any

import httpx
import pytest
from app.auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from app.db.models import DEFAULT_CHARGE_CURRENCY
from app.models import User
from app.models.patient import Patient
from app.models.payments import CardOnFile, PatientCharge
from app.payments import stripe_api
from app.payments.provider import PaymentCredentials, register_payment_credential_provider
from app.repositories import (
    get_appointment_repository,
    get_appointment_type_repository,
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
    ) -> PatientCharge:
        self._next_id += 1
        charge = PatientCharge(
            id=f"charge-{self._next_id}",
            patient_id=patient_id,
            appointment_id=appointment_id,
            amount_cents=amount_cents,
            currency=currency,
            status="pending",
            created_by_user_id=user_id,
            created_at=datetime.now(UTC),
        )
        self.charges.append(charge)
        return charge

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
    credentials: PaymentCredentials | None = PaymentCredentials(
        secret_key=_SECRET_KEY, publishable_key=_PUBLISHABLE_KEY
    ),
    practice_id: str | None = _PRACTICE_ID,
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
    # The routes MUST audit; the unit suite has no Postgres to write those to.
    app.dependency_overrides[get_audit_service] = lambda: AuditService(InMemoryAuditRepository())
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
        # The ledger response is amounts and statuses: no processor customer or
        # payment-method id, no card data.
        assert set(rows[0]) == {
            "id",
            "amount_cents",
            "currency",
            "status",
            "status_detail",
            "appointment_id",
            "created_at",
            "updated_at",
        }

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
