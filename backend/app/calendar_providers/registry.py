# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which calendar providers this build ships, and how to construct one."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ..settings import Settings
    from .capabilities import CalendarCapability, ProviderCapability
    from .provider import CalendarProvider, ConsentSurface


class UnknownCalendarProviderError(KeyError):
    """No provider is registered under the given id."""


@dataclass(frozen=True)
class ProviderRegistration:
    """Everything the app needs to offer one provider without naming it."""

    provider_id: str
    display_name: str
    capabilities: Mapping[CalendarCapability, ProviderCapability]
    consent_surface: Callable[[Settings], ConsentSurface]
    """Reads this provider's credentials out of deployment configuration."""

    build: Callable[..., CalendarProvider]
    """Constructs the provider from a consent surface and the repositories it needs."""


class CalendarProviderRegistry:
    """The providers available to a caller that doesn't know their names."""

    def __init__(self) -> None:
        self._registrations: dict[str, ProviderRegistration] = {}

    def register(self, registration: ProviderRegistration) -> None:
        if registration.provider_id in self._registrations:
            raise ValueError(f"provider already registered: {registration.provider_id}")
        self._registrations[registration.provider_id] = registration

    def get(self, provider_id: str) -> ProviderRegistration:
        try:
            return self._registrations[provider_id]
        except KeyError:
            raise UnknownCalendarProviderError(provider_id) from None

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(self._registrations)

    def build(self, provider_id: str, settings: Settings, **dependencies: Any) -> CalendarProvider:
        """Construct a provider from deployment configuration."""
        registration = self.get(provider_id)
        return registration.build(registration.consent_surface(settings), **dependencies)


@cache
def default_registry() -> CalendarProviderRegistry:
    """The registry the app runs on.

    Implementations are imported here rather than at module scope: they
    import this package's vocabulary, so importing them from the top of
    this module would be a cycle.
    """
    from ..services.google_calendar_service import google_registration

    registry = CalendarProviderRegistry()
    registry.register(google_registration())
    return registry
