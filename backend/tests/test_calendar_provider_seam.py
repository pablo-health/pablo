# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the calendar provider seam: capabilities, consent copy, registry."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from app.calendar_providers.capabilities import (
    CalendarCapability,
    NarrowingEnforcement,
    ProviderCapability,
    UnsupportedCapabilityError,
    scopes_for,
)
from app.calendar_providers.consent_copy import capability_promise, consent_promises
from app.calendar_providers.provider import CalendarProvider, ConsentSurface
from app.calendar_providers.registry import (
    CalendarProviderRegistry,
    UnknownCalendarProviderError,
    default_registry,
)
from app.repositories.google_calendar_token import GoogleCalendarTokenDoc
from app.services.google_calendar_service import (
    GOOGLE_CAPABILITIES,
    GOOGLE_PROVIDER_ID,
    GoogleCalendarService,
    google_registration,
)
from app.settings import get_settings

_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
_FREEBUSY_SCOPE = "https://www.googleapis.com/auth/calendar.freebusy"


@pytest.fixture
def google_service() -> GoogleCalendarService:
    return GoogleCalendarService(
        MagicMock(),
        MagicMock(),
        client_id="test-client-id",
        client_secret="test-client-secret",  # noqa: S106
    )


# Capability -> scope mapping


class TestCapabilityScopeMapping:
    """The mapping is data: a lookup per capability, not a chain of branches."""

    @pytest.mark.parametrize(
        ("capability", "expected"),
        [
            (CalendarCapability.PUSH, (_EVENTS_SCOPE,)),
            (CalendarCapability.BUSY, (_FREEBUSY_SCOPE,)),
            (CalendarCapability.IMPORT, (_READONLY_SCOPE,)),
        ],
    )
    def test_each_capability_maps_to_its_scopes(
        self,
        capability: CalendarCapability,
        expected: tuple[str, ...],
    ) -> None:
        assert scopes_for(GOOGLE_CAPABILITIES, [capability]) == expected

    def test_google_declares_every_capability(self) -> None:
        assert set(GOOGLE_CAPABILITIES) == set(CalendarCapability)

    def test_scopes_are_deduplicated_and_ordered_by_declaration(self) -> None:
        push_first = [CalendarCapability.PUSH, CalendarCapability.IMPORT]
        import_first = [CalendarCapability.IMPORT, CalendarCapability.PUSH]
        expected = (_EVENTS_SCOPE, _READONLY_SCOPE)
        assert scopes_for(GOOGLE_CAPABILITIES, push_first) == expected
        assert scopes_for(GOOGLE_CAPABILITIES, import_first) == expected

    def test_undeclared_capability_is_refused(self) -> None:
        only_push = {CalendarCapability.PUSH: GOOGLE_CAPABILITIES[CalendarCapability.PUSH]}
        with pytest.raises(UnsupportedCapabilityError, match="import"):
            scopes_for(only_push, [CalendarCapability.IMPORT])

    def test_a_declaration_must_carry_scopes_and_a_reach(self) -> None:
        with pytest.raises(ValueError, match="no scopes"):
            ProviderCapability(
                capability=CalendarCapability.BUSY,
                scopes=(),
                incremental=False,
                enforcement=NarrowingEnforcement.PROVIDER_ENFORCED,
                reach="anything",
            )


# Narrowing enforcement and the copy rendered from it


class TestConsentCopy:
    """Copy is generated from the declaration, so it cannot overpromise."""

    def test_every_enforcement_kind_has_copy(self) -> None:
        """A new enforcement kind must ship its copy, not fall through to a default."""
        for enforcement in NarrowingEnforcement:
            declaration = ProviderCapability(
                capability=CalendarCapability.PUSH,
                scopes=("scope",),
                incremental=False,
                enforcement=enforcement,
                reach="something",
            )
            assert capability_promise("Some Calendar", declaration)

    def test_pablo_enforced_copy_never_claims_the_provider_enforces_it(self) -> None:
        """The failure this guards: telling a therapist their calendar is
        unreachable when the grant in fact reaches it."""
        declaration = ProviderCapability(
            capability=CalendarCapability.PUSH,
            scopes=("scope",),
            incremental=False,
            enforcement=NarrowingEnforcement.PABLO_ENFORCED,
            reach="the sessions you book",
        )
        promise = capability_promise("Some Calendar", declaration)
        assert "cannot reach further" not in promise
        assert "the limit is Pablo's own" in promise

    def test_provider_enforced_copy_says_the_grant_itself_is_narrow(self) -> None:
        declaration = ProviderCapability(
            capability=CalendarCapability.BUSY,
            scopes=("scope",),
            incremental=False,
            enforcement=NarrowingEnforcement.PROVIDER_ENFORCED,
            reach="your busy times",
        )
        promise = capability_promise("Some Calendar", declaration)
        assert "cannot reach further" in promise

    def test_google_push_copy_matches_the_grant_google_actually_issues(self) -> None:
        """calendar.events reaches every event, so the copy must not claim
        Google is what keeps Pablo to Pablo's own sessions."""
        promise = capability_promise(
            GoogleCalendarService.display_name,
            GOOGLE_CAPABILITIES[CalendarCapability.PUSH],
        )
        assert "cannot reach further" not in promise
        assert "the limit is Pablo's own" in promise

    def test_google_busy_copy_may_claim_the_narrower_guarantee(self) -> None:
        promise = capability_promise(
            GoogleCalendarService.display_name,
            GOOGLE_CAPABILITIES[CalendarCapability.BUSY],
        )
        assert "cannot reach further" in promise

    def test_promises_cover_exactly_the_requested_capabilities(self) -> None:
        promises = consent_promises(
            GoogleCalendarService.display_name,
            GOOGLE_CAPABILITIES,
            [CalendarCapability.PUSH, CalendarCapability.IMPORT],
        )
        assert len(promises) == 2
        assert all(GoogleCalendarService.display_name in promise for promise in promises)

    def test_promises_refuse_an_undeclared_capability(self) -> None:
        only_push = {CalendarCapability.PUSH: GOOGLE_CAPABILITIES[CalendarCapability.PUSH]}
        with pytest.raises(UnsupportedCapabilityError):
            consent_promises("Some Calendar", only_push, [CalendarCapability.BUSY])


# Registry


class TestProviderRegistry:
    def test_google_is_registered(self) -> None:
        registry = default_registry()
        assert registry.provider_ids() == (GOOGLE_PROVIDER_ID,)
        assert registry.get(GOOGLE_PROVIDER_ID).display_name == "Google Calendar"

    def test_unknown_provider_is_refused(self) -> None:
        with pytest.raises(UnknownCalendarProviderError):
            default_registry().get("nowhere")

    def test_registering_the_same_id_twice_is_refused(self) -> None:
        registry = CalendarProviderRegistry()
        registry.register(google_registration())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(google_registration())

    def test_build_produces_a_calendar_provider(self) -> None:
        provider = default_registry().build(
            GOOGLE_PROVIDER_ID,
            get_settings(),
            token_repo=MagicMock(),
            appointment_repo=MagicMock(),
        )
        assert isinstance(provider, CalendarProvider)
        assert provider.provider_id == GOOGLE_PROVIDER_ID

    def test_google_service_satisfies_the_protocol(
        self,
        google_service: GoogleCalendarService,
    ) -> None:
        assert isinstance(google_service, CalendarProvider)


# Requesting capabilities on the Google implementation


class TestGoogleCapabilityRequests:
    @patch("app.services.google_calendar_service._build_flow")
    def test_connecting_requests_the_same_scopes_as_before_the_seam(
        self,
        mock_build_flow: MagicMock,
        google_service: GoogleCalendarService,
    ) -> None:
        """Regression guard: the seam must not change what a connect asks for."""
        mock_build_flow.return_value.authorization_url.return_value = ("https://url", "state")

        google_service.get_auth_url("user-001", "http://localhost/callback")

        assert mock_build_flow.call_args[0][3] == (_EVENTS_SCOPE, _READONLY_SCOPE)

    @patch("app.services.google_calendar_service._build_flow")
    def test_a_capability_can_be_requested_on_its_own(
        self,
        mock_build_flow: MagicMock,
        google_service: GoogleCalendarService,
    ) -> None:
        """IMPORT is declared incremental: asking for it later asks for one scope."""
        mock_build_flow.return_value.authorization_url.return_value = ("https://url", "state")

        google_service.get_auth_url(
            "user-001",
            "http://localhost/callback",
            capabilities=[CalendarCapability.IMPORT],
        )

        assert mock_build_flow.call_args[0][3] == (_READONLY_SCOPE,)

    def test_a_surface_may_withhold_a_capability(self) -> None:
        surface = ConsentSurface(
            provider_id=GOOGLE_PROVIDER_ID,
            client_id="id",
            client_secret="secret",  # noqa: S106
            allowed_capabilities=frozenset({CalendarCapability.PUSH}),
        )
        service = GoogleCalendarService.from_surface(
            surface,
            token_repo=MagicMock(),
            appointment_repo=MagicMock(),
        )
        with pytest.raises(UnsupportedCapabilityError, match="import"):
            service.get_auth_url(
                "user-001",
                "http://localhost/callback",
                capabilities=[CalendarCapability.IMPORT],
            )

    @pytest.mark.parametrize(
        "method_name",
        ["list_busy_windows", "scan_importable_events"],
    )
    def test_unwired_capabilities_are_seams_not_silent_successes(
        self,
        method_name: str,
        google_service: GoogleCalendarService,
    ) -> None:
        now = datetime.now(UTC)
        with pytest.raises(NotImplementedError):
            getattr(google_service, method_name)("user-001", now, now)


# Provider discriminator on the stored token row


class TestTokenProviderDiscriminator:
    def test_new_docs_default_to_google(self) -> None:
        doc = GoogleCalendarTokenDoc(user_id="user-001", encrypted_tokens="x")
        assert doc.provider == "google"

    def test_a_row_written_before_the_column_reads_as_google(self) -> None:
        """Existing connections keep working without a backfill or re-consent."""
        doc = GoogleCalendarTokenDoc.from_dict(
            {"user_id": "user-001", "encrypted_tokens": "x"},
        )
        assert doc.provider == "google"

    def test_provider_round_trips(self) -> None:
        doc = GoogleCalendarTokenDoc(user_id="user-001", encrypted_tokens="x", provider="google")
        assert GoogleCalendarTokenDoc.from_dict(doc.to_dict()).provider == "google"


# The seam's whole point: scope strings stay behind the provider


def test_scope_strings_appear_only_in_the_google_implementation() -> None:
    """Nothing above the provider layer names a scope. If this fails, a
    caller has learned something about Google it should be asking a
    capability for instead."""
    app_root = Path(__file__).resolve().parents[1] / "app"
    google_impl = app_root / "services" / "google_calendar_service.py"

    scopes = (_EVENTS_SCOPE, _READONLY_SCOPE, _FREEBUSY_SCOPE)
    offenders = sorted(
        str(path.relative_to(app_root))
        for path in app_root.rglob("*.py")
        if path != google_impl and any(scope in path.read_text() for scope in scopes)
    )
    assert offenders == []
