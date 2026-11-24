# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Who may change the client portal and the scheduling policy.

* turning the portal on or off and choosing its parts, its welcome and its
  invitation wording, and the practice's scheduling policy are for the
  practice owner or an admin the owner named;
* any other clinician gets 403 ``NOT_PRACTICE_ADMIN``, and nothing is written;
* every clinician can still read all four.

The owner-or-admin check runs for real against a practice row stood in for
the session, as in ``test_practice_billing_routes.py``. What each save does is
covered in the route's own tests, which sign in as the owner.
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
from app.portal import invite_template_routes, settings_routes, welcome_routes
from app.portal.delivery import CapturingRenderedInviteDelivery
from app.portal.factory import get_invite_delivery
from app.portal.invite_email import DEFAULT_TEMPLATE, InviteTemplate
from app.portal.invite_template_store import (
    InMemoryInviteTemplateStore,
    get_invite_template_store,
)
from app.portal.portal_settings import InMemoryPortalSettingsStore, get_portal_settings_store
from app.portal.practice_routes import PracticeAddress
from app.portal.welcome import DEFAULT_WELCOME, PortalWelcome
from app.portal.welcome_store import InMemoryPortalWelcomeStore, get_portal_welcome_store
from app.routes import _deps
from app.routes import scheduling as scheduling_routes
from app.scheduling_engine.services.scheduling_policy import DEFAULTS
from app.services.audit_service import get_audit_service
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.user_fakes import clinician

if TYPE_CHECKING:
    from app.repositories.practice_admin import InMemoryPracticeAdminRepository

    from tests.audit_fakes import RecordingAudit

PRACTICE_ID = "practice-1"
TENANT = "practice_abc123"
OWNER_ID = "11111111-1111-1111-1111-111111111111"
COLLEAGUE_ID = "22222222-2222-2222-2222-222222222222"


OWNER = clinician(OWNER_ID, "owner@example.com")
COLLEAGUE = clinician(COLLEAGUE_ID, "colleague@example.com")

#: What the practice had before anyone signed in, so a refused reset shows.
SAVED_WELCOME = PortalWelcome(heading="Hello", body="Welcome to {practice_name}.")
SAVED_TEMPLATE = InviteTemplate(subject="Our portal", body=DEFAULT_TEMPLATE.body)

WRITES = [
    ("PUT", "/api/portal/settings", {"enabled": False}),
    ("PUT", "/api/portal/welcome", {"heading": "Hi", "body": DEFAULT_WELCOME.body}),
    ("DELETE", "/api/portal/welcome", None),
    ("PUT", "/api/portal/invite-template", {"subject": "Hi", "body": DEFAULT_TEMPLATE.body}),
    ("DELETE", "/api/portal/invite-template", None),
    ("PATCH", "/api/scheduling/policy", {"min_notice_hours": 48}),
]
READS = [
    "/api/portal/settings",
    "/api/portal/welcome",
    "/api/portal/invite-template",
    "/api/scheduling/policy",
]


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, recording_audit: RecordingAudit) -> dict[str, Any]:
    acting = {"user": OWNER}
    portal = InMemoryPortalSettingsStore()
    welcomes = InMemoryPortalWelcomeStore()
    welcomes.save(PRACTICE_ID, SAVED_WELCOME)
    templates = InMemoryInviteTemplateStore()
    templates.save(PRACTICE_ID, SAVED_TEMPLATE)
    policy_writes: list[dict[str, Any]] = []

    application = FastAPI()
    register_exception_handlers(application)
    for router in (
        settings_routes.router,
        welcome_routes.router,
        invite_template_routes.router,
        scheduling_routes.router,
    ):
        application.include_router(router)
    overrides = application.dependency_overrides
    overrides[get_current_user] = lambda: acting["user"]
    overrides[require_active_subscription] = lambda: acting["user"]
    overrides[get_tenant_context] = lambda: TenantContext(
        user_id=acting["user"].id, practice_id=PRACTICE_ID, practice_schema=TENANT
    )
    overrides[get_portal_settings_store] = lambda: portal
    overrides[get_portal_welcome_store] = lambda: welcomes
    overrides[get_invite_template_store] = lambda: templates
    overrides[get_invite_delivery] = CapturingRenderedInviteDelivery
    overrides[get_audit_service] = lambda: recording_audit

    practice = SimpleNamespace(id=PRACTICE_ID, owner_email=OWNER.email, schema_name=TENANT)
    platform_session = MagicMock()
    platform_session.get.return_value = practice
    monkeypatch.setattr("app.auth.service._resolve_practice", lambda *_a: (PRACTICE_ID, TENANT))
    monkeypatch.setattr("app.db.get_db_session", lambda: platform_session)
    monkeypatch.setattr(_deps, "_resolve_practice", lambda *_a: (PRACTICE_ID, TENANT))
    address = PracticeAddress(slug="example", display_name="Example Therapy", enabled=True)
    for module in (settings_routes, welcome_routes):
        monkeypatch.setattr(module, "ensure_practice_slug", lambda _practice_id: address)
    monkeypatch.setattr(settings_routes, "mounted_modules_on", lambda _app: frozenset())

    def _update_policy(_session: Any, patch: dict[str, Any]) -> dict[str, Any]:
        policy_writes.append(patch)
        return {**DEFAULTS, **patch}

    monkeypatch.setattr(scheduling_routes, "get_db_session", MagicMock)
    monkeypatch.setattr(scheduling_routes, "load_policy", lambda _session: dict(DEFAULTS))
    monkeypatch.setattr(scheduling_routes, "update_policy", _update_policy)
    return {
        "client": TestClient(application),
        "acting": acting,
        "portal": portal,
        "welcomes": welcomes,
        "templates": templates,
        "policy_writes": policy_writes,
    }


def _send(harness: dict[str, Any], method: str, path: str, body: Any) -> Any:
    return harness["client"].request(method, path, json=body)


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
def test_a_colleague_who_is_not_an_admin_is_refused_and_nothing_is_written(
    harness: dict[str, Any], method: str, path: str, body: Any
) -> None:
    harness["acting"]["user"] = COLLEAGUE

    resp = _send(harness, method, path, body)

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "NOT_PRACTICE_ADMIN"
    assert harness["portal"].get(PRACTICE_ID).decided_at is None
    assert harness["welcomes"].get(PRACTICE_ID) == SAVED_WELCOME
    assert harness["templates"].get(PRACTICE_ID) == SAVED_TEMPLATE
    assert harness["policy_writes"] == []


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
def test_the_owner_may(harness: dict[str, Any], method: str, path: str, body: Any) -> None:
    resp = _send(harness, method, path, body)
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
def test_an_admin_the_owner_named_may(
    harness: dict[str, Any],
    practice_admins: InMemoryPracticeAdminRepository,
    method: str,
    path: str,
    body: Any,
) -> None:
    practice_admins.grant(COLLEAGUE_ID, OWNER_ID, datetime(2026, 1, 2, tzinfo=UTC))
    harness["acting"]["user"] = COLLEAGUE

    resp = _send(harness, method, path, body)

    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("path", READS)
def test_a_colleague_can_still_read_it(harness: dict[str, Any], path: str) -> None:
    harness["acting"]["user"] = COLLEAGUE
    resp = harness["client"].get(path)
    assert resp.status_code == 200, resp.text
