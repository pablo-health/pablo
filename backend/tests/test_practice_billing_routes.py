# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Who may change the practice's billing identity.

* the practice owner, or an admin the owner named, saves it as before;
* any other clinician gets 403 ``NOT_PRACTICE_ADMIN``, and nothing is saved
  and no provider record is registered with the clearinghouse;
* every clinician can still read it;
* a practice with no owner on record lets any of its clinicians save.

The owner-or-admin check runs for real against a practice row stood in for
the session, as in ``test_software_update_routes.py``. What a save writes is
proven against Postgres in ``tests_integration/api/test_practice_billing_routes.py``;
here the profile service is a recorder, so a refused save shows as no call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import (
    TenantContext,
    get_current_user,
    get_tenant_context,
    require_active_subscription,
)
from app.routes import practice_billing as practice_billing_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.user_fakes import clinician

if TYPE_CHECKING:
    from app.repositories.practice_admin import InMemoryPracticeAdminRepository

URL = "/api/practice/billing-profile"
OWNER_ID = "11111111-1111-1111-1111-111111111111"
COLLEAGUE_ID = "22222222-2222-2222-2222-222222222222"


OWNER = clinician(OWNER_ID, "owner@example.com")
COLLEAGUE = clinician(COLLEAGUE_ID, "colleague@example.com")


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    acting = {"user": OWNER}
    saved: list[dict[str, Any]] = []
    synced: list[str] = []

    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(practice_billing_routes.router)
    application.dependency_overrides[get_current_user] = lambda: acting["user"]
    application.dependency_overrides[require_active_subscription] = lambda: acting["user"]
    application.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id=acting["user"].id, practice_id="practice-1", practice_schema="practice_1"
    )

    practice = SimpleNamespace(
        id="practice-1", owner_email="owner@example.com", schema_name="practice_1"
    )
    session = MagicMock()
    session.get.return_value = practice
    monkeypatch.setattr(
        "app.auth.service._resolve_practice", lambda *_a: ("practice-1", "practice_1")
    )
    monkeypatch.setattr("app.db.get_db_session", lambda: session)
    monkeypatch.setattr(practice_billing_routes, "get_db_session", lambda: session)

    def _update(_session: Any, changes: dict[str, Any]) -> dict[str, Any]:
        saved.append(changes)
        return {"legal_name": changes.get("legal_name")}

    def _sync(_session: Any, practice_id: str) -> None:
        synced.append(practice_id)

    monkeypatch.setattr(practice_billing_routes, "update_billing_profile", _update)
    monkeypatch.setattr(practice_billing_routes, "sync_provider_record", _sync)
    monkeypatch.setattr(
        practice_billing_routes, "load_billing_profile", lambda _s: {"legal_name": "Kept"}
    )
    return {
        "client": TestClient(application),
        "acting": acting,
        "practice": practice,
        "saved": saved,
        "synced": synced,
    }


def test_the_owner_saves_it(harness: dict[str, Any]) -> None:
    resp = harness["client"].patch(URL, json={"legal_name": "New Name"})
    assert resp.status_code == 200
    assert resp.json()["legal_name"] == "New Name"
    assert harness["saved"] == [{"legal_name": "New Name"}]
    assert harness["synced"] == ["practice-1"]


def test_a_colleague_who_is_not_an_admin_is_refused(harness: dict[str, Any]) -> None:
    harness["acting"]["user"] = COLLEAGUE
    resp = harness["client"].patch(URL, json={"legal_name": "New Name", "tax_id": "84-4459714"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "NOT_PRACTICE_ADMIN"
    assert harness["saved"] == []
    assert harness["synced"] == []


def test_a_colleague_can_still_read_it(harness: dict[str, Any]) -> None:
    harness["acting"]["user"] = COLLEAGUE
    resp = harness["client"].get(URL)
    assert resp.status_code == 200
    assert resp.json()["legal_name"] == "Kept"


def test_an_admin_the_owner_named_saves_it(
    harness: dict[str, Any], practice_admins: InMemoryPracticeAdminRepository
) -> None:
    practice_admins.grant(COLLEAGUE_ID, OWNER_ID, datetime(2026, 1, 2, tzinfo=UTC))
    harness["acting"]["user"] = COLLEAGUE
    resp = harness["client"].patch(URL, json={"legal_name": "New Name"})
    assert resp.status_code == 200
    assert harness["saved"] == [{"legal_name": "New Name"}]


def test_a_practice_with_no_owner_on_record_lets_a_colleague_save(
    harness: dict[str, Any],
) -> None:
    harness["practice"].owner_email = ""
    harness["acting"]["user"] = COLLEAGUE
    resp = harness["client"].patch(URL, json={"legal_name": "New Name"})
    assert resp.status_code == 200
    assert harness["saved"] == [{"legal_name": "New Name"}]
