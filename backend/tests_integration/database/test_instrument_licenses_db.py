# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Instrument licence attestations against real PostgreSQL.

Four things only a database can answer, and the unit suite cannot.

**The table exists the moment a practice does.** Provisioning applies the
captured ``tenant_template.sql``, never the alembic chain, so a table that
exists in the chain and not in the template is missing from every practice
created from here on. That failure surfaces far from its cause, as
``relation ... does not exist`` in whatever runs next.

**Row-level security is deliberately off on it.** ``enable_rls_on_schema``
force-enables RLS on any table carrying an ``id`` and refuses to leave one
with no policy; ``instrument_license_attestations`` is registered as
not-row-scoped, and this is what asserts that registration held. A
regression here does not read as a misconfiguration — it reads as a
deny-all, and a deny-all here reads as a practice that has recorded no
permission and is refused every restricted measure it is entitled to ask.

**Two practices cannot see each other's licences.** The table carries no
``patient_id`` and no ``user_id``, so there is no row policy to isolate it.
The boundary is the schema, which makes the isolation claim a claim about
``search_path``, and the way to check it is to provision two schemas and
read each from the other's session.

**One permission in force per instrument, enforced by the database.** The
service withdraws the row it is replacing before writing the next one, but
two screens can attest at the same instant and no amount of checking in
Python arbitrates that. The partial unique index does.
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

#: Use-restricted measures from the registry; the service refuses to record
#: permission for anything else, which is itself the point. One per test that
#: writes, because the practice schemas are module-scoped and a test that
#: depends on what the one before it left behind is a test that passes alone
#: and fails in a reordered run.
RESTRICTED = "cssrs"
RESTRICTED_SECOND = "epds"
RESTRICTED_THIRD = "dast10"
RESTRICTED_FOURTH = "asrs"


@pytest.fixture(scope="module")
def two_practices() -> Iterator[tuple[Engine, str, str]]:
    """A scratch database with two freshly-provisioned practice schemas.

    Two rather than one because the isolation claim here is about the
    schema, and one schema cannot demonstrate a boundary.
    """
    from app.db.platform_bootstrap import bring_platform_to_head  # noqa: PLC0415
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    scratch = scratch_db.scratch_name("pablo_licenses")
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


@pytest.fixture
def fresh_practice(two_practices: tuple[Engine, str, str]) -> tuple[Engine, str]:
    """A practice schema nothing else in this module has written to.

    The two above are module-scoped and shared, so "a fresh practice has
    recorded nothing" cannot be asked of either once anything has run. This
    one is provisioned per test that needs the claim.
    """
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    engine, _, _ = two_practices
    schema = f"practice_{uuid.uuid4().hex[:12]}"
    create_practice_schema(engine, schema)
    return engine, schema


def _service_on(engine: Engine, schema: str):
    """A session pointed at one practice schema, and a service on it.

    ``search_path`` is set on the connection this session is holding, so a
    test that commits and then keeps reading through the same session is
    reading over whichever connection the pool hands back next — which may
    not be the one that was pointed anywhere. Commit at the end, or open a
    second session for the read.
    """
    from app.repositories.postgres.instrument_license import (  # noqa: PLC0415
        PostgresInstrumentLicenseRepository,
    )
    from app.services.instrument_license_service import (  # noqa: PLC0415
        InstrumentLicenseService,
    )
    from sqlalchemy.orm import Session  # noqa: PLC0415

    session = Session(engine)
    session.execute(text(f"SET search_path = {schema}, platform, public"))
    return session, InstrumentLicenseService(PostgresInstrumentLicenseRepository(session))


class TestTheTemplateCarriesTheTable:
    def test_a_fresh_schema_has_it(self, two_practices: tuple[Engine, str, str]) -> None:
        """Provisioning applies the captured template, not the chain."""
        engine, schema, _ = two_practices
        with engine.connect() as conn:
            found = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_name = "
                    "'instrument_license_attestations'"
                ),
                {"s": schema},
            ).first()
        assert found is not None

    def test_row_level_security_is_off_on_it(self, two_practices: tuple[Engine, str, str]) -> None:
        """Its boundary is the schema, so a row policy would be deny-all.

        A deny-all here would not look like a misconfiguration. It would
        look like a practice that has recorded no permission, and be
        refused every restricted measure it is entitled to ask.
        """
        engine, schema, _ = two_practices
        with engine.connect() as conn:
            forced = conn.execute(
                text(
                    "SELECT c.relrowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relname = "
                    "'instrument_license_attestations'"
                ),
                {"s": schema},
            ).scalar_one()
        assert forced is False

    def test_a_fresh_practice_has_recorded_nothing(
        self, fresh_practice: tuple[Engine, str]
    ) -> None:
        """Permission is the practice's to claim. Nothing claims it for them."""
        engine, schema = fresh_practice
        session, service = _service_on(engine, schema)
        try:
            assert service.list_active() == []
            assert service.attested_codes() == frozenset()
        finally:
            session.close()


class TestTheDatabaseEnforcesOnePermissionInForce:
    def test_a_second_active_row_for_one_instrument_is_refused(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        """Two screens, one instant: the index is the only thing that arbitrates."""
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        engine, schema, _ = two_practices
        session, service = _service_on(engine, schema)
        try:
            service.attest(RESTRICTED, str(uuid.uuid4()))

            with pytest.raises(IntegrityError):
                session.execute(
                    text(
                        "INSERT INTO instrument_license_attestations "
                        "(id, instrument_code, attested_by, attested_at) "
                        "VALUES (:id, :code, :by, NOW())"
                    ),
                    {"id": str(uuid.uuid4()), "code": RESTRICTED, "by": str(uuid.uuid4())},
                )
        finally:
            session.rollback()
            session.close()

    def test_recording_it_again_leaves_exactly_one_in_force(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        """The service withdraws before it writes, so the index is satisfied."""
        engine, _, schema = two_practices
        author = str(uuid.uuid4())
        session, service = _service_on(engine, schema)
        try:
            first = service.attest(RESTRICTED_SECOND, author, license_reference="old")
            second = service.attest(RESTRICTED_SECOND, author, license_reference="new")

            assert second["id"] != first["id"]
            in_force = [
                row
                for row in service.list_active()
                if row["instrument_code"] == RESTRICTED_SECOND
            ]
            assert [row["license_reference"] for row in in_force] == ["new"]
            session.commit()
        finally:
            session.close()

    def test_a_withdrawn_row_is_kept(self, two_practices: tuple[Engine, str, str]) -> None:
        """Who said what and when is the record; withdrawing is not a delete."""
        engine, _, schema = two_practices
        author = str(uuid.uuid4())
        session, service = _service_on(engine, schema)
        try:
            service.attest(RESTRICTED_THIRD, author)
            service.attest(RESTRICTED_THIRD, author)

            rows = session.execute(
                text(
                    "SELECT count(*) FROM instrument_license_attestations "
                    "WHERE instrument_code = :code AND revoked_at IS NOT NULL"
                ),
                {"code": RESTRICTED_THIRD},
            ).scalar_one()
            assert rows == 1
            session.commit()
        finally:
            session.close()


class TestTwoPracticesCannotSeeEachOther:
    def test_permission_recorded_in_one_is_invisible_in_the_other(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        engine, first, second = two_practices
        mine_session, mine = _service_on(engine, first)
        try:
            mine.attest(RESTRICTED_FOURTH, str(uuid.uuid4()), license_reference="mine only")
            mine_session.commit()
        finally:
            mine_session.close()

        theirs_session, theirs = _service_on(engine, second)
        try:
            assert theirs.active_for_code(RESTRICTED_FOURTH) is None
            assert "mine only" not in [row["license_reference"] for row in theirs.list_active()]
        finally:
            theirs_session.close()


class TestWithdrawingAgainstRealStorage:
    def test_it_takes_the_instrument_out_of_the_set_in_force(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        engine, _, second = two_practices
        session, service = _service_on(engine, second)
        try:
            service.attest(RESTRICTED, str(uuid.uuid4()))
            assert RESTRICTED in service.attested_codes()

            service.revoke(RESTRICTED)

            assert RESTRICTED not in service.attested_codes()
            assert service.active_for_code(RESTRICTED) is None
            session.commit()
        finally:
            session.close()

    def test_withdrawing_nothing_answers_nothing(
        self, two_practices: tuple[Engine, str, str]
    ) -> None:
        """Nothing has ever been recorded for this one in either schema."""
        engine, first, _ = two_practices
        session, service = _service_on(engine, first)
        try:
            assert service.revoke("mdq") is None
        finally:
            session.close()
