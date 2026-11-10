# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Which video service a clinician's sessions use when nobody has chosen one.

Connecting Google Calendar is what makes Google Meet possible, so it is also
the moment a clinician with no working default gets one: Meet, set by the
connect itself, with nothing to answer. A setup question here would cost more
clinicians a finished calendar connection than it would ever save.

Three defaults are left alone:

- A stored ``zoom``, whether or not Zoom is connected. It may be a real choice
  (a practice that holds every session on Zoom), and Pablo cannot tell that
  from a value saved before there was any other, so it never changes it. The
  clinician changes it on the Video sessions card.
- Any clinician with Zoom connected, whatever is stored.
- A stored default that already works for them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .telehealth import GOOGLE_MEET, ZOOM, clinician_from_preferences

if TYPE_CHECKING:
    from ..repositories import UserRepository
    from .telehealth import MeetingProviderRegistry


def default_to_meet_on_calendar_connect(
    user_repo: UserRepository,
    user_id: str,
    *,
    registry: MeetingProviderRegistry,
) -> bool:
    """Make Google Meet the default when the clinician has none that works. True if it did.

    Does nothing when the deployment does not offer Meet: a default nobody can
    be given a room by would be the silent no-link this exists to remove.
    """
    if registry.get(GOOGLE_MEET) is None:
        return False
    preferences = user_repo.get_preferences(user_id)
    stored = preferences.default_video_platform
    if stored == ZOOM:
        return False
    clinician = clinician_from_preferences(user_id, preferences)
    for keep in (ZOOM, stored):
        provider = registry.get(keep)
        if provider is not None and provider.is_connected(clinician):
            return False
    user_repo.update_preferences(user_id, {"default_video_platform": GOOGLE_MEET})
    return True
