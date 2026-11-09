# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Which video service a clinician's sessions use when nobody has chosen one.

Connecting Google Calendar is what makes Google Meet possible, so it is also
the moment a clinician with no default gets one: Meet, set by the connect
itself, with nothing to answer. A setup question here would cost more
clinicians a finished calendar connection than it would ever save.

A stored default is left alone, whatever it is. A stored ``zoom`` may be a
real choice (a clinician who holds every session on Zoom), or a value saved
before there was any other; Pablo cannot tell the two apart, so it does not
guess. The calendar setup says Meet is available and the clinician decides.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .telehealth import GOOGLE_MEET, enabled_provider_ids

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..repositories import UserRepository


def default_to_meet_on_calendar_connect(
    user_repo: UserRepository,
    user_id: str,
    *,
    enabled_providers: Iterable[str],
) -> bool:
    """Make Google Meet the default when the clinician has none. True if it did.

    Does nothing when the deployment does not offer Meet: a default nobody can
    be given a room by would be the silent no-link this exists to remove.
    """
    if GOOGLE_MEET not in enabled_provider_ids(enabled_providers):
        return False
    if user_repo.get_preferences(user_id).default_video_platform is not None:
        return False
    user_repo.update_preferences(user_id, {"default_video_platform": GOOGLE_MEET})
    return True
