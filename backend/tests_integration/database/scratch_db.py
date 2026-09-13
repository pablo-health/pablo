# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Creating and dropping a throwaway database, without needing to be superuser.

Two fixtures want a database of their own: the boot test, which needs a genuinely
fresh install to run ``ensure_schemas`` against, and the alembic idempotency
test, which needs an empty one to migrate. Both were dropping it by terminating
whatever was still connected — ``DROP DATABASE ... WITH (FORCE)`` in one,
``pg_terminate_backend`` in the other.

Terminating a backend needs superuser or ``pg_signal_backend``, and the CI role
is deliberately neither: it is NOBYPASSRLS and unprivileged, because that is the
posture the RLS suites are proving. So when a connection outlived the engine
that opened it, teardown failed with ``InsufficientPrivilege`` and took a
green suite down with it — 594 passing tests and one failed cleanup.

The fix is to stop needing the privilege. Dispose the engine, wait for the
connections to that database to actually go, and drop it normally. Postgres
closes a backend asynchronously, so "dispose returned" and "the server has
noticed" are not the same instant, and the wait is what bridges them.

A scratch database that outlives the run is not worth failing over. In CI the
whole server is thrown away; locally it is one row in ``\\l`` with an obvious
name. So the last resort here is a warning, not an error: a cleanup step must
not be able to fail a suite whose tests all passed.
"""

from __future__ import annotations

import time
import uuid
import warnings
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

#: How long to wait for a disposed pool's connections to leave the server.
#: Generous because the cost of being wrong is an unnecessary failure, and a
#: teardown that takes an extra second costs nothing anybody notices.
_DRAIN_TIMEOUT_SECONDS = 10.0
_DRAIN_POLL_SECONDS = 0.1


def scratch_name(prefix: str) -> str:
    """A unique name, so two runs on one server never collide."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def swap_database(url: str, name: str) -> str:
    """The same connection URL, pointed at a different database."""
    base, _, _ = url.rpartition("/")
    return f"{base}/{name}"


def create(admin: Engine, name: str) -> None:
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))


def _connections_to(admin: Engine, name: str) -> int:
    with admin.connect() as conn:
        return (
            conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname = :db AND pid <> pg_backend_pid()"
                ),
                {"db": name},
            ).scalar()
            or 0
        )


def _wait_until_idle(admin: Engine, name: str) -> bool:
    """Wait for the server to notice the connections are gone."""
    deadline = time.monotonic() + _DRAIN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            if _connections_to(admin, name) == 0:
                return True
        except SQLAlchemyError:
            # Cannot read the view: fall through to trying the drop anyway,
            # which is no worse than what we would have done regardless.
            return True
        time.sleep(_DRAIN_POLL_SECONDS)
    return False


def drop(admin: Engine, name: str) -> None:
    """Drop a scratch database, quietly, without terminating anybody.

    Never raises. The caller is a fixture teardown, and the whole point of this
    module is that cleanup cannot fail a passing suite.
    """
    drained = _wait_until_idle(admin, name)
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        return
    except SQLAlchemyError as exc:
        warnings.warn(
            f"could not drop scratch database {name!r}"
            f"{'' if drained else ' (connections were still attached)'}: {exc}."
            " Leaving it behind rather than failing the suite.",
            stacklevel=2,
        )
