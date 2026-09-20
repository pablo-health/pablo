# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which providers a deployment builds, and which it leaves out.

A provider whose dependency is missing must not be built broken: a
deployment with no Zoom app registered does not offer Zoom, and one whose
clinicians have no calendar does not offer Meet. Both are ordinary states,
not misconfiguration, and the surfaces above already render them correctly.
"""

from __future__ import annotations

import pytest
from app.meeting_providers.registry import build_registry
from app.services.telehealth import (
    DOXY_ME,
    GOOGLE_MEET,
    MANUAL,
    ZOOM,
    Clinician,
    enabled_provider_ids,
)
from app.settings import Settings


class FakeZoomStore:
    def get(self, user_id: str) -> None:
        return None

    def save(self, user_id: str, grant: object) -> None:
        return None

    def delete(self, user_id: str) -> bool:
        return False


def a_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"telehealth_providers_enabled": ["manual"]}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class TestWhatIsBuilt:
    def test_the_default_deployment_offers_a_pasted_link_and_nothing_else(self) -> None:
        registry = build_registry(a_settings())
        assert registry.ids() == (MANUAL,)

    def test_a_provider_left_off_the_list_is_never_built(self) -> None:
        registry = build_registry(
            a_settings(telehealth_providers_enabled=["doxy_me"]),
            zoom_store=FakeZoomStore(),
            is_calendar_connected=lambda _user_id: True,
        )
        assert registry.ids() == (DOXY_ME,)

    def test_zoom_needs_a_registered_app_as_well_as_the_list(self) -> None:
        """No client id means the deployment has not registered a Zoom app."""
        without = build_registry(
            a_settings(telehealth_providers_enabled=["zoom"]),
            zoom_store=FakeZoomStore(),
        )
        assert ZOOM not in without.ids()

        with_app = build_registry(
            a_settings(telehealth_providers_enabled=["zoom"], zoom_client_id="abc"),
            zoom_store=FakeZoomStore(),
        )
        assert ZOOM in with_app.ids()

    def test_meet_needs_a_way_to_ask_about_the_calendar(self) -> None:
        without = build_registry(a_settings(telehealth_providers_enabled=["google_meet"]))
        assert GOOGLE_MEET not in without.ids()

        with_calendar = build_registry(
            a_settings(telehealth_providers_enabled=["google_meet"]),
            is_calendar_connected=lambda _user_id: True,
        )
        assert GOOGLE_MEET in with_calendar.ids()

    def test_the_order_is_canonical_not_the_order_they_were_listed(self) -> None:
        registry = build_registry(
            a_settings(
                telehealth_providers_enabled=["manual", "doxy_me", "zoom"],
                zoom_client_id="abc",
            ),
            zoom_store=FakeZoomStore(),
        )
        assert registry.ids() == (ZOOM, DOXY_ME, MANUAL)


class TestWhatIsOffered:
    def test_a_clinician_is_only_offered_what_they_have_connected(self) -> None:
        registry = build_registry(
            a_settings(
                telehealth_providers_enabled=["manual", "doxy_me", "zoom"],
                zoom_client_id="abc",
            ),
            zoom_store=FakeZoomStore(),
        )

        assert registry.offered_to(Clinician(id="c")) == (MANUAL,)
        assert registry.offered_to(Clinician(id="c", room_url="https://d.test/r")) == (
            DOXY_ME,
            MANUAL,
        )


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("manual", (MANUAL,)), ("zoom,manual", (ZOOM, MANUAL)), ("zoom, doxy_me", (ZOOM, DOXY_ME))],
)
def test_the_setting_accepts_the_comma_separated_form_an_environment_carries(
    configured: str, expected: tuple[str, ...]
) -> None:
    settings = Settings(telehealth_providers_enabled=configured)  # type: ignore[arg-type]
    assert enabled_provider_ids(settings.telehealth_providers_enabled) == expected
