# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL user repository — reads/writes from platform.users."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select

from ...db.platform_models import PlatformUserPreferencesRow, PlatformUserRow
from ...models import User, UserPreferences
from ..user import UserRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresUserRepository(UserRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: str) -> User | None:
        # ``users.id`` is a uuid column, but the auth resolve path still
        # falls back to the provider's raw subject id when no identity
        # mapping exists yet (a brand-new user). Postgres would reject the
        # cast with a DataError; "no such user" is the correct answer.
        try:
            uuid.UUID(user_id)
        except ValueError:
            return None
        row = self._session.get(PlatformUserRow, user_id)
        if row is None:
            return None
        return _row_to_user(row)

    def update(self, user: User) -> User:
        row = self._session.get(PlatformUserRow, user.id)
        if row is None:
            row = PlatformUserRow(id=user.id)
            self._session.add(row)
        _user_to_row(user, row)
        self._session.flush()
        return user

    def list_all(self) -> list[User]:
        rows = self._session.execute(select(PlatformUserRow)).scalars().all()
        return [_row_to_user(r) for r in rows]

    def get_preferences(self, user_id: str) -> UserPreferences:
        row = self._session.get(PlatformUserPreferencesRow, user_id)
        if row is None:
            return UserPreferences()
        return UserPreferences(**row.preferences)

    def get_preferences_many(self, user_ids: list[str]) -> dict[str, UserPreferences]:
        if not user_ids:
            return {}
        rows = (
            self._session.execute(
                select(PlatformUserPreferencesRow).where(
                    PlatformUserPreferencesRow.user_id.in_(user_ids)
                )
            )
            .scalars()
            .all()
        )
        saved = {row.user_id: UserPreferences(**row.preferences) for row in rows}
        return {uid: saved.get(uid, UserPreferences()) for uid in user_ids}

    def save_preferences(self, user_id: str, prefs: UserPreferences) -> UserPreferences:
        row = self._session.get(PlatformUserPreferencesRow, user_id)
        if row is None:
            row = PlatformUserPreferencesRow(user_id=user_id, preferences=prefs.model_dump())
            self._session.add(row)
        else:
            row.preferences = prefs.model_dump()
        self._session.flush()
        return prefs


def _row_to_user(row: PlatformUserRow) -> User:
    return User(
        id=row.id,
        email=row.email,
        name=row.name,
        created_at=row.created_at,
        picture=row.picture,
        phone=row.phone,
        baa_accepted_at=row.baa_accepted_at,
        baa_version=row.baa_version,
        legal_name=row.legal_name,
        is_platform_admin=row.is_platform_admin,
        status=row.status,
        mfa_enrolled_at=row.mfa_enrolled_at,
        provider_type=row.provider_type,
        security_guide_acknowledged_at=row.security_guide_acknowledged_at,
        security_guide_version=row.security_guide_version,
        onboarding_state=row.onboarding_state,
        chat_quality_review_opt_in=row.chat_quality_review_opt_in,
        chat_quality_review_opt_in_at=row.chat_quality_review_opt_in_at,
        chat_quality_review_opt_out_at=row.chat_quality_review_opt_out_at,
        session_notes_quality_review_opt_in=row.session_notes_quality_review_opt_in,
        session_notes_quality_review_opt_in_at=row.session_notes_quality_review_opt_in_at,
        session_notes_quality_review_opt_out_at=row.session_notes_quality_review_opt_out_at,
        quality_review_consent_prompted_at=row.quality_review_consent_prompted_at,
        profile_basics_completed_at=row.profile_basics_completed_at,
    )


def _user_to_row(user: User, row: PlatformUserRow) -> None:
    row.email = user.email
    row.name = user.name
    row.created_at = user.created_at
    row.picture = user.picture
    row.phone = user.phone
    row.baa_accepted_at = user.baa_accepted_at
    row.baa_version = user.baa_version
    row.legal_name = user.legal_name
    row.is_platform_admin = user.is_platform_admin
    row.status = user.status
    row.mfa_enrolled_at = user.mfa_enrolled_at
    row.provider_type = user.provider_type
    row.security_guide_acknowledged_at = user.security_guide_acknowledged_at
    row.security_guide_version = user.security_guide_version
    row.onboarding_state = user.onboarding_state
    row.chat_quality_review_opt_in = user.chat_quality_review_opt_in
    row.chat_quality_review_opt_in_at = user.chat_quality_review_opt_in_at
    row.chat_quality_review_opt_out_at = user.chat_quality_review_opt_out_at
    row.session_notes_quality_review_opt_in = user.session_notes_quality_review_opt_in
    row.session_notes_quality_review_opt_in_at = user.session_notes_quality_review_opt_in_at
    row.session_notes_quality_review_opt_out_at = user.session_notes_quality_review_opt_out_at
    row.quality_review_consent_prompted_at = user.quality_review_consent_prompted_at
    row.profile_basics_completed_at = user.profile_basics_completed_at
