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
    PeopleTerm,
    PeopleWords,
    SuggestionSource,
    people_words,
)
from ..people_term_lookup import PeopleTermLookup, people_term_facts
from ..repositories import (
    ClinicianProfileRepository,
    UserRepository,
    get_clinician_profile_repository,
    get_user_repository,
)
from .users import _get_own_practice_as_owner, _is_practice_owner

if TYPE_CHECKING:
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


def load_people_term(
    user: User,
    user_repo: UserRepository,
    profile_repo: ClinicianProfileRepository,
) -> PeopleTermResponse:
    """Everything that decides the word for ``user``."""
    facts = people_term_facts(user, user_repo, profile_repo)
    return PeopleTermResponse(
        people_term=facts.term,
        choice=facts.choice,
        suggested=facts.suggestion.term if facts.suggestion else None,
        suggested_from=facts.suggestion.source if facts.suggestion else None,
        practice_default=facts.practice_default,
        can_set_practice_default=facts.practice is not None
        and _is_practice_owner(facts.practice, user),
    )


def people_words_for(
    user_id: str,
    user_repo: UserRepository,
    profile_repo: ClinicianProfileRepository,
) -> PeopleWords:
    """The words for backend copy ``user_id`` reads: ``words.many`` and friends."""
    return people_words(PeopleTermLookup(user_repo, profile_repo).term(user_id))


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
