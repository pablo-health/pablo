# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Connecting Google Calendar gives a clinician with no video default Meet."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from app.main import app
from app.models import UserPreferences
from app.repositories import InMemoryUserRepository
from app.routes import telehealth
from app.routes.scheduling import get_google_calendar_service
from app.services.video_default import default_to_meet_on_calendar_connect
from app.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.testclient import TestClient

USER = "test-user-123"
_REDIRECT = "http://localhost:3000/dashboard/settings/calendar"
_CONNECT = {"code": "auth-code", "redirect_uri": _REDIRECT, "state": "signed-state"}


def test_a_new_account_has_no_video_default() -> None:
    assert UserPreferences().default_video_platform is None


def test_meet_becomes_the_default_when_none_is_stored() -> None:
    repo = InMemoryUserRepository()

    changed = default_to_meet_on_calendar_connect(
        repo, USER, enabled_providers=("manual", "google_meet")
    )

    assert changed is True
    assert repo.get_preferences(USER).default_video_platform == "google_meet"


@pytest.mark.parametrize("stored", ["zoom", "doxy_me", "google_meet"])
def test_a_stored_default_is_never_changed(stored: str) -> None:
    repo = InMemoryUserRepository()
    repo.update_preferences(USER, {"default_video_platform": stored})

    changed = default_to_meet_on_calendar_connect(
        repo, USER, enabled_providers=("google_meet", "zoom")
    )

    assert changed is False
    assert repo.get_preferences(USER).default_video_platform == stored


def test_nothing_is_set_where_the_deployment_does_not_offer_meet() -> None:
    repo = InMemoryUserRepository()

    changed = default_to_meet_on_calendar_connect(repo, USER, enabled_providers=("manual",))

    assert changed is False
    assert repo.get_preferences(USER).default_video_platform is None


def test_other_preferences_survive_the_default_being_set() -> None:
    repo = InMemoryUserRepository()
    repo.update_preferences(USER, {"default_duration_minutes": 75})

    default_to_meet_on_calendar_connect(repo, USER, enabled_providers=("google_meet",))

    prefs = repo.get_preferences(USER)
    assert prefs.default_duration_minutes == 75
    assert prefs.default_video_platform == "google_meet"


@pytest.fixture
def gcal() -> Iterator[MagicMock]:
    service = MagicMock()
    app.dependency_overrides[get_google_calendar_service] = lambda: service
    yield service
    app.dependency_overrides.pop(get_google_calendar_service, None)


@pytest.fixture
def meet_offered() -> Iterator[None]:
    settings = get_settings().model_copy(update={"telehealth_providers_enabled": "google_meet"})
    with patch("app.routes.scheduling.get_settings", return_value=settings):
        yield


@pytest.mark.usefixtures("gcal", "meet_offered")
def test_a_calendar_connect_makes_meet_the_default(
    client: TestClient, mock_user_repo: InMemoryUserRepository
) -> None:
    response = client.get("/api/google-calendar/callback", params=_CONNECT)

    assert response.status_code == 200, response.text
    assert mock_user_repo.get_preferences(USER).default_video_platform == "google_meet"


@pytest.mark.usefixtures("gcal", "meet_offered")
def test_a_calendar_connect_leaves_a_stored_zoom_alone(
    client: TestClient, mock_user_repo: InMemoryUserRepository
) -> None:
    mock_user_repo.update_preferences(USER, {"default_video_platform": "zoom"})

    response = client.get("/api/google-calendar/callback", params=_CONNECT)

    assert response.status_code == 200, response.text
    assert mock_user_repo.get_preferences(USER).default_video_platform == "zoom"


@pytest.mark.usefixtures("meet_offered")
def test_an_incremental_grant_does_not_set_a_default(
    client: TestClient, gcal: MagicMock, mock_user_repo: InMemoryUserRepository
) -> None:
    """Granting import on top of a connection is not a connect."""
    gcal.get_sync_status.return_value = {"write_target": "primary"}

    response = client.get(
        "/api/google-calendar/callback", params={**_CONNECT, "capability": "import"}
    )

    assert response.status_code == 200, response.text
    assert mock_user_repo.get_preferences(USER).default_video_platform is None


@pytest.mark.usefixtures("meet_offered")
def test_a_failed_connect_sets_nothing(
    client: TestClient, gcal: MagicMock, mock_user_repo: InMemoryUserRepository
) -> None:
    gcal.handle_callback.side_effect = RuntimeError("google said no")

    response = client.get("/api/google-calendar/callback", params=_CONNECT)

    assert response.status_code == 400
    assert mock_user_repo.get_preferences(USER).default_video_platform is None


@pytest.mark.usefixtures("gcal", "meet_offered")
def test_the_connect_still_succeeds_when_the_default_cannot_be_saved(
    client: TestClient, mock_user_repo: InMemoryUserRepository
) -> None:
    with patch.object(mock_user_repo, "update_preferences", side_effect=RuntimeError("db down")):
        response = client.get("/api/google-calendar/callback", params=_CONNECT)

    assert response.status_code == 200, response.text


@pytest.fixture
def providers_client(client: TestClient) -> Iterator[TestClient]:
    """The providers route with Meet offered and the calendar connected."""
    settings = get_settings().model_copy(update={"telehealth_providers_enabled": "google_meet"})
    tokens = MagicMock()
    tokens.exists.return_value = True
    zoom_store = MagicMock()
    app.dependency_overrides[telehealth.get_zoom_store] = lambda: zoom_store
    with (
        patch.object(telehealth, "get_settings", return_value=settings),
        patch.object(telehealth, "_gcal_token_repo_factory", return_value=tokens),
    ):
        yield client
    app.dependency_overrides.pop(telehealth.get_zoom_store, None)


def test_providers_report_a_stored_default_that_is_not_offered(
    providers_client: TestClient, mock_user_repo: InMemoryUserRepository
) -> None:
    """A stored zoom with no Zoom connected: no usable default, but the screen
    can still tell it from a clinician who never chose."""
    mock_user_repo.update_preferences(USER, {"default_video_platform": "zoom"})

    response = providers_client.get("/api/telehealth/providers")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["default_provider"] is None
    assert body["stored_default"] == "zoom"


def test_providers_report_meet_once_it_is_the_default(
    providers_client: TestClient, mock_user_repo: InMemoryUserRepository
) -> None:
    mock_user_repo.update_preferences(USER, {"default_video_platform": "google_meet"})

    response = providers_client.get("/api/telehealth/providers")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["default_provider"] == "google_meet"
    assert body["stored_default"] == "google_meet"


def test_providers_report_nothing_stored_for_a_new_account(providers_client: TestClient) -> None:
    response = providers_client.get("/api/telehealth/providers")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["default_provider"] is None
    assert body["stored_default"] is None
