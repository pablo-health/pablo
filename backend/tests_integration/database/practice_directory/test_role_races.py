# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Role races behavior."""

from __future__ import annotations

import os
import threading
import time
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
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


def _race(engine: Engine, role: str, first: str) -> BaseException | None:
    """Run the revision for ``role`` while another session's ``first`` is in flight.

    The role is cluster-wide, so two databases migrating on one server at the
    same moment (parallel test workers, say) both find it missing and both
    create it. Here session A creates it and holds its transaction open; session
    B runs the revision, gets past the existence check and waits on the
    catalog's unique index; then A commits. Returns what B raised, if anything.
    """
    sql = _platform_revision().ENSURE_ROLE_SQL.replace(_ROLE, role)
    failure: list[BaseException] = []
    with engine.connect() as a, engine.connect() as b:
        b.execute(text("SET lock_timeout = '20s'"))
        b.commit()
        b_pid = b.execute(text("SELECT pg_backend_pid()")).scalar()
        b.commit()

        a.execute(text(first))

        def run_b() -> None:
            try:
                b.execute(text(sql))
                b.commit()
            except DBAPIError as exc:
                failure.append(exc)
                b.rollback()

        thread = threading.Thread(target=run_b)
        thread.start()
        deadline = time.monotonic() + 10
        waiting = False
        while not waiting and thread.is_alive() and time.monotonic() < deadline:
            waiting = bool(
                a.execute(
                    text(
                        "SELECT 1 FROM pg_stat_activity "
                        "WHERE pid = :pid AND wait_event_type = 'Lock'"
                    ),
                    {"pid": b_pid},
                ).scalar()
            )
            if not waiting:
                time.sleep(0.05)
        a.commit()
        thread.join(30)
        assert not thread.is_alive()
        assert waiting, f"session B never waited on A: {failure}"
    return failure[0] if failure else None


class TestTwoMigrationsAtOnce:
    """Two migrations on one server must both pass, whichever creates the role first."""

    def test_the_role_created_by_another_session_mid_check_is_not_an_error(
        self, engine: Engine, throwaway_role: str
    ) -> None:
        """Before, the second CREATE ROLE raised UniqueViolation and the migration stopped."""
        failure = _race(engine, throwaway_role, f"CREATE ROLE {throwaway_role} NOLOGIN NOBYPASSRLS")
        assert failure is None, failure
        # And it went on to the grant: held, settable, not inherited.
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT pg_has_role(current_user, :r, 'SET') AS can_set, "
                    "pg_has_role(current_user, :r, 'USAGE') AS inherits"
                ),
                {"r": throwaway_role},
            ).one()
        assert (row.can_set, row.inherits) == (True, False)
