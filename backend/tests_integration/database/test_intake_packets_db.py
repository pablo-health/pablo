# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Intake packets against real PostgreSQL.

Three things only a database can answer, and the unit suite cannot.

**The default form exists the moment a practice does.** It is seeded on the
path that provisions a schema, not by a migration, so the only honest proof
is to provision a schema and look. A practice that signed up and found an
empty form builder would have nothing to send anybody.

**Two practices cannot see each other's forms.** These tables carry no
``patient_id`` and no ``user_id``, so there is no row policy to isolate them
— the schema is the boundary. That makes the isolation claim a claim about
``search_path``, and the way to check it is to provision two schemas and
read each from the other's session.

**The template matches the model.** Provisioning applies the captured
``tenant_template.sql``, never the alembic chain, so a table that exists in
the chain and not in the template is missing from every practice created
from here on. That failure surfaces far from its cause, as
``relation ... does not exist`` in whatever runs next.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, text

from . import scratch_db

#: backend/, which holds alembic.ini and both migration trees.
_BACKEND_DIR = Path(__file__).resolve().parents[2]

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

_PACKET_TABLES = (
    "intake_packet_templates",
    "intake_packet_versions",
    "intake_item_definitions",
)


@pytest.fixture(scope="module")
def two_practices() -> Iterator[tuple[Engine, str, str]]:
    """A scratch database with two freshly-provisioned practice schemas.

    Two rather than one because the isolation claim here is about the
    schema, and one schema cannot demonstrate a boundary.
    """
    from app.db.platform_bootstrap import bring_platform_to_head  # noqa: PLC0415
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    scratch = scratch_db.scratch_name("pablo_packets")
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


def _repo_on(engine: Engine, schema: str):
    """A session pointed at one practice schema, and a repository on it."""
    from app.repositories.postgres.intake_packet import (  # noqa: PLC0415
        PostgresIntakePacketRepository,
    )
    from sqlalchemy.orm import Session  # noqa: PLC0415

    session = Session(engine)
    session.execute(text(f"SET search_path = {schema}, platform, public"))
    return session, PostgresIntakePacketRepository(session)


class TestTheTemplateCarriesTheTables:
    def test_a_fresh_schema_has_all_three(self, two_practices: tuple[Engine, str, str]) -> None:
        """Provisioning applies the captured template, not the chain."""
        engine, schema, _ = two_practices
        with engine.connect() as conn:
            present = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = :s AND table_name = ANY(:names)"
                    ),
                    {"s": schema, "names": list(_PACKET_TABLES)},
                )
            }
        assert present == set(_PACKET_TABLES)

    def test_row_level_security_is_off_on_all_three(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        """Their boundary is the schema, so a row policy would be deny-all.

        ``enable_rls_on_schema`` force-enables RLS on any table carrying an
        ``id``, and refuses to leave one with no policy. These three are
        registered as not-row-scoped, which is what this asserts held.
        """
        engine, schema, _ = two_practices
        with engine.connect() as conn:
            forced = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT c.relname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :s AND c.relname = ANY(:names) "
                        "AND c.relrowsecurity"
                    ),
                    {"s": schema, "names": list(_PACKET_TABLES)},
                )
            }
        assert forced == set()


class TestTheDefaultForm:
    def test_a_fresh_practice_has_one_published_form(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        engine, schema, _ = two_practices
        session, repo = _repo_on(engine, schema)
        try:
            templates = repo.list_templates()
            assert len(templates) == 1
            assert templates[0]["name"] == "Intake"
            assert templates[0]["created_by"] is None

            versions = repo.list_versions(str(templates[0]["id"]))
            assert len(versions) == 1
            assert versions[0]["version"] == 1
            assert versions[0]["published_at"] is not None
        finally:
            session.close()

    def test_it_asks_the_four_questions_in_order(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        """The same four the fixed form asked, so intake does not change shape."""
        engine, schema, _ = two_practices
        session, repo = _repo_on(engine, schema)
        try:
            template = repo.list_templates()[0]
            version = repo.list_versions(str(template["id"]))[0]
            items = repo.list_items(str(version["id"]))

            assert [(i["key"], i["item_type"]) for i in items] == [
                ("demographics", "demographics"),
                ("reason", "reason"),
                ("phq9", "instrument"),
                ("gad7", "instrument"),
            ]
            assert items[2]["config"] == {"code": "phq9"}
            assert items[3]["config"] == {"code": "gad7"}
        finally:
            session.close()

    def test_it_publishes_cleanly_against_the_item_validator(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        """The seed ships published, so its items have to be publishable.

        A seed the validator would refuse is a form nobody could ever make a
        version 2 of, and nothing else would notice.
        """
        from app.intake.items import ItemDraft, stored_config, validate_item_list  # noqa: PLC0415

        engine, schema, _ = two_practices
        session, repo = _repo_on(engine, schema)
        try:
            template = repo.list_templates()[0]
            version = repo.list_versions(str(template["id"]))[0]
            validate_item_list(
                [
                    ItemDraft(
                        key=str(row["key"]),
                        item_type=str(row["item_type"]),
                        required=bool(row["required"]),
                        config=stored_config(row["config"]),
                    )
                    for row in repo.list_items(str(version["id"]))
                ]
            )
        finally:
            session.close()

    def test_re_provisioning_does_not_seed_a_second_copy(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        from app.db.intake_seed import seed_default_intake_packet  # noqa: PLC0415

        engine, schema, _ = two_practices
        seed_default_intake_packet(engine, schema)

        session, repo = _repo_on(engine, schema)
        try:
            assert len(repo.list_templates()) == 1
        finally:
            session.close()


class TestTwoPracticesCannotSeeEachOther:
    def test_a_form_created_in_one_is_invisible_in_the_other(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        from app.services.intake_packet_service import IntakePacketService  # noqa: PLC0415

        engine, first, second = two_practices
        author = str(uuid.uuid4())

        mine_session, mine_repo = _repo_on(engine, first)
        try:
            created = IntakePacketService(mine_repo).create_template("Only mine", author)
            mine_session.commit()
            template_id = str(created["id"])
        finally:
            mine_session.close()

        theirs_session, theirs_repo = _repo_on(engine, second)
        try:
            assert theirs_repo.get_template(template_id) is None
            assert "Only mine" not in [t["name"] for t in theirs_repo.list_templates()]
        finally:
            theirs_session.close()

    def test_a_version_id_from_another_practice_is_a_miss(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        """A version id is not a capability — it resolves in one schema only."""
        engine, first, second = two_practices

        mine_session, mine_repo = _repo_on(engine, first)
        try:
            template = mine_repo.list_templates()[0]
            version_id = str(mine_repo.list_versions(str(template["id"]))[0]["id"])
        finally:
            mine_session.close()

        theirs_session, theirs_repo = _repo_on(engine, second)
        try:
            assert theirs_repo.get_version(version_id) is None
            assert theirs_repo.list_items(version_id) == []
        finally:
            theirs_session.close()


class TestTheDatabaseEnforcesWhatTheCodeAssumes:
    def test_an_unknown_item_type_is_refused_by_the_column(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        """The CHECK is the backstop under the discriminated union."""
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        engine, schema, _ = two_practices
        session, repo = _repo_on(engine, schema)
        try:
            version_id = str(repo.list_versions(str(repo.list_templates()[0]["id"]))[0]["id"])
            with pytest.raises(IntegrityError):
                session.execute(
                    text(
                        "INSERT INTO intake_item_definitions "
                        "(id, version_id, key, position, item_type, required, config, "
                        "resign_on_new_version) VALUES "
                        "(:id, :v, 'mood', 99, 'mood_ring', TRUE, '{}'::jsonb, FALSE)"
                    ),
                    {"id": str(uuid.uuid4()), "v": version_id},
                )
        finally:
            session.rollback()
            session.close()

    def test_two_items_cannot_share_a_key_in_one_version(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        engine, schema, _ = two_practices
        session, repo = _repo_on(engine, schema)
        try:
            version_id = str(repo.list_versions(str(repo.list_templates()[0]["id"]))[0]["id"])
            with pytest.raises(IntegrityError):
                session.execute(
                    text(
                        "INSERT INTO intake_item_definitions "
                        "(id, version_id, key, position, item_type, required, config, "
                        "resign_on_new_version) VALUES "
                        "(:id, :v, 'reason', 99, 'reason', TRUE, '{}'::jsonb, FALSE)"
                    ),
                    {"id": str(uuid.uuid4()), "v": version_id},
                )
        finally:
            session.rollback()
            session.close()

    def test_deleting_a_template_takes_its_versions_and_items(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        """The cascades are declared; this is the check that they arrived."""
        from app.services.intake_packet_service import IntakePacketService  # noqa: PLC0415

        engine, schema, _ = two_practices
        session, repo = _repo_on(engine, schema)
        try:
            service = IntakePacketService(repo)
            template_id = str(service.create_template("Throwaway", str(uuid.uuid4()))["id"])
            version_id = str(service.list_versions(template_id)[0]["id"])
            from app.intake.items import ItemDraft  # noqa: PLC0415

            service.replace_items(version_id, [ItemDraft(key="reason", item_type="reason")])
            session.flush()

            session.execute(
                text("DELETE FROM intake_packet_templates WHERE id = :i"), {"i": template_id}
            )
            session.flush()

            assert repo.get_version(version_id) is None
            assert repo.list_items(version_id) == []
        finally:
            session.rollback()
            session.close()
