# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether the practice asks its clients to agree to AI-assisted notes.

A practice setting, on by default. On, the session surface offers a script to
read aloud before recording and every session note shows the client's answer
from their consent record (``app.services.client_ai_consent``). Off, neither
appears; the consent record itself is unaffected and still shows on the chart.

``GET`` is readable by every clinician in the practice, because each of them
needs it to decide what to show. It also carries the practice's audio retention
window, which the script reads aloud, so a clinician who cannot open the
owner-only retention setting still gets the right number. ``PUT`` is the
practice owner's, like every other practice-wide setting.

No PHI: one practice preference and a number of days.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..api_errors import NotFoundError
from ..auth.service import require_baa_acceptance
from .users import _get_own_practice_as_owner, _is_practice_owner, _resolve_practice_id_for

if TYPE_CHECKING:
    from ..db.platform_models import PracticeRow
    from ..models import User

router = APIRouter(prefix="/api/users", tags=["users"])

CurrentUser = Annotated["User", Depends(require_baa_acceptance)]


class AiNotesConsentSetting(BaseModel):
    """``GET``/``PUT /api/users/me/practice/ai-notes-consent``."""

    #: Whether clinicians ask each client to agree to AI-assisted notes.
    ask_clients_about_ai_notes: bool
    #: How long the practice keeps session audio, in days. Read aloud in the
    #: script.
    audio_retention_days: int
    #: Whether the caller may change the setting (the practice owner).
    can_change: bool


class UpdateAiNotesConsentSetting(BaseModel):
    ask_clients_about_ai_notes: bool


def _response(practice: PracticeRow, user: User) -> AiNotesConsentSetting:
    return AiNotesConsentSetting(
        ask_clients_about_ai_notes=practice.ask_clients_about_ai_notes,
        audio_retention_days=practice.audio_retention_days,
        can_change=_is_practice_owner(practice, user),
    )


@router.get("/me/practice/ai-notes-consent")
def get_ai_notes_consent_setting(user: CurrentUser) -> AiNotesConsentSetting:
    """The setting, for any clinician in the practice."""
    from ..db import get_db_session
    from ..db.platform_models import PracticeRow

    practice_id = _resolve_practice_id_for(user)
    practice = get_db_session().get(PracticeRow, practice_id) if practice_id else None
    if practice is None:
        raise NotFoundError("No practice mapping for this account", code="NO_PRACTICE")
    return _response(practice, user)


@router.put("/me/practice/ai-notes-consent")
def update_ai_notes_consent_setting(
    request: UpdateAiNotesConsentSetting, user: CurrentUser
) -> AiNotesConsentSetting:
    """Turn the setting on or off. 403 unless the caller owns the practice."""
    from ..db import get_db_session

    practice = _get_own_practice_as_owner(user)
    practice.ask_clients_about_ai_notes = request.ask_clients_about_ai_notes
    get_db_session().flush()
    return _response(practice, user)
