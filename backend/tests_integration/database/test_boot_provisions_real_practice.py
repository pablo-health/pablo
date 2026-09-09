# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A fresh install runs on a real practice schema, not on the template.

The template schema is the one ``enable_rls_on_schema`` deliberately skips.
For as long as boot registered the live practice against that same schema,
the default install ran its charts with no row policies at all — and the
patient principal could not authenticate, because its fence requires a
``practice_*`` name.

These tests pin the shape that fixes it: the template stays a template, and
the deployment's own practice is a practice like any other.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
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
def booted_engine() -> Iterator[Engine]:
    """A database taken through the real boot path, on its own copy.

    ``ensure_schemas`` is the function under test, so it is called rather
    than imitated. It runs against a scratch database so a fresh install is
    genuinely fresh — the shared one has already been booted by every other
    suite.
    """
    from app.db.provisioning import ensure_schemas  # noqa: PLC0415

    scratch = f"pablo_boot_{uuid.uuid4().hex[:8]}"
    admin = create_engine(_DB_URL, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{scratch}"'))

    base, _, _ = _DB_URL.rpartition("/")
    eng = create_engine(f"{base}/{scratch}", pool_pre_ping=True)
    ensure_schemas(eng)
    yield eng
    eng.dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
    admin.dispose()


def _registered_schema(engine: Engine, practice_id: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT schema_name FROM platform.practices WHERE id = :i"),
            {"i": practice_id},
        ).scalar()


class TestFreshInstallProvisionsARealPractice:
    def test_the_registered_practice_is_not_the_template(self, booted_engine: Engine) -> None:
        from app.db import DEFAULT_PRACTICE_ID, DEFAULT_PRACTICE_SCHEMA  # noqa: PLC0415

        schema = _registered_schema(booted_engine, DEFAULT_PRACTICE_ID)
        assert schema is not None, "boot registered no practice at all"
        assert schema != DEFAULT_PRACTICE_SCHEMA
        assert schema.startswith(f"{DEFAULT_PRACTICE_SCHEMA}_")

    def test_no_practice_is_registered_against_the_template(self, booted_engine: Engine) -> None:
        from app.db import DEFAULT_PRACTICE_SCHEMA  # noqa: PLC0415

        with booted_engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM platform.practices WHERE schema_name = :s"),
                {"s": DEFAULT_PRACTICE_SCHEMA},
            ).scalar_one()
        assert count == 0, "the provisioning template is registered as a live practice"

    def test_the_practice_schema_exists_and_carries_policies(self, booted_engine: Engine) -> None:
        """RLS on, and policies actually present — the property the old shape lacked."""
        from app.db import DEFAULT_PRACTICE_OWN_SCHEMA  # noqa: PLC0415

        with booted_engine.connect() as conn:
            tables = conn.execute(
                text("SELECT count(*) FROM information_schema.tables WHERE table_schema = :s"),
                {"s": DEFAULT_PRACTICE_OWN_SCHEMA},
            ).scalar_one()
            forced = conn.execute(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                    "ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relkind = 'r' AND c.relrowsecurity"
                ),
                {"s": DEFAULT_PRACTICE_OWN_SCHEMA},
            ).scalar_one()
            policies = conn.execute(
                text("SELECT count(*) FROM pg_policies WHERE schemaname = :s"),
                {"s": DEFAULT_PRACTICE_OWN_SCHEMA},
            ).scalar_one()

        assert tables > 0, "the practice schema was never built"
        assert forced > 0, "no table in the live practice has row-level security enabled"
        assert policies > 0, "row-level security is enabled with no policies — deny-all"

    def test_every_rls_enabled_table_has_at_least_one_policy(self, booted_engine: Engine) -> None:
        """RLS with no policy is deny-all, which fails far from its cause."""
        from app.db import DEFAULT_PRACTICE_OWN_SCHEMA  # noqa: PLC0415

        with booted_engine.connect() as conn:
            naked = (
                conn.execute(
                    text(
                        "SELECT c.relname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :s AND c.relkind = 'r' AND c.relrowsecurity "
                        "AND NOT EXISTS ("
                        "  SELECT 1 FROM pg_policies p "
                        "  WHERE p.schemaname = :s AND p.tablename = c.relname"
                        ")"
                    ),
                    {"s": DEFAULT_PRACTICE_OWN_SCHEMA},
                )
                .scalars()
                .all()
            )
        assert not naked, f"RLS forced with no policy on: {sorted(naked)}"

    def test_the_patient_principal_fence_accepts_this_schema(self) -> None:
        """The fence was right; it was the schema that was wrong.

        It still earns its keep — it rejects the template, the platform
        schema, and public.
        """
        from app.auth.patient_context import _is_tenant_schema  # noqa: PLC0415
        from app.db import DEFAULT_PRACTICE_OWN_SCHEMA, DEFAULT_PRACTICE_SCHEMA  # noqa: PLC0415

        assert _is_tenant_schema(DEFAULT_PRACTICE_OWN_SCHEMA)
        assert not _is_tenant_schema(DEFAULT_PRACTICE_SCHEMA)
        assert not _is_tenant_schema("platform")
        assert not _is_tenant_schema("public")

    def test_boot_is_idempotent(self, booted_engine: Engine) -> None:
        """Every Cloud Run instance races through this on rollout."""
        from app.db import DEFAULT_PRACTICE_ID  # noqa: PLC0415
        from app.db.provisioning import ensure_schemas  # noqa: PLC0415

        before = _registered_schema(booted_engine, DEFAULT_PRACTICE_ID)
        ensure_schemas(booted_engine)
        assert _registered_schema(booted_engine, DEFAULT_PRACTICE_ID) == before
