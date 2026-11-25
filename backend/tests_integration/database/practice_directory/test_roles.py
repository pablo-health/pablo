# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Roles behavior."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import DBAPIError

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

from .helpers import (
    _ROLE,
    _platform_revision,
)

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)


class TestTheRoles:
    def test_the_app_role_does_not_bypass_rls(self, engine: Engine) -> None:
        """If this fails, every isolation assertion here is meaningless."""
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).one()
        assert (row.rolsuper, row.rolbypassrls) == (False, False)

    def test_the_directory_role_cannot_log_in_or_bypass_rls(self, engine: Engine) -> None:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT rolcanlogin, rolbypassrls, rolsuper FROM pg_roles WHERE rolname = :r"),
                {"r": _ROLE},
            ).one()
        assert (row.rolcanlogin, row.rolbypassrls, row.rolsuper) == (False, False, False)

    def test_the_app_role_can_hand_it_ownership_but_inherits_nothing(self, engine: Engine) -> None:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT pg_has_role(current_user, :r, 'SET') AS can_set, "
                    "pg_has_role(current_user, :r, 'USAGE') AS inherits"
                ),
                {"r": _ROLE},
            ).one()
        assert row.can_set is True
        assert row.inherits is False


class TestTheMigrationFailsLoudly:
    def test_a_user_who_cannot_be_granted_the_role_is_told_how_to_fix_it(
        self, engine: Engine
    ) -> None:
        sql = _platform_revision().ENSURE_ROLE_SQL
        user = f"no_createrole_{uuid.uuid4().hex[:8]}"
        password = uuid.uuid4().hex
        with engine.connect() as conn:
            conn.execute(text(f"CREATE ROLE {user} LOGIN NOCREATEROLE PASSWORD '{password}'"))
            conn.commit()
        limited = create_engine(
            make_url(_DB_URL).set(username=user, password=password), pool_pre_ping=True
        )
        try:
            with limited.connect() as conn, pytest.raises(DBAPIError) as raised:
                conn.execute(text(sql))
            message = str(raised.value)
            assert "cannot be granted pablo_practice_directory" in message
            assert "docs/SELF_HOSTING_HIPAA_GUIDE.md, Database roles" in message
        finally:
            limited.dispose()
            with engine.connect() as conn:
                conn.execute(text(f"DROP ROLE {user}"))
                conn.commit()

    def test_it_is_a_no_op_for_the_user_that_already_holds_it(self, engine: Engine) -> None:
        with engine.connect() as conn:
            conn.execute(text(_platform_revision().ENSURE_ROLE_SQL))
            conn.commit()

    def test_a_membership_that_inherits_is_granted_again_without_inheriting(
        self, engine: Engine
    ) -> None:
        """A hand-run grant, or createrole_self_grant, may have left INHERIT TRUE."""
        inherits = text("SELECT pg_has_role(current_user, :r, 'USAGE')")
        with engine.connect() as conn:
            try:
                conn.execute(text(f"GRANT {_ROLE} TO current_user WITH INHERIT TRUE, SET TRUE"))
                conn.commit()
                assert conn.execute(inherits, {"r": _ROLE}).scalar() is True

                conn.execute(text(_platform_revision().ENSURE_ROLE_SQL))
                conn.commit()

                assert conn.execute(inherits, {"r": _ROLE}).scalar() is False
            finally:
                conn.rollback()
                conn.execute(text(f"GRANT {_ROLE} TO current_user WITH INHERIT FALSE, SET TRUE"))
                conn.commit()
