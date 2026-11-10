# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether the practice sends its clients their video link.

A practice setting, on by default. On, a message to a client about a video
appointment carries the link to join; off, it does not. The decision itself is
applied in ``app.services.telehealth.client_join_link``.

``GET`` is readable by every clinician in the practice. ``PUT`` is the practice
owner's, like every other practice-wide setting. ``available`` is false when the
deployment sends no link to anybody, in which case the practice's answer
changes nothing and the setting is not shown.

No PHI: one practice preference.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..api_errors import NotFoundError
from ..auth.service import require_baa_acceptance
from ..settings import get_settings
from .users import _get_own_practice_as_owner, _is_practice_owner, _resolve_practice_id_for

if TYPE_CHECKING:
    from ..db.platform_models import PracticeRow
    from ..models import User

router = APIRouter(prefix="/api/users", tags=["users"])

CurrentUser = Annotated["User", Depends(require_baa_acceptance)]


class ClientVideoLinkSetting(BaseModel):
    """``GET``/``PUT /api/users/me/practice/client-video-link``."""

    #: Whether messages to clients carry a video appointment's link.
    send_clients_video_link: bool
    #: Whether the deployment lets any link go out. False means the practice's
    #: answer has no effect.
    available: bool
    #: Whether the caller may change the setting (the practice owner).
    can_change: bool


class UpdateClientVideoLinkSetting(BaseModel):
    send_clients_video_link: bool


def _response(practice: PracticeRow, user: User) -> ClientVideoLinkSetting:
    return ClientVideoLinkSetting(
        send_clients_video_link=practice.send_clients_video_link,
        available=get_settings().telehealth_include_join_link_in_reminders,
        can_change=_is_practice_owner(practice, user),
    )


@router.get("/me/practice/client-video-link")
def get_client_video_link_setting(user: CurrentUser) -> ClientVideoLinkSetting:
    """The setting, for any clinician in the practice."""
    from ..db import get_db_session
    from ..db.platform_models import PracticeRow

    practice_id = _resolve_practice_id_for(user)
    practice = get_db_session().get(PracticeRow, practice_id) if practice_id else None
    if practice is None:
        raise NotFoundError("No practice mapping for this account", code="NO_PRACTICE")
    return _response(practice, user)


@router.put("/me/practice/client-video-link")
def update_client_video_link_setting(
    request: UpdateClientVideoLinkSetting, user: CurrentUser
) -> ClientVideoLinkSetting:
    """Turn the setting on or off. 403 unless the caller owns the practice."""
    from ..db import get_db_session

    practice = _get_own_practice_as_owner(user)
    practice.send_clients_video_link = request.send_clients_video_link
    get_db_session().flush()
    return _response(practice, user)
