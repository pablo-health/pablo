# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice learns which account owns it, and never learns it twice.

``platform.practices.owner_user_id`` is what ``owner_session`` in
``app.routes.patient_booking`` arms before it reads a diary, so a practice
that does not know its owner cannot offer a patient a time. These tests are
over the real registry because the rules that matter are in the statements:
``owner_user_id IS NULL`` is what makes the write one-time, and the email
test is what keeps it off the wrong account. Both are SQL, and neither is
visible to a test that stands in for the database.

The reconcile is database-wide by design — it is a deploy-time backfill, not
a per-row call — so the tests for it assert about their own practices rather
than about the count, and the rows they create are cleaned up either way.

Requires ``DATABASE_URL`` + ``DATABASE_BACKEND=postgres``. Run:
``make test-integration``.
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
def registry(engine: Engine) -> Iterator[_Registry]:
    """Rows of this test's own, dropped afterwards whatever happened."""
    reg = _Registry(engine)
    try:
        yield reg
    finally:
        reg.clean_up()


class _Registry:
    """A handful of practices and accounts, addressable by short names."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._stamp = uuid.uuid4().hex[:8]
        self._practices: list[str] = []
        self._users: list[str] = []

    def account(self, name: str) -> tuple[str, str]:
        """An account in ``platform.users``. Returns ``(user_id, email)``."""
        user_id = str(uuid.uuid4())
        email = f"{name}-{self._stamp}@example.test"
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform.users (id, email, name, created_at, status,"
                    " is_platform_admin, chat_quality_review_opt_in,"
                    " session_notes_quality_review_opt_in, inbox_quality_review_opt_in)"
                    " VALUES (CAST(:i AS uuid), :e, :n, now(), 'approved', false,"
                    " false, false, false)"
                ),
                {"i": user_id, "e": email, "n": name},
            )
        self._users.append(user_id)
        return user_id, email

    def practice(self, name: str, owner_email: str) -> str:
        """A practice registered under ``owner_email`` and owned by nobody yet."""
        practice_id = f"owner-test-{name}-{self._stamp}"
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform.practices (id, name, schema_name, owner_email,"
                    " owner_user_id, product, status, is_active, created_at)"
                    " VALUES (:i, :n, :s, :e, NULL, 'pablo', 'active', true, now())"
                ),
                {
                    "i": practice_id,
                    "n": f"Owner Test {name}",
                    "s": f"practice_owner_test_{name}_{self._stamp}",
                    "e": owner_email,
                },
            )
        self._practices.append(practice_id)
        return practice_id

    def owner_of(self, practice_id: str) -> str | None:
        with self._engine.connect() as conn:
            return conn.execute(
                # ``::text`` because the driver hands back a ``UUID`` object and
                # every caller here is comparing against the string form the
                # application layer deals in.
                text("SELECT owner_user_id::text FROM platform.practices WHERE id = :i"),
                {"i": practice_id},
            ).scalar_one()

    def clean_up(self) -> None:
        with self._engine.begin() as conn:
            for practice_id in self._practices:
                conn.execute(
                    text("DELETE FROM platform.practices WHERE id = :i"), {"i": practice_id}
                )
            for user_id in self._users:
                conn.execute(
                    text("DELETE FROM platform.users WHERE id = CAST(:i AS uuid)"), {"i": user_id}
                )


# --- the account signing in ------------------------------------------------


def test_the_registered_owner_signing_in_records_them(registry: _Registry) -> None:
    from app.db.practice_owner import record_owner_on_sign_in  # noqa: PLC0415

    owner_id, owner_email = registry.account("owner")
    practice_id = registry.practice("declared", owner_email)

    assert record_owner_on_sign_in(practice_id, owner_email, owner_id) is True
    assert registry.owner_of(practice_id) == owner_id


def test_signing_in_again_changes_nothing(registry: _Registry) -> None:
    """The second sign-in is the common case, and it must be inert."""
    from app.db.practice_owner import record_owner_on_sign_in  # noqa: PLC0415

    owner_id, owner_email = registry.account("owner")
    practice_id = registry.practice("declared", owner_email)

    assert record_owner_on_sign_in(practice_id, owner_email, owner_id) is True
    assert record_owner_on_sign_in(practice_id, owner_email, owner_id) is False
    assert registry.owner_of(practice_id) == owner_id


def test_another_account_never_becomes_the_owner(registry: _Registry) -> None:
    """A colleague signing into the practice does not take it over.

    Checked from an empty column, not from a filled one: "somebody else got
    there first" and "the rule reads the email" fail the same assertion if the
    owner is already recorded, and only the second is the property here.
    """
    from app.db.practice_owner import record_owner_on_sign_in  # noqa: PLC0415

    _, owner_email = registry.account("owner")
    colleague_id, colleague_email = registry.account("colleague")
    practice_id = registry.practice("declared", owner_email)

    assert record_owner_on_sign_in(practice_id, colleague_email, colleague_id) is False
    assert registry.owner_of(practice_id) is None


def test_the_email_test_ignores_case_and_padding(registry: _Registry) -> None:
    from app.db.practice_owner import record_owner_on_sign_in  # noqa: PLC0415

    owner_id, owner_email = registry.account("owner")
    practice_id = registry.practice("declared", f"  {owner_email.upper()}  ")

    assert record_owner_on_sign_in(practice_id, owner_email, owner_id) is True
    assert registry.owner_of(practice_id) == owner_id


def test_a_practice_registered_under_no_email_takes_its_first_account(
    registry: _Registry,
) -> None:
    """The deployment's own practice, which is registered before anyone exists.

    It has no email to match against, so the first account to sign into it is
    the answer — and the second account still is not, because the column is
    only ever written while it is empty.
    """
    from app.db.practice_owner import record_owner_on_sign_in  # noqa: PLC0415

    first_id, first_email = registry.account("first")
    second_id, second_email = registry.account("second")
    practice_id = registry.practice("undeclared", "")

    assert record_owner_on_sign_in(practice_id, first_email, first_id) is True
    assert record_owner_on_sign_in(practice_id, second_email, second_id) is False
    assert registry.owner_of(practice_id) == first_id


# --- the one-off reconcile -------------------------------------------------


def test_reconcile_fills_the_owner_from_the_registered_email(
    engine: Engine, registry: _Registry
) -> None:
    from app.db.practice_owner import reconcile_practice_owners  # noqa: PLC0415

    owner_id, owner_email = registry.account("owner")
    practice_id = registry.practice("legacy", owner_email)

    assert reconcile_practice_owners(engine) >= 1
    assert registry.owner_of(practice_id) == owner_id


def test_reconcile_leaves_a_practice_whose_email_has_no_account(
    engine: Engine, registry: _Registry
) -> None:
    """Nothing to record, so nothing is recorded.

    Refusing to offer times in a diary is recoverable; arming the wrong
    principal over one is not.
    """
    from app.db.practice_owner import reconcile_practice_owners  # noqa: PLC0415

    practice_id = registry.practice("stranger", f"nobody-{uuid.uuid4().hex[:8]}@example.test")

    reconcile_practice_owners(engine)
    assert registry.owner_of(practice_id) is None


def test_reconcile_leaves_a_practice_registered_under_no_email(
    engine: Engine, registry: _Registry
) -> None:
    from app.db.practice_owner import reconcile_practice_owners  # noqa: PLC0415

    practice_id = registry.practice("undeclared", "")

    reconcile_practice_owners(engine)
    assert registry.owner_of(practice_id) is None


def test_reconcile_is_idempotent_and_never_reassigns(engine: Engine, registry: _Registry) -> None:
    """A second run matches nothing, and an owner already recorded stands.

    The second half is the one that matters: the registered email is editable,
    so a reconcile that re-read it every run would move a live practice's
    principal underneath it.
    """
    from app.db.practice_owner import reconcile_practice_owners  # noqa: PLC0415

    owner_id, owner_email = registry.account("owner")
    other_id, _ = registry.account("other")
    practice_id = registry.practice("legacy", owner_email)

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE platform.practices SET owner_user_id = CAST(:u AS uuid) WHERE id = :i"),
            {"u": other_id, "i": practice_id},
        )

    reconcile_practice_owners(engine)
    assert registry.owner_of(practice_id) == other_id
    assert owner_id != other_id
