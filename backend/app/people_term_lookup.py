# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading what decides a clinician's word for the people they see.

The rules are in ``app.people_term``. This module loads what those rules
need (the clinician's own choice, their professional details and the
practice default) for the settings routes and for backend text written in
the clinician's words, such as the prompts a note is drafted with.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import db
from .auth import service as auth_service
from .db.platform_models import PracticeRow
from .people_term import (
    DEFAULT_PEOPLE_TERM,
    PeopleTerm,
    Suggestion,
    as_people_term,
    people_words,
    resolve_people_term,
    suggest_people_term,
)
from .repositories import get_clinician_profile_repository, get_user_repository

if TYPE_CHECKING:
    from .models import User
    from .repositories import ClinicianProfileRepository, UserRepository


def practice_row_for(user: User) -> PracticeRow | None:
    """The practice ``user`` belongs to, by their email's mapping."""
    # Looked up through the modules, so a test can stand either in.
    practice = auth_service._resolve_practice_from_email(user.email)
    if practice is None:
        return None
    return db.get_db_session().get(PracticeRow, practice[0])


@dataclass(frozen=True)
class PeopleTermFacts:
    """Everything that decides the word for one clinician."""

    choice: PeopleTerm | None
    suggestion: Suggestion | None
    practice: PracticeRow | None

    @property
    def practice_default(self) -> PeopleTerm | None:
        return as_people_term(self.practice.people_term if self.practice else None)

    @property
    def term(self) -> PeopleTerm:
        return resolve_people_term(
            choice=self.choice,
            suggested=self.suggestion.term if self.suggestion else None,
            practice_default=self.practice_default,
        )


def people_term_facts(
    user: User, user_repo: UserRepository, profile_repo: ClinicianProfileRepository
) -> PeopleTermFacts:
    profile = profile_repo.get(user.id)
    return PeopleTermFacts(
        choice=user_repo.get_preferences(user.id).people_term,
        suggestion=suggest_people_term(
            provider_type=user.provider_type,
            credential_titles=profile.credential_titles if profile else None,
            credentials=profile.credentials if profile else None,
            dea_number=profile.dea_number if profile else None,
        ),
        practice=practice_row_for(user),
    )


class PeopleTermLookup:
    """The word each clinician uses, for text written in their words."""

    def __init__(self, users: UserRepository, profiles: ClinicianProfileRepository) -> None:
        self._users = users
        self._profiles = profiles

    def term(self, user_id: str) -> PeopleTerm:
        user = self._users.get(user_id)
        if user is None:
            return DEFAULT_PEOPLE_TERM
        return people_term_facts(user, self._users, self._profiles).term

    def person(self, user_id: str) -> str:
        """The singular: "client" or "patient"."""
        return people_words(self.term(user_id)).one


def worker_people_term_lookup() -> PeopleTermLookup:
    """A lookup on the current database session, for code that builds its own repositories."""
    return PeopleTermLookup(get_user_repository(), get_clinician_profile_repository())
