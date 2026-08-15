# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Behavior of the subscription gate across access levels.

These tests build their own miniature app rather than using the
``client`` fixture: ``conftest.py`` overrides
``require_active_subscription`` app-wide (every other test wants the
gate out of the way), so the shared app cannot exercise the gate at
all. A mini app also lets the read/write matrix be exhaustive and
fast — five methods against one path, plus one route from each side
of the intent-override table.

The subscription record itself comes from ``_fetch_subscription`` in a
routes module that a deployment supplies; there is no such module in
this tree, and the gate imports it lazily precisely so there does not
have to be. The fixture installs a stub under that import name, which
is also what pins the call signature: if the gate ever starts calling
``_fetch_subscription`` differently, these tests fail rather than
silently drifting from what a deployment implements.

The compatibility cases matter as much as the new ones. A record with
no ``access_level`` key must behave exactly as it did before access
levels existed — active allows, anything else is
``SUBSCRIPTION_INACTIVE`` — and that is asserted here directly, not
inferred.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

import pytest
from app.auth import service as auth_service
from app.auth.route_access import (
    _INTENT_OVERRIDES,
    AccessIntent,
    AccessLevel,
    access_intent,
    derive_access_level,
    register_intent_override,
    resolve_access_level,
)
from app.auth.service import get_current_user, require_active_subscription
from app.models import User
from app.settings import get_settings
from app.utcnow import utc_now
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.settings import Settings

# The gate imports ``_fetch_subscription`` relative to its own package,
# so derive the name the same way instead of hard-coding a namespace
# that differs between how this tree is imported and how a deployment
# packages it.
SUBSCRIPTION_MODULE = f"{auth_service.__package__.rsplit('.', 1)[0]}.routes.subscription"

WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
ALL_METHODS = ["GET", *WRITE_METHODS]


class _SubscriptionStub:
    """Stand-in for a deployment's ``_fetch_subscription``."""

    def __init__(self) -> None:
        self.row: dict[str, Any] | None = None
        self.calls: list[tuple[str, Settings]] = []

    def fetch(self, email: str, settings: Settings) -> dict[str, Any] | None:
        self.calls.append((email, settings))
        return self.row


@pytest.fixture
def subscription(monkeypatch: pytest.MonkeyPatch) -> _SubscriptionStub:
    """Install a subscription-routes stub under the gate's import name."""
    stub = _SubscriptionStub()
    module = ModuleType(SUBSCRIPTION_MODULE)
    module._fetch_subscription = stub.fetch  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, SUBSCRIPTION_MODULE, module)
    return stub


@pytest.fixture
def enforcing_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Point the gate at a deployment that enforces subscriptions."""
    settings = get_settings().model_copy(update={"pablo_edition": "solo"})
    assert settings.is_saas
    monkeypatch.setattr(auth_service, "get_settings", lambda: settings)
    return settings


@pytest.fixture
def gate_user() -> User:
    return User(
        id="user-1",
        email="clinician@example.com",
        name="Test Clinician",
        created_at=utc_now(),
    )


@pytest.fixture
def client(gate_user: User) -> Iterator[TestClient]:
    """A miniature app whose every route sits behind the gate.

    ``/api/things`` carries all five methods so intent falls out of the
    HTTP method alone. The other two paths are real entries in the
    override table, one in each direction, so the overrides are proven
    against the gate rather than only against ``access_intent``.
    """
    app = FastAPI()

    def ok(_user: User = Depends(require_active_subscription)) -> dict[str, str]:
        return {"result": "ok"}

    for method in ALL_METHODS:
        app.add_api_route("/api/things", ok, methods=[method])
        app.add_api_route("/api/things/{thing_id}", ok, methods=[method])

    app.add_api_route("/api/chat/conversations/preview", ok, methods=["POST"])
    app.add_api_route("/api/google-calendar/authorize", ok, methods=["GET"])

    app.dependency_overrides[get_current_user] = lambda: gate_user
    with TestClient(app) as test_client:
        yield test_client


def _error_code(response: Any) -> str | None:
    body = response.json()
    detail = body.get("detail", body)
    if isinstance(detail, dict):
        code = detail.get("error", {}).get("code")
        return code if isinstance(code, str) else None
    return None


# ---------------------------------------------------------------------------
# route_access unit behavior
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status_value", "expected"),
    [
        ("active", AccessLevel.FULL),
        ("trial", AccessLevel.FULL),
        ("canceled", AccessLevel.NONE),
        ("past_due", AccessLevel.NONE),
        ("", AccessLevel.NONE),
        (None, AccessLevel.NONE),
    ],
)
def test_derive_access_level(status_value: str | None, expected: AccessLevel) -> None:
    assert derive_access_level(status_value) is expected


@pytest.mark.parametrize(
    ("sub", "expected"),
    [
        ({"access_level": "full"}, AccessLevel.FULL),
        ({"access_level": "read_only"}, AccessLevel.READ_ONLY),
        ({"access_level": "none"}, AccessLevel.NONE),
        # An explicit level wins over the status it disagrees with.
        ({"access_level": "read_only", "effective_status": "active"}, AccessLevel.READ_ONLY),
        ({"access_level": "full", "effective_status": "canceled"}, AccessLevel.FULL),
        # Unrecognized values fail closed rather than falling back to
        # the status, which would re-open a deliberately narrowed record.
        ({"access_level": "unlimited", "effective_status": "active"}, AccessLevel.NONE),
        ({"access_level": None, "effective_status": "active"}, AccessLevel.NONE),
        ({"access_level": 1, "effective_status": "active"}, AccessLevel.NONE),
        # No key at all: the pre-access-level behavior, unchanged.
        ({"effective_status": "active"}, AccessLevel.FULL),
        ({"effective_status": "canceled", "status": "active"}, AccessLevel.NONE),
        ({"status": "trial"}, AccessLevel.FULL),
        ({}, AccessLevel.NONE),
    ],
)
def test_resolve_access_level(sub: dict[str, Any], expected: AccessLevel) -> None:
    assert resolve_access_level(sub) is expected


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/api/things", AccessIntent.READ),
        ("HEAD", "/api/things", AccessIntent.READ),
        ("OPTIONS", "/api/things", AccessIntent.READ),
        ("POST", "/api/things", AccessIntent.WRITE),
        ("PUT", "/api/things", AccessIntent.WRITE),
        ("PATCH", "/api/things", AccessIntent.WRITE),
        ("DELETE", "/api/things", AccessIntent.WRITE),
        # Lowercase methods classify the same as uppercase ones.
        ("get", "/api/things", AccessIntent.READ),
        ("post", "/api/things", AccessIntent.WRITE),
        # Overrides, in both directions.
        ("POST", "/api/chat/conversations/preview", AccessIntent.READ),
        ("POST", "/api/availability/check", AccessIntent.READ),
        ("GET", "/api/google-calendar/authorize", AccessIntent.WRITE),
        ("GET", "/api/google-calendar/callback", AccessIntent.WRITE),
        # An override is keyed by method too — the other methods on an
        # overridden path keep the default classification.
        ("GET", "/api/chat/conversations/preview", AccessIntent.READ),
        ("POST", "/api/google-calendar/authorize", AccessIntent.WRITE),
    ],
)
def test_access_intent(method: str, path: str, expected: AccessIntent) -> None:
    assert access_intent(method, path) is expected


def test_register_intent_override() -> None:
    """A deployment can classify a route this module has never seen."""
    original = dict(_INTENT_OVERRIDES)
    try:
        assert access_intent("POST", "/api/extension/report") is AccessIntent.WRITE
        register_intent_override("post", "/api/extension/report", AccessIntent.READ)
        assert access_intent("POST", "/api/extension/report") is AccessIntent.READ
    finally:
        _INTENT_OVERRIDES.clear()
        _INTENT_OVERRIDES.update(original)

    assert access_intent("POST", "/api/extension/report") is AccessIntent.WRITE


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ALL_METHODS)
def test_no_enforcement_allows_every_method(
    client: TestClient,
    subscription: _SubscriptionStub,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    """A deployment that does not track subscriptions is untouched.

    Not even a lookup happens — the gate returns before importing the
    subscription module at all.
    """
    settings = get_settings().model_copy(update={"pablo_edition": "core"})
    assert not settings.is_saas
    monkeypatch.setattr(auth_service, "get_settings", lambda: settings)
    subscription.row = {"access_level": "none"}

    assert client.request(method, "/api/things").status_code == 200
    assert subscription.calls == []


@pytest.mark.parametrize("method", ALL_METHODS)
def test_missing_subscription_row_allows_every_method(
    client: TestClient,
    subscription: _SubscriptionStub,
    enforcing_settings: Settings,
    gate_user: User,
    method: str,
) -> None:
    """No record yet is mid-provisioning, not lapsed."""
    subscription.row = None

    assert client.request(method, "/api/things").status_code == 200
    assert subscription.calls[0] == (gate_user.email, enforcing_settings)


@pytest.mark.parametrize("method", ALL_METHODS)
def test_full_access_allows_every_method(
    client: TestClient,
    subscription: _SubscriptionStub,
    enforcing_settings: Settings,
    method: str,
) -> None:
    subscription.row = {"status": "canceled", "access_level": "full"}

    assert client.request(method, "/api/things").status_code == 200


def test_read_only_allows_reads(
    client: TestClient,
    subscription: _SubscriptionStub,
    enforcing_settings: Settings,
) -> None:
    subscription.row = {"status": "canceled", "access_level": "read_only"}

    assert client.get("/api/things").status_code == 200
    # Path parameters must not defeat the classification: the gate
    # matches on the route's template, not the resolved URL.
    assert client.get("/api/things/abc-123").status_code == 200


@pytest.mark.parametrize("method", list(WRITE_METHODS))
def test_read_only_refuses_writes(
    client: TestClient,
    subscription: _SubscriptionStub,
    enforcing_settings: Settings,
    method: str,
) -> None:
    subscription.row = {
        "status": "canceled",
        "access_level": "read_only",
        "grace_extension_available": True,
    }

    response = client.request(method, "/api/things")

    assert response.status_code == 403
    error = response.json()["detail"]["error"]
    assert error["code"] == "SUBSCRIPTION_READONLY"
    assert "view and export" in error["message"]
    assert error["details"] == {
        "status": "canceled",
        "access_level": "read_only",
        "grace_extension_available": True,
    }


def test_read_only_write_details_default_grace_to_false(
    client: TestClient,
    subscription: _SubscriptionStub,
    enforcing_settings: Settings,
) -> None:
    """A record that says nothing about grace does not imply one."""
    subscription.row = {"status": "canceled", "access_level": "read_only"}

    details = client.post("/api/things").json()["detail"]["error"]["details"]
    assert details["grace_extension_available"] is False


def test_read_only_honors_intent_overrides(
    client: TestClient,
    subscription: _SubscriptionStub,
    enforcing_settings: Settings,
) -> None:
    """Overrides decide in both directions, not just the lenient one."""
    subscription.row = {"status": "canceled", "access_level": "read_only"}

    # A POST that persists nothing stays available.
    assert client.post("/api/chat/conversations/preview").status_code == 200

    # A GET that persists tokens does not.
    response = client.get("/api/google-calendar/authorize")
    assert response.status_code == 403
    assert _error_code(response) == "SUBSCRIPTION_READONLY"


@pytest.mark.parametrize("method", ALL_METHODS)
@pytest.mark.parametrize("access_level", ["none", "unrecognized", None])
def test_no_access_refuses_every_method(
    client: TestClient,
    subscription: _SubscriptionStub,
    enforcing_settings: Settings,
    access_level: str | None,
    method: str,
) -> None:
    """No access — and anything unrecognized — blocks reads too.

    An access level this build does not implement must not be read as
    permission, so it lands here rather than in the read-only branch.
    """
    subscription.row = {"status": "canceled", "access_level": access_level}

    response = client.request(method, "/api/things")

    assert response.status_code == 403
    error = response.json()["detail"]["error"]
    assert error["code"] == "SUBSCRIPTION_INACTIVE"
    assert error["message"] == "Your subscription is not active"
    assert error["details"] == {"status": "canceled", "grace_extension_available": False}


@pytest.mark.parametrize("method", ALL_METHODS)
@pytest.mark.parametrize("effective_status", ["active", "trial"])
def test_record_without_access_level_allows_when_active(
    client: TestClient,
    subscription: _SubscriptionStub,
    enforcing_settings: Settings,
    effective_status: str,
    method: str,
) -> None:
    """Compatibility: a record predating access levels behaves as before."""
    subscription.row = {"status": "active", "effective_status": effective_status}

    assert client.request(method, "/api/things").status_code == 200


@pytest.mark.parametrize("method", ALL_METHODS)
def test_record_without_access_level_blocks_when_lapsed(
    client: TestClient,
    subscription: _SubscriptionStub,
    enforcing_settings: Settings,
    method: str,
) -> None:
    """Compatibility: reads are refused too, exactly as before.

    Read-only access has to be granted; a lapsed record does not
    acquire it by upgrading the code.
    """
    subscription.row = {
        "status": "canceled",
        "effective_status": "canceled",
        "grace_extension_available": True,
    }

    response = client.request(method, "/api/things")

    assert response.status_code == 403
    error = response.json()["detail"]["error"]
    assert error["code"] == "SUBSCRIPTION_INACTIVE"
    assert error["details"] == {
        "status": "canceled",
        "grace_extension_available": True,
    }
