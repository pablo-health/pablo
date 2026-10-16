# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A new sign-in whose email already belongs to another user is refused.

When a Firebase account is deleted and re-created, the next sign-in carries
a new uid with the same email. The new uid has no identity mapping, so the
auto-provision path tried to insert a second ``platform.users`` row for the
email and hit ``users_email_key`` — a 500 on every request, retried forever.

The answer is a 409, not a link: attaching a fresh uid to an existing user
because the emails match would hand that user's account to whoever controls
the new identity. These tests run against a real Postgres because the unique
constraint is the thing that used to fire.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)


@pytest.fixture
def existing_user(engine: Engine) -> Iterator[tuple[str, str, str]]:
    """A provisioned user: platform row, its identity, and its practice mapping."""
    from app.db import DEFAULT_PRACTICE_ID  # noqa: PLC0415
    from app.db.platform_models import (  # noqa: PLC0415
        EmailTenantMappingRow,
        PlatformUserRow,
        UserIdentityRow,
    )
    from app.utcnow import utc_now  # noqa: PLC0415

    user_id = str(uuid.uuid4())
    uid = f"fb{uuid.uuid4().hex[:26]}"
    email = f"recreated-{user_id[:8]}@example.invalid"
    now = utc_now()
    with Session(engine) as session:
        session.add(PlatformUserRow(id=user_id, email=email, name="Original", created_at=now))
        session.flush()
        session.add(
            UserIdentityRow(provider="firebase", subject_id=uid, user_id=user_id, linked_at=now)
        )
        session.add(
            EmailTenantMappingRow(
                email=email,
                tenant_id=DEFAULT_PRACTICE_ID,
                practice_id=DEFAULT_PRACTICE_ID,
                created_at=now,
            )
        )
        session.commit()
    try:
        yield user_id, uid, email
    finally:
        with Session(engine) as session:
            session.query(UserIdentityRow).filter_by(user_id=user_id).delete()
            session.query(EmailTenantMappingRow).filter_by(email=email).delete()
            session.query(PlatformUserRow).filter_by(id=user_id).delete()
            session.commit()


def _resolve(session: Session, decoded: dict[str, object]):  # type: ignore[no-untyped-def]
    from app.auth.service import _resolve_user  # noqa: PLC0415
    from app.repositories.postgres.allowlist import PostgresAllowlistRepository  # noqa: PLC0415
    from app.repositories.postgres.identity import PostgresIdentityRepository  # noqa: PLC0415
    from app.repositories.postgres.user import PostgresUserRepository  # noqa: PLC0415

    return _resolve_user(
        decoded,
        PostgresUserRepository(session),
        PostgresAllowlistRepository(session),
        PostgresIdentityRepository(session),
        None,
    )


def test_a_new_uid_with_a_taken_email_is_a_conflict_and_writes_nothing(
    engine: Engine, existing_user: tuple[str, str, str]
) -> None:
    from app.db.platform_models import PlatformUserRow, UserIdentityRow  # noqa: PLC0415

    user_id, old_uid, email = existing_user
    new_uid = f"fb{uuid.uuid4().hex[:26]}"

    with Session(engine) as session:
        with pytest.raises(HTTPException) as exc_info:
            _resolve(session, {"uid": new_uid, "email": email, "name": "Someone"})
        # Commit whatever the refused call left pending: a refusal must not
        # have staged a write for the request to commit afterwards.
        session.commit()

    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["error"]["code"] == "ACCOUNT_CONFLICT"

    with Session(engine) as session:
        users_with_email = session.execute(
            select(func.count()).select_from(PlatformUserRow).where(PlatformUserRow.email == email)
        ).scalar_one()
        assert users_with_email == 1
        # The new uid was not linked to anything — least of all the existing user.
        assert session.get(UserIdentityRow, ("firebase", new_uid)) is None
        # And the original sign-in is untouched.
        original = session.get(UserIdentityRow, ("firebase", old_uid))
        assert original is not None
        assert original.user_id == user_id


def test_the_original_uid_still_signs_in(
    engine: Engine, existing_user: tuple[str, str, str]
) -> None:
    """The conflict check must not catch the email's own user."""
    user_id, old_uid, email = existing_user
    with Session(engine) as session:
        user = _resolve(session, {"uid": old_uid, "email": email, "name": "Original"})
    assert user.id == user_id
