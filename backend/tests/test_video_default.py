# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Connecting Google Calendar gives a clinician with no video default Meet."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from app.main import app
from app.meeting_providers.doxy import DoxyMeProvider
from app.meeting_providers.manual import ManualProvider
from app.meeting_providers.meet import GoogleMeetProvider
from app.models import UserPreferences
from app.repositories import InMemoryUserRepository
from app.routes.scheduling import get_google_calendar_service, get_meeting_registry, get_savepoint
from app.services.telehealth import Clinician, MeetingProviderRegistry
from app.services.video_default import default_to_meet_on_calendar_connect

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.testclient import TestClient

USER = "test-user-123"
_REDIRECT = "http://localhost:3000/dashboard/settings/calendar"
_CONNECT = {"code": "auth-code", "redirect_uri": _REDIRECT, "state": "signed-state"}


class FakeZoom:
    """Zoom, connected or not, without a grant store behind it."""

    provider_id = "zoom"

    def __init__(self, *, connected: bool) -> None:
        self._connected = connected

    def is_connected(self, _clinician: Clinician) -> bool:
        return self._connected


def _registry(*, meet: bool = True, zoom: bool | None = None) -> MeetingProviderRegistry:
    """The providers a deployment registers; ``zoom`` is whether it is connected."""
    providers: list[object] = [ManualProvider(), DoxyMeProvider()]
    if meet:
        providers.append(GoogleMeetProvider(lambda _user_id: True))
    if zoom is not None:
        providers.append(FakeZoom(connected=zoom))
    return MeetingProviderRegistry(providers)  # type: ignore[arg-type]


def _connect(repo: InMemoryUserRepository, *, meet: bool = True, zoom: bool | None = None) -> bool:
    return default_to_meet_on_calendar_connect(repo, USER, registry=_registry(meet=meet, zoom=zoom))


def _default(repo: InMemoryUserRepository) -> str | None:
    return repo.get_preferences(USER).default_video_platform


def test_a_new_account_has_no_video_default() -> None:
    assert UserPreferences().default_video_platform is None


@pytest.mark.parametrize("zoom", [None, False])
def test_meet_becomes_the_default_when_none_is_stored(zoom: bool | None) -> None:
    repo = InMemoryUserRepository()

    assert _connect(repo, zoom=zoom) is True
    assert _default(repo) == "google_meet"


@pytest.mark.parametrize("zoom", [None, False, True])
def test_a_stored_zoom_is_never_changed(zoom: bool | None) -> None:
    """Kurt, 2026-11-09: a stored "zoom" stays, connected or not."""
    repo = InMemoryUserRepository()
    repo.update_preferences(USER, {"default_video_platform": "zoom"})

    assert _connect(repo, zoom=zoom) is False
    assert _default(repo) == "zoom"


def test_nothing_changes_for_a_clinician_with_zoom_connected() -> None:
    """The design's acceptance: connect with Zoom connected, unchanged."""
    repo = InMemoryUserRepository()

    assert _connect(repo, zoom=True) is False
    assert _default(repo) is None


def test_a_stored_service_that_is_not_offered_becomes_meet() -> None:
    """doxy.me with no room pasted gives no link; it is not zoom, so Meet."""
    repo = InMemoryUserRepository()
    repo.update_preferences(USER, {"default_video_platform": "doxy_me"})

    assert _connect(repo) is True
    assert _default(repo) == "google_meet"


@pytest.mark.parametrize("stored", ["doxy_me", "google_meet"])
def test_a_stored_default_that_works_is_left_alone(stored: str) -> None:
    repo = InMemoryUserRepository()
    repo.update_preferences(
        USER,
        {"default_video_platform": stored, "telehealth_room_url": "https://doxy.me/dr-test"},
    )

    assert _connect(repo) is False
    assert _default(repo) == stored


def test_nothing_is_set_where_the_deployment_does_not_offer_meet() -> None:
    repo = InMemoryUserRepository()

    assert _connect(repo, meet=False) is False
    assert _default(repo) is None


def test_other_preferences_survive_the_default_being_set() -> None:
    repo = InMemoryUserRepository()
    repo.update_preferences(USER, {"default_duration_minutes": 75})

    _connect(repo)

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
def meet_offered() -> Iterator[list[int]]:
    """Meet registered, Zoom not; the savepoint counts how often it was opened."""
    opened: list[int] = []

    def savepoint() -> contextlib.AbstractContextManager[object]:
        opened.append(1)
        return contextlib.nullcontext()

    registry = _registry()
    app.dependency_overrides[get_meeting_registry] = lambda: registry
    app.dependency_overrides[get_savepoint] = lambda: savepoint
    yield opened
    app.dependency_overrides.pop(get_meeting_registry, None)
    app.dependency_overrides.pop(get_savepoint, None)


@pytest.mark.usefixtures("gcal")
def test_a_calendar_connect_makes_meet_the_default_inside_a_savepoint(
    client: TestClient, mock_user_repo: InMemoryUserRepository, meet_offered: list[int]
) -> None:
    response = client.get("/api/google-calendar/callback", params=_CONNECT)

    assert response.status_code == 200, response.text
    assert mock_user_repo.get_preferences(USER).default_video_platform == "google_meet"
    assert meet_offered == [1]


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
