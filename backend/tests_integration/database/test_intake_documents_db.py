# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Consent documents against real PostgreSQL.

Four things only a database can answer, and the unit suite cannot.

**The table exists the moment a practice does.** Provisioning applies the
captured ``tenant_template.sql``, never the alembic chain, so a table that
exists in the chain and not in the template is missing from every practice
created from here on. That failure surfaces far from its cause, as
``relation ... does not exist`` in whatever runs next.

**Row-level security is deliberately off on it.** ``enable_rls_on_schema``
force-enables RLS on any table carrying an ``id`` and refuses to leave one
with no policy; ``intake_documents`` is registered as not-row-scoped, and
this is what asserts that registration held. A regression here does not
read as a misconfiguration — it reads as a deny-all.

**Two practices cannot see each other's documents.** The table carries no
``patient_id`` and no ``user_id``, so there is no row policy to isolate it.
The boundary is the schema, which makes the isolation claim a claim about
``search_path``, and the way to check it is to provision two schemas and
read each from the other's session.

**One draft per document, enforced by the database.** The service returns
an existing draft rather than stacking a second, but a practice can reach
"start a new version" from more than one screen at the same instant, and no
amount of checking in Python arbitrates that. The partial unique index
does.
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


@pytest.fixture(scope="module")
def two_practices() -> Iterator[tuple[Engine, str, str]]:
    """A scratch database with two freshly-provisioned practice schemas.

    Two rather than one because the isolation claim here is about the
    schema, and one schema cannot demonstrate a boundary.
    """
    from app.db.platform_bootstrap import bring_platform_to_head  # noqa: PLC0415
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    scratch = scratch_db.scratch_name("pablo_documents")
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


def _service_on(engine: Engine, schema: str):
    """A session pointed at one practice schema, and a service on it."""
    from app.repositories.postgres.intake_document import (  # noqa: PLC0415
        PostgresIntakeDocumentRepository,
    )
    from app.services.intake_document_service import IntakeDocumentService  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    session = Session(engine)
    session.execute(text(f"SET search_path = {schema}, platform, public"))
    return session, IntakeDocumentService(PostgresIntakeDocumentRepository(session))


class TestTheTemplateCarriesTheTable:
    def test_a_fresh_schema_has_it(self, two_practices: tuple[Engine, str, str]) -> None:
        """Provisioning applies the captured template, not the chain."""
        engine, schema, _ = two_practices
        with engine.connect() as conn:
            found = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_name = 'intake_documents'"
                ),
                {"s": schema},
            ).first()
        assert found is not None

    def test_row_level_security_is_off_on_it(self, two_practices: tuple[Engine, str, str]) -> None:
        """Its boundary is the schema, so a row policy would be deny-all."""
        engine, schema, _ = two_practices
        with engine.connect() as conn:
            forced = conn.execute(
                text(
                    "SELECT c.relrowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relname = 'intake_documents'"
                ),
                {"s": schema},
            ).scalar_one()
        assert forced is False

    def test_a_fresh_practice_starts_with_no_documents(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        """Consent text is a practice's own words. Nothing seeds it for them."""
        engine, schema, _ = two_practices
        session, service = _service_on(engine, schema)
        try:
            assert service.list_documents() == []
        finally:
            session.close()


class TestTheDatabaseEnforcesOneDraft:
    def test_a_second_draft_of_one_document_is_refused(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        """Two screens, one instant: the index is the only thing that arbitrates."""
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        engine, schema, _ = two_practices
        author = str(uuid.uuid4())
        session, service = _service_on(engine, schema)
        try:
            created = service.create(title="Consent", body_markdown="Words.")
            published = service.publish(str(created["id"]), author)
            service.new_version(str(published["id"]))

            # What a second concurrent "start a new version" would attempt.
            with pytest.raises(IntegrityError):
                session.execute(
                    text(
                        "INSERT INTO intake_documents "
                        "(id, document_key, title, body_markdown, version, digest, "
                        " requires_signature, signer_roles, created_at) "
                        "VALUES (:id, :key, 'Consent', 'Words.', 3, :digest, "
                        " TRUE, '[\"patient\"]'::jsonb, NOW())"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "key": str(created["document_key"]),
                        "digest": str(created["digest"]),
                    },
                )
        finally:
            session.rollback()
            session.close()

    def test_two_documents_may_each_have_a_draft(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        """The index is per document, not per practice."""
        engine, _, schema = two_practices
        session, service = _service_on(engine, schema)
        try:
            service.create(title="Consent", body_markdown="One.")
            service.create(title="Telehealth", body_markdown="Two.")
            assert len(service.list_documents()) == 2
        finally:
            session.close()


class TestTwoPracticesCannotSeeEachOther:
    def test_a_document_written_in_one_is_invisible_in_the_other(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        engine, first, second = two_practices
        mine_session, mine = _service_on(engine, first)
        try:
            created = mine.create(title="Only mine", body_markdown="Private words.")
            mine_session.commit()
            document_id = str(created["id"])
            document_key = str(created["document_key"])
        finally:
            mine_session.close()

        theirs_session, theirs = _service_on(engine, second)
        try:
            assert theirs.get(document_id) is None
            assert theirs.published_for_key(document_key) is None
            assert "Only mine" not in [d["title"] for d in theirs.list_documents()]
        finally:
            theirs_session.close()


class TestVersionsAgainstRealStorage:
    def test_publishing_a_second_version_leaves_the_first_readable(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        """A signature points at a version, so that version has to stay put."""
        engine, first, _ = two_practices
        author = str(uuid.uuid4())
        session, service = _service_on(engine, first)
        try:
            created = service.create(title="Consent", body_markdown="Original words.")
            published = service.publish(str(created["id"]), author)
            draft = service.new_version(str(published["id"]))
            service.update_draft(str(draft["id"]), body_markdown="Newer words.")
            service.publish(str(draft["id"]), author)

            still = service.get(str(published["id"]))
            assert still is not None
            assert still["body_markdown"] == "Original words."
            assert still["digest"] == published["digest"]

            live = service.published_for_key(str(published["document_key"]))
            assert live is not None
            assert live["version"] == 2
        finally:
            session.close()

    def test_the_digest_survives_a_round_trip(self, two_practices: tuple[Engine, str, str]) -> None:
        """Stored and read back, it is still the digest of the stored words."""
        from app.intake.documents import content_digest  # noqa: PLC0415

        engine, first, _ = two_practices
        session, service = _service_on(engine, first)
        try:
            created = service.create(
                title="Consent", body_markdown="# Heading\n\nSome **words** here."
            )

            read_back = service.get(str(created["id"]))
            assert read_back is not None
            assert read_back["digest"] == content_digest(str(read_back["body_markdown"]))
        finally:
            session.close()
