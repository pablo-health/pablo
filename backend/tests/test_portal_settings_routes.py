# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether a practice offers the portal: the routes a clinician sets it with.

Mounts the real router on a fresh app with auth, the store and the audit
service overridden, like ``test_portal_welcome.py``. The platform store and
the migration that gave existing practices the portal are proven against
Postgres in ``tests_integration/database/test_portal_settings_store.py``;
what the switch does to sign-in and signed-in clients is covered in
``test_portal_auth_routes.py`` and ``test_portal_resolver.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import get_current_user, require_active_subscription
from app.models.audit import AuditAction, ResourceType
from app.portal import settings_routes
from app.portal.portal_settings import InMemoryPortalSettingsStore, get_portal_settings_store
from app.portal.practice_routes import PracticeAddress
from app.services.audit_service import get_audit_service
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from app.models import User

PRACTICE_ID = "practice-1"
TENANT = "practice_abc123"
URL = "/api/portal/settings"


class _RecordingAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def log(self, action: Any, *_args: Any, **kwargs: Any) -> None:
        self.entries.append({"action": action, **kwargs})


@pytest.fixture
def minted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Which practices had an address minted, without a platform table."""
    calls: list[str] = []

    def _mint(practice_id: str) -> PracticeAddress:
        calls.append(practice_id)
        return PracticeAddress(slug="example-therapy", display_name="Example", enabled=True)

    monkeypatch.setattr(settings_routes, "ensure_practice_slug", _mint)
    monkeypatch.setattr(
        settings_routes, "_resolve_practice_from_email", lambda _email: (PRACTICE_ID, TENANT)
    )
    return calls


@pytest.fixture
def store() -> InMemoryPortalSettingsStore:
    return InMemoryPortalSettingsStore()


@pytest.fixture
def audit() -> _RecordingAudit:
    return _RecordingAudit()


@pytest.fixture
def client(
    mock_user: User,
    store: InMemoryPortalSettingsStore,
    audit: _RecordingAudit,
    minted: list[str],  # patches the practice lookups
) -> TestClient:
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(settings_routes.router)
    overrides = application.dependency_overrides
    overrides[get_current_user] = lambda: mock_user
    overrides[require_active_subscription] = lambda: mock_user
    overrides[get_portal_settings_store] = lambda: store
    overrides[get_audit_service] = lambda: audit
    return TestClient(application)


def test_a_practice_that_was_never_asked_does_not_offer_the_portal(client: TestClient) -> None:
    assert client.get(URL).json() == {"enabled": False, "decided": False}


def test_turning_it_on_mints_the_address_and_records_the_decision(
    client: TestClient, minted: list[str], store: InMemoryPortalSettingsStore
) -> None:
    response = client.put(URL, json={"enabled": True})

    assert response.status_code == 200
    assert response.json() == {"enabled": True, "decided": True}
    assert minted == [PRACTICE_ID]
    assert store.get(PRACTICE_ID).enabled is True
    assert client.get(URL).json() == {"enabled": True, "decided": True}


def test_turning_it_off_does_not_mint_anything(client: TestClient, minted: list[str]) -> None:
    """Saying no is an answer too: it is recorded, and needs no address."""
    response = client.put(URL, json={"enabled": False})

    assert response.json() == {"enabled": False, "decided": True}
    assert minted == []


def test_a_change_is_audited_with_the_old_and_new_setting_only(
    client: TestClient, audit: _RecordingAudit, mock_user: User
) -> None:
    client.put(URL, json={"enabled": True})
    client.put(URL, json={"enabled": False})

    assert [entry["action"] for entry in audit.entries] == [
        AuditAction.PRACTICE_PORTAL_OFFERING_CHANGED,
        AuditAction.PRACTICE_PORTAL_OFFERING_CHANGED,
    ]
    first, second = audit.entries
    assert first["resource_type"] == ResourceType.PRACTICE_PORTAL
    assert first["resource_id"] == PRACTICE_ID
    assert first["changes"] == {"enabled": {"old": False, "new": True}}
    assert second["changes"] == {"enabled": {"old": True, "new": False}}
    assert mock_user.email not in repr(audit.entries)


def test_saving_the_same_setting_again_writes_no_audit_entry(
    client: TestClient, audit: _RecordingAudit
) -> None:
    client.put(URL, json={"enabled": True})
    client.put(URL, json={"enabled": True})

    assert len(audit.entries) == 1


def test_the_practice_comes_from_the_caller_not_the_request(
    client: TestClient, store: InMemoryPortalSettingsStore
) -> None:
    """There is no field a clinician could use to reach another practice."""
    client.put(URL, json={"enabled": True, "practice_id": "someone-else"})

    assert store.get("someone-else").enabled is False
    assert store.get(PRACTICE_ID).enabled is True


def test_a_caller_with_no_practice_gets_a_conflict(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_routes, "_resolve_practice_from_email", lambda _email: None)

    assert client.get(URL).status_code == 409
    assert client.put(URL, json={"enabled": True}).status_code == 409
