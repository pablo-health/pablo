# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether the app says "clients" or "patients" to the signed-in clinician.

The rules live in ``app.people_term``; this module loads what they need and
stores the two choices: the clinician's own (in their preferences) and the
practice default (on the practice row, owner only).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth.route_access import subscription_exempt
from ..auth.service import get_current_user_no_mfa
from ..people_term import (
    DEFAULT_PEOPLE_TERM,
    PeopleTerm,
    PeopleWords,
    SuggestionSource,
    as_people_term,
    people_words,
    resolve_people_term,
    suggest_people_term,
)
from ..repositories import (
    ClinicianProfileRepository,
    UserRepository,
    get_clinician_profile_repository,
    get_user_repository,
)
from .users import _get_own_practice_as_owner, _is_practice_owner, _resolve_practice_id_for

if TYPE_CHECKING:
    from ..db.platform_models import PracticeRow
    from ..models import User

router = APIRouter(prefix="/api/users", tags=["users"])


class PeopleTermResponse(BaseModel):
    """The word in use, and what decided it."""

    #: The word the app uses for this clinician.
    people_term: PeopleTerm
    #: The clinician's own choice; None when they have not made one.
    choice: PeopleTerm | None
    #: What their clinician type, licenses and prescriber details suggest;
    #: None when they don't settle it.
    suggested: PeopleTerm | None
    #: Which detail the suggestion came from: "clinician_type", "dea_number"
    #: or "license". None when there is no suggestion.
    suggested_from: SuggestionSource | None
    #: The practice default; None when the practice has not set one.
    practice_default: PeopleTerm | None
    #: Whether this clinician may change the practice default.
    can_set_practice_default: bool


class UpdatePeopleTermRequest(BaseModel):
    """Set a choice, or clear it with null to go back to the default."""

    people_term: PeopleTerm | None


def _practice_row(user: User) -> PracticeRow | None:
    from ..db import get_db_session
    from ..db.platform_models import PracticeRow

    practice_id = _resolve_practice_id_for(user)
    if practice_id is None:
        return None
    return get_db_session().get(PracticeRow, practice_id)


def load_people_term(
    user: User,
    user_repo: UserRepository,
    profile_repo: ClinicianProfileRepository,
) -> PeopleTermResponse:
    """Everything that decides the word for ``user``."""
    choice = user_repo.get_preferences(user.id).people_term
    profile = profile_repo.get(user.id)
    suggestion = suggest_people_term(
        provider_type=user.provider_type,
        credential_titles=profile.credential_titles if profile else None,
        credentials=profile.credentials if profile else None,
        dea_number=profile.dea_number if profile else None,
    )
    suggested = suggestion.term if suggestion else None
    practice = _practice_row(user)
    practice_default = as_people_term(practice.people_term if practice else None)
    return PeopleTermResponse(
        people_term=resolve_people_term(
            choice=choice, suggested=suggested, practice_default=practice_default
        ),
        choice=choice,
        suggested=suggested,
        suggested_from=suggestion.source if suggestion else None,
        practice_default=practice_default,
        can_set_practice_default=practice is not None and _is_practice_owner(practice, user),
    )


def people_words_for(
    user_id: str,
    user_repo: UserRepository,
    profile_repo: ClinicianProfileRepository,
) -> PeopleWords:
    """The words for backend copy ``user_id`` reads: ``words.many`` and friends."""
    user = user_repo.get(user_id)
    if user is None:
        return people_words(DEFAULT_PEOPLE_TERM)
    return people_words(load_people_term(user, user_repo, profile_repo).people_term)


@router.get("/me/people-term")
def get_people_term(
    user: User = Depends(get_current_user_no_mfa),
    user_repo: UserRepository = Depends(get_user_repository),
    profile_repo: ClinicianProfileRepository = Depends(get_clinician_profile_repository),
    _: None = Depends(subscription_exempt),
) -> PeopleTermResponse:
    """The word the app uses for the caller, and what decided it."""
    return load_people_term(user, user_repo, profile_repo)


@router.put("/me/people-term")
def set_people_term(
    request: UpdatePeopleTermRequest,
    user: User = Depends(get_current_user_no_mfa),
    user_repo: UserRepository = Depends(get_user_repository),
    profile_repo: ClinicianProfileRepository = Depends(get_clinician_profile_repository),
    _: None = Depends(subscription_exempt),
) -> PeopleTermResponse:
    """Set the caller's own word. Reachable before MFA so onboarding can set it."""
    prefs = user_repo.get_preferences(user.id)
    prefs.people_term = request.people_term
    user_repo.save_preferences(user.id, prefs)
    return load_people_term(user, user_repo, profile_repo)


@router.put("/me/practice/people-term")
def set_practice_people_term(
    request: UpdatePeopleTermRequest,
    user: User = Depends(get_current_user_no_mfa),
    user_repo: UserRepository = Depends(get_user_repository),
    profile_repo: ClinicianProfileRepository = Depends(get_clinician_profile_repository),
    _: None = Depends(subscription_exempt),
) -> PeopleTermResponse:
    """Set the practice default. 403 unless the caller owns the practice."""
    from ..db import get_db_session

    practice = _get_own_practice_as_owner(user)
    practice.people_term = request.people_term
    get_db_session().flush()
    return load_people_term(user, user_repo, profile_repo)
