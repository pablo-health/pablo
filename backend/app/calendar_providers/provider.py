# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The contract every calendar provider implementation offers the app above it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

from .capabilities import CalendarCapability

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping
    from datetime import datetime

    from ..scheduling_engine.models.appointment import Appointment
    from .capabilities import CalendarWriteTarget, ProviderCapability


@dataclass(frozen=True)
class ConsentSurface:
    """Credentials and permitted capabilities for one place we ask for consent.

    Credentials are configuration attached to a surface, not constants on a
    provider class: whether two surfaces share a client id, and which
    capabilities a surface may ask for at all, are deployment decisions and
    should never need a code change.
    """

    provider_id: str
    client_id: str
    client_secret: str
    allowed_capabilities: frozenset[CalendarCapability] = field(
        default_factory=lambda: frozenset(CalendarCapability)
    )
    redirect_uri: str | None = None
    """Configured default. Callers that carry their own validated redirect
    URI (the OAuth routes do) pass it per request instead."""


@dataclass(frozen=True)
class BusyWindow:
    """A stretch of time the therapist is unavailable. Times only, no titles."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class ImportCandidate:
    """One event a therapist may choose to import as a Pablo appointment.

    Only ever produced under an IMPORT grant, which is asked for when an
    import is run and not at connect.
    """

    provider_event_id: str
    start: datetime
    end: datetime
    summary: str


@runtime_checkable
class CalendarProvider(Protocol):
    """A therapist's calendar, whoever hosts it.

    Everything here is expressed in Pablo's terms — capabilities,
    appointments, busy windows. Provider-shaped detail (scope strings, event
    JSON, sync tokens, page tokens) stays behind the implementation.
    """

    provider_id: ClassVar[str]
    display_name: ClassVar[str]

    def capability_declarations(
        self,
        *,
        write_target: CalendarWriteTarget = ...,
    ) -> Mapping[CalendarCapability, ProviderCapability]:
        """How this provider satisfies each capability it supports.

        PUSH is declared per write target, because whether a provider can
        narrow a write to a calendar it owns is a fact about the provider.
        """
        ...

    def get_auth_url(
        self,
        user_id: str,
        redirect_uri: str,
        *,
        capabilities: Collection[CalendarCapability] | None = None,
        write_target: CalendarWriteTarget = ...,
    ) -> str:
        """Authorization URL granting exactly the requested capabilities.

        ``None`` means the provider's connect-time set. An incremental
        capability is asked for later, by passing it here on its own.
        """
        ...

    def handle_callback(
        self,
        user_id: str,
        code: str,
        redirect_uri: str,
        *,
        state: str,
        capabilities: Collection[CalendarCapability] | None = None,
        write_target: CalendarWriteTarget = ...,
    ) -> None:
        """Exchange an authorization code for tokens and store them encrypted.

        ``state`` is the value the provider handed back with the code, and
        must be the one this user's authorization request minted; an
        implementation checks it before spending the code.

        The write target has to match the one the authorization URL was
        built with: it decides both the grant asked for and which calendar
        the connection is bound to.
        """
        ...

    def push_appointment(self, user_id: str, appointment: Appointment) -> str | None:
        """Create or update the provider's event for an appointment.

        Returns the provider's event id, or None if the user isn't connected.
        """
        ...

    def delete_event(self, user_id: str, event_id: str) -> bool:
        """Delete a provider event Pablo previously pushed."""
        ...

    def list_busy_windows(
        self,
        user_id: str,
        start: datetime,
        end: datetime,
    ) -> list[BusyWindow]:
        """When the therapist is already booked, over a window.

        Raises NotImplementedError on a provider that has not wired BUSY up.
        """
        ...

    def scan_importable_events(
        self,
        user_id: str,
        start: datetime,
        end: datetime,
    ) -> list[ImportCandidate]:
        """Events over a window that a therapist could import.

        Raises NotImplementedError on a provider that has not wired IMPORT up.
        """
        ...

    def disconnect(self, user_id: str) -> bool:
        """Remove stored tokens, disconnecting the calendar."""
        ...

    def get_sync_status(self, user_id: str) -> dict[str, Any]:
        """Connection status and last sync time."""
        ...
