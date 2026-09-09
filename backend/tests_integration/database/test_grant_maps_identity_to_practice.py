# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Granting access and mapping the identity are one operation.

Resolution runs on the login path for every user, so an email that is
allowed but unmapped is an account that authenticates and then resolves to
no practice — an empty account rather than a visible misconfiguration.
These tests are what stop the two halves drifting apart.

See ``docs/architecture/identity-to-practice-resolution.md``.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
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


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    from app.db import PLATFORM_SCHEMA  # noqa: PLC0415

    with Session(engine) as s:
        s.execute(text(f"SET search_path = {PLATFORM_SCHEMA}, public"))
        yield s
        s.rollback()


def _repo(session: Session):  # type: ignore[no-untyped-def]
    from app.repositories.postgres.allowlist import PostgresAllowlistRepository  # noqa: PLC0415

    return PostgresAllowlistRepository(session)


def _mapping_for(session: Session, email: str):  # type: ignore[no-untyped-def]
    from app.db.platform_models import EmailTenantMappingRow  # noqa: PLC0415

    return session.get(EmailTenantMappingRow, email)


class TestGrantingAlsoMaps:
    def test_a_granted_email_resolves_to_the_practice_it_was_granted_into(
        self, session: Session
    ) -> None:
        email = f"grantee-{uuid.uuid4().hex[:8]}@example.com"
        practice = f"practice-{uuid.uuid4().hex[:8]}"

        _repo(session).add(email, "admin-user", practice_id=practice)

        mapping = _mapping_for(session, email)
        assert mapping is not None, "granting access left the email unmapped"
        assert mapping.practice_id == practice
        assert mapping.tenant_id == practice

    def test_the_grant_row_carries_the_practice_too(self, session: Session) -> None:
        """Both registries answer the same question the same way.

        ``allowed_emails.practice_id`` used to be left NULL by this path,
        which is how an email ended up allowed with nothing saying where.
        """
        from app.db.platform_models import PlatformAllowedEmailRow  # noqa: PLC0415

        email = f"grantee-{uuid.uuid4().hex[:8]}@example.com"
        practice = f"practice-{uuid.uuid4().hex[:8]}"

        _repo(session).add(email, "admin-user", practice_id=practice)

        row = session.get(PlatformAllowedEmailRow, email)
        assert row is not None
        assert row.practice_id == practice

    def test_email_is_normalized_in_both_places(self, session: Session) -> None:
        """A mixed-case invitation must not resolve only when typed the same way."""
        raw = f"MixedCase-{uuid.uuid4().hex[:8]}@Example.COM"
        practice = f"practice-{uuid.uuid4().hex[:8]}"

        _repo(session).add(raw, "admin-user", practice_id=practice)

        assert _mapping_for(session, raw.lower()) is not None
        assert _repo(session).is_allowed(raw.upper())

    def test_regranting_into_a_different_practice_moves_the_mapping(self, session: Session) -> None:
        """Re-inviting must not leave the old answer behind.

        A stale mapping would send the user into a practice the current
        grant no longer names.
        """
        email = f"grantee-{uuid.uuid4().hex[:8]}@example.com"
        first = f"practice-{uuid.uuid4().hex[:8]}"
        second = f"practice-{uuid.uuid4().hex[:8]}"

        repo = _repo(session)
        repo.add(email, "admin-user", practice_id=first)
        repo.add(email, "admin-user", practice_id=second)

        mapping = _mapping_for(session, email)
        assert mapping is not None
        assert mapping.practice_id == second
        assert mapping.tenant_id == second

    def test_revoking_retires_the_mapping(self, session: Session) -> None:
        """A mapping outliving its grant resolves an identity into a practice it may not enter."""
        email = f"grantee-{uuid.uuid4().hex[:8]}@example.com"
        practice = f"practice-{uuid.uuid4().hex[:8]}"

        repo = _repo(session)
        repo.add(email, "admin-user", practice_id=practice)
        assert _mapping_for(session, email) is not None  # control

        assert repo.remove(email) is True

        assert _mapping_for(session, email) is None, "the mapping outlived the grant"
        assert repo.is_allowed(email) is False

    def test_removing_an_unknown_email_is_not_an_error(self, session: Session) -> None:
        assert _repo(session).remove(f"nobody-{uuid.uuid4().hex[:8]}@example.com") is False
