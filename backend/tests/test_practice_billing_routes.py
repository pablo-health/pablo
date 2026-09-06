# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The billing-profile endpoint the settings form talks to.

* the tax id goes in whole and comes back as its last four digits only —
  neither response nor any later read carries the number;
* a save that completes the profile registers the practice's provider
  record with the clearinghouse through the enrollment service, once, and
  the response carries the clearinghouse's id;
* an incomplete save registers nothing and still succeeds.

Runs the real router over an in-memory SQLite session with the clearinghouse
answered from recorded fixtures.
"""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from app.auth.service import TenantContext, get_tenant_context, require_active_subscription
from app.claims import enrollment
from app.db.models import PracticeBillingProfileRow
from app.models import User
from app.routes import practice_billing as practice_billing_routes
from app.settings import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.enrollment_fakes import PROVIDER_ID, FakeClearinghouse
from tests.sqlite_engine import sqlite_engine

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine

_USER_ID = "11111111-1111-4111-8111-111111111111"

_COMPLETE = {
    "legal_name": "Pablo Health Test Provider",
    "tax_id": "84-4459714",
    "tax_id_type": "ein",
    "billing_npi": "1999999984",
    "address_line1": "1 Test St",
    "city": "Atlanta",
    "state": "GA",
    "postal_code": "30301",
    "phone": "4045550100",
    "contact_email": "billing@example.com",
}


def _user() -> User:
    return User(
        id=_USER_ID,
        email="therapist@example.com",
        name="Test Therapist",
        created_at=datetime.now(UTC),
        baa_accepted_at=datetime.now(UTC),
        baa_version="2024-01-01",
    )


def _tenant() -> TenantContext:
    return TenantContext(user_id=_USER_ID, practice_id="practice-1", practice_schema="practice_x")


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def engine() -> Iterator[Engine]:
    with sqlite_engine([PracticeBillingProfileRow.__table__]) as eng:
        yield eng


@pytest.fixture
def clearinghouse() -> Iterator[FakeClearinghouse]:
    client = FakeClearinghouse()
    enrollment.register_clearinghouse_client_factory(lambda _practice_id: client)
    yield client
    enrollment.register_clearinghouse_client_factory(None)


@pytest.fixture
def harness(
    engine: Engine, clearinghouse: FakeClearinghouse, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, Any]]:
    session = Session(engine)
    app = FastAPI()
    app.include_router(practice_billing_routes.router)
    app.dependency_overrides[require_active_subscription] = _user
    app.dependency_overrides[get_tenant_context] = _tenant
    # The handlers read the request-scoped session directly rather than
    # through ``Depends``, so the swap is on the module, not the app.
    monkeypatch.setattr(practice_billing_routes, "get_db_session", lambda: session)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield {"client": client, "session": session, "clearinghouse": clearinghouse}
    finally:
        session.close()


_URL = "/api/practice/billing-profile"


class TestTaxIdNeverLeavesTheServer:
    def test_save_answers_with_last4_only(self, harness: dict[str, Any]) -> None:
        response = harness["client"].patch(
            _URL, json={"tax_id": "84-4459714", "tax_id_type": "ein"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["tax_id_last4"] == "9714"
        assert "tax_id" not in body
        assert "tax_id_encrypted" not in body
        assert "4459714" not in response.text

    def test_reading_back_carries_last4_only(self, harness: dict[str, Any]) -> None:
        harness["client"].patch(_URL, json={"tax_id": "84-4459714"})

        response = harness["client"].get(_URL)

        assert response.status_code == 200
        assert response.json()["tax_id_last4"] == "9714"
        assert "4459714" not in response.text

    def test_an_unconfigured_practice_reads_as_empty(self, harness: dict[str, Any]) -> None:
        body = harness["client"].get(_URL).json()

        assert body["legal_name"] is None
        assert body["tax_id_last4"] is None
        assert body["clearinghouse_provider_id"] is None


class TestCompletingTheProfileRegistersTheProvider:
    def test_a_complete_save_creates_the_record_once(self, harness: dict[str, Any]) -> None:
        response = harness["client"].patch(_URL, json=_COMPLETE)

        assert response.status_code == 200
        assert response.json()["clearinghouse_provider_id"] == PROVIDER_ID
        registrations = harness["clearinghouse"].calls_named("create_provider")
        assert len(registrations) == 1
        assert registrations[0].taxId == "844459714"
        assert registrations[0].contacts[0].email == "billing@example.com"

        harness["client"].patch(_URL, json={"phone": "4045550199"})

        assert len(harness["clearinghouse"].calls_named("create_provider")) == 1

    def test_an_incomplete_save_registers_nothing_and_still_saves(
        self, harness: dict[str, Any]
    ) -> None:
        partial = {k: v for k, v in _COMPLETE.items() if k != "contact_email"}

        response = harness["client"].patch(_URL, json=partial)

        assert response.status_code == 200
        assert response.json()["clearinghouse_provider_id"] is None
        assert response.json()["legal_name"] == _COMPLETE["legal_name"]
        assert harness["clearinghouse"].calls_named("create_provider") == []

    def test_the_contact_email_completes_it_later(self, harness: dict[str, Any]) -> None:
        partial = {k: v for k, v in _COMPLETE.items() if k != "contact_email"}
        harness["client"].patch(_URL, json=partial)

        response = harness["client"].patch(_URL, json={"contact_email": "billing@example.com"})

        assert response.json()["clearinghouse_provider_id"] == PROVIDER_ID
