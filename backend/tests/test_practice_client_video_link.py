# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether the practice sends its clients their video link: the routes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.settings import get_settings

_PATH = "/api/users/me/practice/client-video-link"


def _practice(**overrides: Any) -> SimpleNamespace:
    values = {
        "id": "practice-1",
        "owner_email": "test@example.com",
        "send_clients_video_link": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _call(client: Any, method: str, practice: Any, **kwargs: Any) -> Any:
    session = MagicMock()
    session.get.return_value = practice
    with (
        patch(
            "app.auth.service._resolve_practice_from_email",
            return_value=("practice-1", "practice_1"),
        ),
        patch("app.db.get_db_session", return_value=session),
    ):
        return getattr(client, method)(_PATH, **kwargs)


@pytest.fixture(autouse=True)
def _deployment_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEHEALTH_INCLUDE_JOIN_LINK_IN_REMINDERS", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_reads_the_setting(client: Any) -> None:
    response = _call(client, "get", _practice())
    assert response.status_code == 200
    assert response.json() == {
        "send_clients_video_link": True,
        "available": True,
        "can_change": True,
    }


def test_any_clinician_in_the_practice_can_read_it(client: Any) -> None:
    response = _call(client, "get", _practice(owner_email="someone-else@example.com"))
    assert response.status_code == 200
    assert response.json()["send_clients_video_link"] is True
    assert response.json()["can_change"] is False


def test_owner_turns_it_off_and_on(client: Any) -> None:
    practice = _practice()
    off = _call(client, "put", practice, json={"send_clients_video_link": False})
    assert off.status_code == 200
    assert off.json()["send_clients_video_link"] is False
    assert practice.send_clients_video_link is False
    assert _call(client, "get", practice).json()["send_clients_video_link"] is False

    on = _call(client, "put", practice, json={"send_clients_video_link": True})
    assert on.json()["send_clients_video_link"] is True
    assert practice.send_clients_video_link is True


def test_non_owner_cannot_change_it(client: Any) -> None:
    practice = _practice(owner_email="someone-else@example.com")
    response = _call(client, "put", practice, json={"send_clients_video_link": False})
    assert response.status_code == 403
    assert practice.send_clients_video_link is True


def test_a_deployment_that_sends_no_link_says_so(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEHEALTH_INCLUDE_JOIN_LINK_IN_REMINDERS", "false")
    get_settings.cache_clear()
    response = _call(client, "get", _practice())
    assert response.json()["available"] is False
    assert response.json()["send_clients_video_link"] is True


def test_no_practice_is_404(client: Any) -> None:
    with patch("app.auth.service._resolve_practice_from_email", return_value=None):
        response = client.get(_PATH)
    assert response.status_code == 404


def test_rejects_a_missing_value(client: Any) -> None:
    assert _call(client, "put", _practice(), json={}).status_code == 422
