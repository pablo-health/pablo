# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Practice-defined note types against real PostgreSQL.

What only a database can answer:

**The table exists the moment a practice does.** Provisioning applies the
captured ``tenant_template.sql``, never the alembic chain, so a table missing
from the template is missing from every practice provisioned from here on.

**Row-level security is deliberately off on it.** ``practice_note_types`` is
registered not-row-scoped; if that registration slipped it would ship
force-RLS'd with no policy, which reads as a practice that has defined no
note types at all.

**Two practices cannot see each other's types.** The boundary is the schema,
so the way to check it is to provision two and read each from the other.

**A version number is taken once.** The ``(key, version)`` constraint is
what makes two saves racing on one key unable to both land as the same
version.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, text

from . import scratch_db

#: backend/, which holds alembic.ini and both migration trees.
_BACKEND_DIR = Path(__file__).resolve().parents[2]

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.repositories.postgres.practice_note_type import PostgresPracticeNoteTypeRepository
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

NOW = datetime(2026, 9, 23, tzinfo=UTC)

SPEC = {
    "label": "Interview Coach",
    "sections": [{"key": "s", "label": "S", "fields": [{"key": "f", "label": "F"}]}],
}


@pytest.fixture(scope="module")
def two_practices() -> Iterator[tuple[Engine, str, str]]:
    """A scratch database with two freshly-provisioned practice schemas."""
    from app.db.platform_bootstrap import bring_platform_to_head  # noqa: PLC0415
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    scratch = scratch_db.scratch_name("pablo_note_types")
    admin = create_engine(_DB_URL, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    scratch_db.create(admin, scratch)

    eng = create_engine(scratch_db.swap_database(_DB_URL, scratch), pool_pre_ping=True)
    bring_platform_to_head(eng, str(_BACKEND_DIR / "alembic.ini"))

    first = f"practice_{uuid.uuid4().hex[:12]}"
    second = f"practice_{uuid.uuid4().hex[:12]}"
    create_practice_schema(eng, first)
    create_practice_schema(eng, second)

    yield eng, first, second

    eng.dispose()
    scratch_db.drop(admin, scratch)
    admin.dispose()


def _repo_on(engine: Engine, schema: str) -> tuple[Session, PostgresPracticeNoteTypeRepository]:
    from app.repositories.postgres.practice_note_type import (  # noqa: PLC0415
        PostgresPracticeNoteTypeRepository,
    )
    from sqlalchemy.orm import Session  # noqa: PLC0415

    session = Session(engine)
    session.execute(text(f"SET search_path = {schema}, platform, public"))
    return session, PostgresPracticeNoteTypeRepository(session)


class TestTheTemplateCarriesTheTable:
    def test_a_fresh_schema_has_it(self, two_practices: tuple[Engine, str, str]) -> None:
        engine, schema, _ = two_practices
        with engine.connect() as conn:
            found = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_name = 'practice_note_types'"
                ),
                {"s": schema},
            ).first()
        assert found is not None

    def test_row_level_security_is_off_on_it(self, two_practices: tuple[Engine, str, str]) -> None:
        engine, schema, _ = two_practices
        with engine.connect() as conn:
            forced = conn.execute(
                text(
                    "SELECT c.relrowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relname = 'practice_note_types'"
                ),
                {"s": schema},
            ).scalar_one()
        assert forced is False

    def test_notes_and_appointments_carry_the_new_columns(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        engine, schema, _ = two_practices
        with engine.connect() as conn:
            columns = set(
                conn.execute(
                    text(
                        "SELECT table_name || '.' || column_name "
                        "FROM information_schema.columns WHERE table_schema = :s"
                    ),
                    {"s": schema},
                ).scalars()
            )
        assert {
            "notes.note_type_version",
            "notes.note_inputs",
            "appointments.note_inputs",
        } <= columns


class TestVersions:
    def test_saves_version_and_retire(self, two_practices: tuple[Engine, str, str]) -> None:
        engine, schema, _ = two_practices
        session, repo = _repo_on(engine, schema)
        author = str(uuid.uuid4())
        try:
            first = repo.add_version("custom.versions", SPEC, author, NOW)
            second = repo.add_version(
                "custom.versions", {**SPEC, "label": "v2"}, author, NOW
            )
            assert (first.version, second.version) == (1, 2)
            assert repo.get("custom.versions").definition["label"] == "v2"
            assert repo.get("custom.versions", 1).definition["label"] == "Interview Coach"

            retired = repo.retire("custom.versions", NOW)
            assert retired is not None
            assert retired.version == 2
            assert retired.retired_at is not None
            session.commit()
        finally:
            session.close()

    def test_a_version_number_is_taken_once(self, two_practices: tuple[Engine, str, str]) -> None:
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        engine, _, schema = two_practices
        session, repo = _repo_on(engine, schema)
        try:
            repo.add_version("custom.race", SPEC, str(uuid.uuid4()), NOW)
            with pytest.raises(IntegrityError):
                session.execute(
                    text(
                        "INSERT INTO practice_note_types "
                        "(id, key, version, definition, created_by, created_at) "
                        "VALUES (:id, 'custom.race', 1, '{}', :by, NOW())"
                    ),
                    {"id": str(uuid.uuid4()), "by": str(uuid.uuid4())},
                )
        finally:
            session.rollback()
            session.close()


class TestTwoPracticesCannotSeeEachOther:
    def test_a_type_saved_in_one_is_invisible_in_the_other(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        engine, first, second = two_practices
        mine_session, mine = _repo_on(engine, first)
        try:
            mine.add_version("custom.mine_only", SPEC, str(uuid.uuid4()), NOW)
            mine_session.commit()
        finally:
            mine_session.close()

        theirs_session, theirs = _repo_on(engine, second)
        try:
            assert theirs.get("custom.mine_only") is None
            assert "custom.mine_only" not in [s.key for s in theirs.list_latest()]
        finally:
            theirs_session.close()
