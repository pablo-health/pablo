# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Which video service a clinician's sessions use when nobody has chosen one.

Connecting Google Calendar is what makes Google Meet possible, so it is also
the moment a clinician with no working default gets one: Meet, set by the
connect itself, with nothing to answer. A setup question here would cost more
clinicians a finished calendar connection than it would ever save.

Two defaults are left alone:

- A stored one, whatever it is. A stored ``zoom`` may be a real choice (a
  clinician who holds every session on Zoom), or a value saved before there
  was any other; Pablo cannot tell the two apart, so it does not guess. The
  calendar setup says Meet is available and the clinician decides.
- One never saved that already works. An account with no saved default reads
  ``zoom`` (``UserPreferences``), and a clinician with Zoom connected has been
  getting Zoom rooms from exactly that. Moving them to Meet would change where
  their clients go without asking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .telehealth import GOOGLE_MEET, Clinician

if TYPE_CHECKING:
    from ..models import UserPreferences
    from ..repositories import UserRepository
    from .telehealth import MeetingProviderRegistry

_FIELD = "default_video_platform"


def has_stored_video_default(preferences: UserPreferences) -> bool:
    """Whether the clinician's preferences hold a saved video default.

    False for an account that never saved one, which still reads ``zoom``.
    """
    return _FIELD in preferences.model_fields_set


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
    if has_stored_video_default(preferences):
        return False
    unsaved = registry.get(preferences.default_video_platform)
    clinician = Clinician(
        id=user_id,
        preferred_provider=preferences.default_video_platform,
        room_url=preferences.telehealth_room_url,
    )
    if unsaved is not None and unsaved.is_connected(clinician):
        return False
    user_repo.update_preferences(user_id, {_FIELD: GOOGLE_MEET})
    return True
