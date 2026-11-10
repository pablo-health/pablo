# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Assembling the providers this deployment can actually offer.

Two filters, and they answer different questions:

* ``telehealth_providers_enabled`` is the deployment's list. A provider left
  off it is never built, so its credentials are never read and its module
  never runs.
* :meth:`MeetingProviderRegistry.offered_to` is the clinician's. A provider
  that is built but that this clinician has not connected is not offered to
  them, because booking against it would produce an appointment whose link
  never arrives.

``manual`` is always registered when it is enabled, and enabling it is the
sane default: it is the only provider that works with no connection, no
credentials and no vendor, and a deployment that offers nothing else can
still run telehealth by pasting a link.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..services.telehealth import (
    DOXY_ME,
    GOOGLE_MEET,
    MANUAL,
    ZOOM,
    MeetingProvider,
    MeetingProviderRegistry,
    enabled_provider_ids,
)
from .doxy import DoxyMeProvider
from .manual import ManualProvider
from .meet import GoogleMeetProvider
from .zoom import ZoomProvider
from .zoom_room import ZoomAppOrRoom, ZoomRoomProvider

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..settings import Settings
    from .zoom import ZoomConnectionStore


def build_registry(
    settings: Settings,
    *,
    zoom_store: ZoomConnectionStore | None = None,
    is_calendar_connected: Callable[[str], bool] | None = None,
) -> MeetingProviderRegistry:
    """The providers this process can talk to, in one registry.

    A provider whose dependency is missing is left out rather than built
    broken: no calendar means no Meet. Zoom with no OAuth client configured
    (or no connection store) is a deployment that has not registered a Zoom
    app, so Zoom there is the clinician's own pasted room and nothing more;
    with an app, a connected clinician gets a meeting per appointment and the
    pasted room is the fallback for one who has not connected.
    """
    enabled = enabled_provider_ids(settings.telehealth_provider_names)
    providers: list[MeetingProvider] = []

    if MANUAL in enabled:
        providers.append(ManualProvider())
    if DOXY_ME in enabled:
        providers.append(DoxyMeProvider())
    if GOOGLE_MEET in enabled and is_calendar_connected is not None:
        providers.append(GoogleMeetProvider(is_calendar_connected))
    if ZOOM in enabled:
        if zoom_store is not None and settings.zoom_client_id:
            providers.append(
                ZoomAppOrRoom(
                    ZoomProvider(
                        store=zoom_store,
                        client_id=settings.zoom_client_id,
                        client_secret=settings.zoom_client_secret.get_secret_value(),
                    )
                )
            )
        else:
            providers.append(ZoomRoomProvider())
    return MeetingProviderRegistry(providers)
