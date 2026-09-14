# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``platform.panel_applications`` — the first platform table with row security.

The table moved out of the practice schemas because the surface that reads it
most is an operator working across every practice at once; as a per-tenant
table that made an operator board a catalog scan plus a union with one branch
per schema.

**The isolation was supposed to survive that move, and this is where that claim
is tested rather than asserted.** The route tests exercise the surface; what
stops a clinician reading a colleague's applications is a policy on this table,
and a policy is only ever proven by the database that enforces it.

Bug classes:

* **RLS not actually enabled on the new table.** The move would then have
  quietly downgraded a database-enforced boundary to a ``WHERE`` clause, and
  every existing test would still pass.
* **``ENABLE`` without ``FORCE``.** The app connects as the table's owner, and
  an owner is exempt from its own policies unless forced. This is the specific
  way "we enabled RLS" ends up meaning nothing here.
* **A policy that reads the wrong GUC**, so either everybody sees everything or
  nobody sees anything.
* **Writes escaping the policy.** ``USING`` governs reads; without
  ``WITH CHECK`` a clinician could insert a row owned by somebody else.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _db_url or "placeholder" in _db_url,
    reason="needs a real Postgres (integration lane)",
)

_CLINICIAN_A = "11111111-1111-4111-8111-111111111111"
_CLINICIAN_B = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


class _ArmedSession:
    """A session armed as one clinician, the way a request opens one.

    No tenant schema is bound: the table is platform-scoped now, so the only
    thing that decides what this session can see is the GUC.
    """

    def __init__(self, engine: Engine, user_id: str) -> None:
        from app.db import (  # noqa: PLC0415
            _current_user_id,
            arm_current_user_id,
        )
        from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

        self._uid_token = _current_user_id.set(user_id)
        self.session = OrmSession(bind=engine)
        self.session.execute(text("SET search_path = platform, public"))
        arm_current_user_id(self.session, user_id)

    def close(self) -> None:
        from app.db import _current_user_id  # noqa: PLC0415

        self.session.close()
        _current_user_id.reset(self._uid_token)


def _insert(session, user_id: str, *, application_id: str | None = None) -> str:
    now = datetime.now(UTC)
    app_id = application_id or str(uuid.uuid4())
    session.execute(
        text(
            "INSERT INTO platform.panel_applications "
            "(id, user_id, practice_id, payer_id, status, action_owner, created_at, updated_at) "
            "VALUES (:id, :uid, :pid, :payer, 'researching', 'pablo', :now, :now)"
        ),
        {
            "id": app_id,
            "uid": user_id,
            "pid": "practice-under-test",
            "payer": str(uuid.uuid4()),
            "now": now,
        },
    )
    return app_id


def test_the_table_has_row_security_enabled_and_forced(engine: Engine) -> None:
    """ENABLE alone would be decorative: the app connects as the owner."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'platform' AND c.relname = 'panel_applications'"
            )
        ).one()

    assert row.relrowsecurity is True
    assert row.relforcerowsecurity is True


def test_one_clinician_cannot_read_anothers_application(engine: Engine) -> None:
    """The property the move had to preserve, proven rather than assumed."""
    owner = _ArmedSession(engine, _CLINICIAN_A)
    try:
        _insert(owner.session, _CLINICIAN_A)
        owner.session.commit()
    finally:
        owner.close()

    other = _ArmedSession(engine, _CLINICIAN_B)
    try:
        visible = other.session.execute(
            text("SELECT count(*) FROM platform.panel_applications WHERE user_id = :uid"),
            {"uid": _CLINICIAN_A},
        ).scalar()
    finally:
        other.close()

    assert visible == 0


def test_a_clinician_sees_her_own(engine: Engine) -> None:
    """The control. Without it, "sees zero" could just mean RLS refuses everyone."""
    session = _ArmedSession(engine, _CLINICIAN_B)
    try:
        _insert(session.session, _CLINICIAN_B)
        session.session.commit()
        mine = session.session.execute(
            text("SELECT count(*) FROM platform.panel_applications WHERE user_id = :uid"),
            {"uid": _CLINICIAN_B},
        ).scalar()
    finally:
        session.close()

    assert mine == 1


def test_a_clinician_cannot_write_a_row_owned_by_somebody_else(engine: Engine) -> None:
    """WITH CHECK, not just USING.

    Forging a colleague's application is the worst case here: it would put an
    application on somebody's record that they never asked for, and the
    concierge operator would act on it.
    """
    session = _ArmedSession(engine, _CLINICIAN_B)
    try:
        # The insert itself is what the policy refuses, so the commit is not
        # inside the ``raises`` block — if it ever needed to be, the refusal
        # would be coming from something other than WITH CHECK.
        with pytest.raises(DBAPIError):
            _insert(session.session, _CLINICIAN_A)
    finally:
        session.session.rollback()
        session.close()


def test_an_unarmed_session_sees_nothing(engine: Engine) -> None:
    """Fail-closed.

    A connection with no principal armed — a stray script, a job that forgot —
    must read as "no access", never as "all access". ``current_setting(...,
    true)`` returns NULL there, and NULL never equals a user id.
    """
    with engine.connect() as conn:
        conn.execute(text("SET search_path = platform, public"))
        visible = conn.execute(text("SELECT count(*) FROM platform.panel_applications")).scalar()

    assert visible == 0
