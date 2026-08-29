# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for GET/PUT /api/users/me/preferences."""

from typing import Any, ClassVar

from app.models import User
from app.models.user import UserPreferences
from app.repositories import InMemoryUserRepository


class TestGetPreferences:
    """Test GET /api/users/me/preferences."""

    def test_get_returns_defaults_when_never_saved(self, client: Any) -> None:
        response = client.get("/api/users/me/preferences")

        assert response.status_code == 200
        body = response.json()
        assert body["default_duration_minutes"] == 50
        assert body["default_video_platform"] == "zoom"
        assert body["auto_transcribe"] is True
        assert body["therapist_display_name"] is None
        assert body["calendar_density"] == "balanced"


class TestCalendarDensity:
    """Calendar density defaults to balanced and round-trips through the API."""

    def test_default_is_balanced(self) -> None:
        assert UserPreferences().calendar_density == "balanced"

    def test_a_stored_blob_missing_the_key_loads_with_the_default(self) -> None:
        assert UserPreferences(**{}).calendar_density == "balanced"

    def test_put_round_trips_compact_through_get(self, client: Any) -> None:
        put_response = client.put("/api/users/me/preferences", json={"calendar_density": "compact"})
        assert put_response.status_code == 200
        assert put_response.json()["calendar_density"] == "compact"

        get_response = client.get("/api/users/me/preferences")
        assert get_response.status_code == 200
        assert get_response.json()["calendar_density"] == "compact"

    def test_put_rejects_an_unknown_density(self, client: Any) -> None:
        response = client.put("/api/users/me/preferences", json={"calendar_density": "dense"})
        assert response.status_code == 422


class TestSavePreferences:
    """Test PUT /api/users/me/preferences."""

    FULL_BODY: ClassVar[dict[str, Any]] = {
        "default_video_platform": "google_meet",
        "default_session_type": "group",
        "default_duration_minutes": 90,
        "auto_transcribe": False,
        "quality_preset": "high",
        "therapist_display_name": "Dr. Rivera",
        "working_hours_start": 6,
        "working_hours_end": 22,
        "calendar_default_view": "dayGridMonth",
        "timezone": "America/Los_Angeles",
        "theme": "dark",
        "calendar_density": "gentle",
    }

    def test_put_round_trips_a_full_body(self, client: Any) -> None:
        put_response = client.put("/api/users/me/preferences", json=self.FULL_BODY)

        assert put_response.status_code == 200
        for key, value in self.FULL_BODY.items():
            assert put_response.json()[key] == value

        get_response = client.get("/api/users/me/preferences")

        assert get_response.status_code == 200
        for key, value in self.FULL_BODY.items():
            assert get_response.json()[key] == value

    def test_put_is_a_full_replace(self, client: Any) -> None:
        client.put("/api/users/me/preferences", json=self.FULL_BODY)

        response = client.put("/api/users/me/preferences", json={"default_duration_minutes": 30})

        assert response.status_code == 200
        body = response.json()
        assert body["default_duration_minutes"] == 30
        assert body["default_video_platform"] == "zoom"
        assert body["default_session_type"] == "individual"
        assert body["auto_transcribe"] is True
        assert body["therapist_display_name"] is None
        assert body["working_hours_start"] == 8
        assert body["working_hours_end"] == 18

    def test_put_rejects_out_of_range_duration(self, client: Any) -> None:
        too_low = client.put("/api/users/me/preferences", json={"default_duration_minutes": 0})
        too_high = client.put("/api/users/me/preferences", json={"default_duration_minutes": 481})

        assert too_low.status_code == 422
        assert too_high.status_code == 422

    def test_put_rejects_out_of_range_working_hours(self, client: Any) -> None:
        bad_start = client.put("/api/users/me/preferences", json={"working_hours_start": 24})
        bad_end = client.put("/api/users/me/preferences", json={"working_hours_end": 0})

        assert bad_start.status_code == 422
        assert bad_end.status_code == 422

    def test_put_rejects_wrong_types(self, client: Any) -> None:
        response = client.put(
            "/api/users/me/preferences", json={"auto_transcribe": "not-a-boolean"}
        )

        assert response.status_code == 422

    def test_preferences_are_scoped_to_the_caller(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        client.put("/api/users/me/preferences", json=self.FULL_BODY)

        other_user_prefs = mock_user_repo.get_preferences("someone-else")

        assert other_user_prefs.default_duration_minutes == 50
        assert other_user_prefs.default_video_platform == "zoom"
