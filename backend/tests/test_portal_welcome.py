# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""A practice's portal welcome: checking it, filling it in, and the routes.

What the client actually receives is the capability document's, and is
covered in ``test_portal_account_routes.py``; the platform store is proven
against Postgres in ``tests_integration/database/test_portal_welcome_store.py``.

Mounts the real router on a fresh app with auth and the store overridden, like
``test_portal_invite_template.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import get_current_user, require_active_subscription
from app.portal import welcome_routes
from app.portal.practice_routes import PracticeAddress
from app.portal.welcome import (
    DEFAULT_WELCOME,
    MAX_BODY_LENGTH,
    MAX_HEADING_LENGTH,
    PortalWelcome,
    render_welcome,
    welcome_problems,
)
from app.portal.welcome_store import InMemoryPortalWelcomeStore, get_portal_welcome_store
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from app.models import User

PRACTICE_ID = "practice-1"
TENANT = "practice_abc123"
PRACTICE_NAME = "Example Therapy"


# ── checking and filling in a welcome ───────────────────────────────────


def test_the_default_is_valid_and_names_the_practice() -> None:
    assert welcome_problems(DEFAULT_WELCOME) == []
    rendered = render_welcome(DEFAULT_WELCOME, PRACTICE_NAME)
    assert rendered.heading == "Welcome to Example Therapy"
    assert rendered.body == (
        "This is where you'll find what Example Therapy has asked you to do, and where "
        "you can reach them between visits. You can leave and come back any time using "
        "the link in your email."
    )


@pytest.mark.parametrize("name", [None, "", "   "])
def test_a_practice_without_a_name_is_your_practice(name: str | None) -> None:
    assert render_welcome(DEFAULT_WELCOME, name).heading == "Welcome to your practice"


def test_the_invitation_emails_double_braces_fill_in_too() -> None:
    welcome = PortalWelcome(heading="Hi from {{practice_name}}", body="{ practice_name }")
    assert welcome_problems(welcome) == []
    assert render_welcome(welcome, PRACTICE_NAME) == PortalWelcome(
        heading="Hi from Example Therapy", body="Example Therapy"
    )


def test_text_is_passed_through_as_typed() -> None:
    welcome = PortalWelcome(heading="Hello", body="Line one\n\n<b>x</b> & more")
    assert render_welcome(welcome, PRACTICE_NAME).body == "Line one\n\n<b>x</b> & more"


@pytest.mark.parametrize(
    ("welcome", "problem"),
    [
        (PortalWelcome(heading="Hi", body="Hello {client_name}"), "{client_name}"),
        (PortalWelcome(heading="{diagnosis}", body="Hello"), "{diagnosis}"),
        (PortalWelcome(heading="x" * (MAX_HEADING_LENGTH + 1), body="Hello"), "heading under"),
        (PortalWelcome(heading="Hi", body="x" * (MAX_BODY_LENGTH + 1)), "message under"),
        (PortalWelcome(heading="", body="Hello"), "Add a heading."),
        (PortalWelcome(heading="Hi", body="  "), "Add a message."),
        (PortalWelcome(heading="Two\nlines", body="Hello"), "one line"),
    ],
)
def test_a_welcome_that_cannot_be_saved_says_why(welcome: PortalWelcome, problem: str) -> None:
    assert any(problem in p for p in welcome_problems(welcome))


def test_the_limits_are_inclusive() -> None:
    welcome = PortalWelcome(heading="x" * MAX_HEADING_LENGTH, body="y" * MAX_BODY_LENGTH)
    assert welcome_problems(welcome) == []


# ── the routes ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _practice(monkeypatch: pytest.MonkeyPatch) -> None:
    address = PracticeAddress(slug="example-therapy", display_name=PRACTICE_NAME, enabled=True)
    monkeypatch.setattr(
        welcome_routes, "_resolve_practice_from_email", lambda _email: (PRACTICE_ID, TENANT)
    )
    monkeypatch.setattr(welcome_routes, "ensure_practice_slug", lambda _practice_id: address)


@pytest.fixture
def welcomes() -> InMemoryPortalWelcomeStore:
    return InMemoryPortalWelcomeStore()


@pytest.fixture
def client(mock_user: User, welcomes: InMemoryPortalWelcomeStore) -> TestClient:
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(welcome_routes.router)
    overrides = application.dependency_overrides
    overrides[get_current_user] = lambda: mock_user
    overrides[require_active_subscription] = lambda: mock_user
    overrides[get_portal_welcome_store] = lambda: welcomes
    return TestClient(application)


def test_a_practice_starts_on_the_default(client: TestClient) -> None:
    body = client.get("/api/portal/welcome").json()
    assert body["is_default"] is True
    assert body["heading"] == DEFAULT_WELCOME.heading
    assert body["body"] == DEFAULT_WELCOME.body
    assert body["practice_name"] == PRACTICE_NAME
    assert [p["name"] for p in body["placeholders"]] == ["practice_name"]


def test_a_saved_welcome_is_read_back_and_can_be_reset(
    client: TestClient, welcomes: InMemoryPortalWelcomeStore
) -> None:
    saved = client.put(
        "/api/portal/welcome",
        json={"heading": "Hello from {practice_name}", "body": "Line one\n<b>x</b>"},
    )
    assert saved.status_code == 200
    assert saved.json()["is_default"] is False
    body = client.get("/api/portal/welcome").json()
    assert body["heading"] == "Hello from {practice_name}"
    assert body["body"] == "Line one\n<b>x</b>"
    assert welcomes.welcomes[PRACTICE_ID].heading == "Hello from {practice_name}"

    reset = client.delete("/api/portal/welcome").json()
    assert reset["is_default"] is True
    assert reset["heading"] == DEFAULT_WELCOME.heading
    assert welcomes.welcomes == {}


def test_an_unknown_placeholder_is_refused_by_name(
    client: TestClient, welcomes: InMemoryPortalWelcomeStore
) -> None:
    response = client.put(
        "/api/portal/welcome", json={"heading": "Hi", "body": "Hello {client_name}"}
    )
    assert response.status_code == 422
    assert any("{client_name}" in p for p in response.json()["error"]["details"]["problems"])
    assert welcomes.welcomes == {}


@pytest.mark.parametrize(
    "draft",
    [
        {"heading": "x" * (MAX_HEADING_LENGTH + 1), "body": "Hello"},
        {"heading": "Hi", "body": "x" * (MAX_BODY_LENGTH + 1)},
    ],
)
def test_text_over_the_limit_is_refused(
    client: TestClient, welcomes: InMemoryPortalWelcomeStore, draft: dict[str, str]
) -> None:
    assert client.put("/api/portal/welcome", json=draft).status_code == 422
    assert welcomes.welcomes == {}
