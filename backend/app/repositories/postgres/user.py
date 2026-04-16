# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL user repository — reads/writes from platform.users."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...db.platform_models import PlatformUserPreferencesRow, PlatformUserRow
from ...models import User, UserPreferences
from ..user import UserRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresUserRepository(UserRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: str) -> User | None:
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
        rows = self._session.query(PlatformUserRow).all()
        return [_row_to_user(r) for r in rows]

    def get_preferences(self, user_id: str) -> UserPreferences:
        row = self._session.get(PlatformUserPreferencesRow, user_id)
        if row is None:
            return UserPreferences()
        return UserPreferences(**row.preferences)

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
        baa_accepted_at=row.baa_accepted_at,
        baa_version=row.baa_version,
        baa_legal_name=row.baa_legal_name,
        baa_license_number=row.baa_license_number,
        baa_license_state=row.baa_license_state,
        baa_practice_name=row.baa_practice_name,
        baa_business_address=row.baa_business_address,
        baa_full_text=row.baa_full_text,
        is_platform_admin=row.is_platform_admin,
        status=row.status,
        mfa_enrolled_at=row.mfa_enrolled_at,
    )


def _user_to_row(user: User, row: PlatformUserRow) -> None:
    row.email = user.email
    row.name = user.name
    row.created_at = user.created_at
    row.picture = user.picture
    row.baa_accepted_at = user.baa_accepted_at
    row.baa_version = user.baa_version
    row.baa_legal_name = user.baa_legal_name
    row.baa_license_number = user.baa_license_number
    row.baa_license_state = user.baa_license_state
    row.baa_practice_name = user.baa_practice_name
    row.baa_business_address = user.baa_business_address
    row.baa_full_text = user.baa_full_text
    row.is_platform_admin = user.is_platform_admin
    row.status = user.status
    row.mfa_enrolled_at = user.mfa_enrolled_at
