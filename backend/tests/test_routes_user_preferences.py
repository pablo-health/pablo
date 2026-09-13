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


class TestCalendarSetupComplete:
    """The first-visit calendar wizard flag defaults off and round-trips."""

    def test_default_is_false(self) -> None:
        assert UserPreferences().calendar_setup_complete is False

    def test_a_stored_blob_missing_the_key_loads_with_the_default(self) -> None:
        assert UserPreferences(**{}).calendar_setup_complete is False

    def test_put_round_trips_true_through_get(self, client: Any) -> None:
        put_response = client.put(
            "/api/users/me/preferences", json={"calendar_setup_complete": True}
        )
        assert put_response.status_code == 200
        assert put_response.json()["calendar_setup_complete"] is True

        get_response = client.get("/api/users/me/preferences")
        assert get_response.status_code == 200
        assert get_response.json()["calendar_setup_complete"] is True


class TestSavePreferences:
    """Test PUT /api/users/me/preferences."""

    FULL_BODY: ClassVar[dict[str, Any]] = {
        "default_video_platform": "google_meet",
        "default_session_type": "group",
        "default_duration_minutes": 90,
        "auto_transcribe": False,
        "quality_preset": "high",
        "therapist_display_name": "Dr. Rivera",
        "calendar_default_view": "dayGridMonth",
        "timezone": "America/Los_Angeles",
        "theme": "dark",
        "calendar_density": "gentle",
        "calendar_setup_complete": True,
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

    def test_put_rejects_out_of_range_duration(self, client: Any) -> None:
        too_low = client.put("/api/users/me/preferences", json={"default_duration_minutes": 0})
        too_high = client.put("/api/users/me/preferences", json={"default_duration_minutes": 481})

        assert too_low.status_code == 422
        assert too_high.status_code == 422

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


class TestHowSheIsPaid:
    """The checklist that replaced the single-select billing-setup router.

    Two bug classes, and both fail quietly rather than loudly:

    * **An empty list reading back as "she has not answered".** ``[]`` is her
      real answer of "not seeing clients yet"; if it collapsed to ``None`` she
      would be asked again every time, having already told us. That is the
      whole reason the stored shape is a list rather than a nullable route.
    * **A clinician who answered the superseded question losing that answer.**
      Her saved route is read forward once, so she resumes rather than meeting
      an empty checklist.
    """

    def test_several_can_be_true_at_once(self) -> None:
        # The case the single answer could not express: on a platform AND
        # taking clients privately, which is an ordinary practice.
        prefs = UserPreferences(billing_setup_state=["platform", "self_pay"])

        assert prefs.billing_setup_state == ["platform", "self_pay"]

    def test_unanswered_and_no_clients_yet_are_different(self) -> None:
        assert UserPreferences().billing_setup_state is None
        assert UserPreferences(billing_setup_state=[]).billing_setup_state == []

    def test_wanting_credentialing_is_its_own_fact(self) -> None:
        # Not a value in the list: it is about what she WANTS, and folding a
        # wish into a description of today is what made the old answer unable
        # to describe a platform clinician.
        prefs = UserPreferences(billing_setup_state=["platform"])

        assert prefs.billing_setup_wants_credentialing is False

    def test_a_superseded_answer_is_read_forward(self) -> None:
        prefs = UserPreferences(billing_setup_route="platform_to_own")

        assert prefs.billing_setup_state == ["platform"]
        assert prefs.billing_setup_wants_credentialing is True

    def test_only_the_present_tense_half_survives(self) -> None:
        # "wants_panels" said she is paid privately AND hopes to panel. The
        # hope becomes the separate question rather than staying an inference.
        prefs = UserPreferences(billing_setup_route="wants_panels")

        assert prefs.billing_setup_state == ["self_pay"]
        assert prefs.billing_setup_wants_credentialing is True

    def test_a_plain_private_pay_answer_asks_for_nothing(self) -> None:
        prefs = UserPreferences(billing_setup_route="private_pay")

        assert prefs.billing_setup_state == ["self_pay"]
        assert prefs.billing_setup_wants_credentialing is False

    def test_an_answered_checklist_is_never_overwritten_by_the_old_value(self) -> None:
        # The failure this guards: she unticks everything and says she is not
        # seeing clients yet, and the superseded route underneath silently
        # puts her back on a platform.
        prefs = UserPreferences(billing_setup_route="platform_to_own", billing_setup_state=[])

        assert prefs.billing_setup_state == []
        assert prefs.billing_setup_wants_credentialing is False

    def test_it_round_trips_through_the_api(self, client: Any) -> None:
        client.put(
            "/api/users/me/preferences",
            json={"billing_setup_state": ["platform", "self_pay"]},
        )

        body = client.get("/api/users/me/preferences").json()

        assert body["billing_setup_state"] == ["platform", "self_pay"]

    def test_the_api_refuses_a_state_it_does_not_know(self, client: Any) -> None:
        response = client.put(
            "/api/users/me/preferences", json={"billing_setup_state": ["through_a_friend"]}
        )

        assert response.status_code == 422
