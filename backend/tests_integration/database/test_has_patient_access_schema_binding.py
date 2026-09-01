# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``has_patient_access`` must answer about its own tenant, always.

The function gates the read policy on ``patient_documents``, ``notes``,
``chat_conversations``, ``medications``, ``outcome_measures``,
``diagnostic_assessments``, ``appointments`` and the prescribing tables —
most of the clinical surface. Each tenant schema holds its own copy, and
each copy is called schema-qualified from its own policies.

Its body named ``patient_clinicians`` unqualified, and it is
``LANGUAGE sql STABLE`` with no ``SET search_path`` and no
``SECURITY DEFINER``, so the grant table was resolved against the
CALLER's ``search_path`` rather than against the schema the function
lives in. Tenant A's function asked whichever ``patient_clinicians``
the caller happened to have on its path — so a grant in tenant B could
answer a question about tenant A.

Nothing in the tree can produce that state today: ``_VALID_SCHEMA_RE``
admits no comma or space, so the ``SET search_path`` interpolation
cannot be widened to two practice schemas; every ``SET search_path`` in
the codebase is the fixed ``{schema}, platform, public`` form; and there
is no ``SECURITY DEFINER`` function to borrow rights through. This is
hardening, not a live-escape fix.

It is worth doing anyway because the safety argument is entirely
negative — it rests on an exhaustive audit of every call site staying
true forever, across every future reporting query, operator tool and
two-schema migration helper. Binding the function to its own schema
replaces "nothing does this yet" with "this cannot mean that", which is
the kind of guarantee that survives people.

NOTE for whoever fixes this next: the textbook remedy — ``SET
search_path`` on the function — is WRONG here, and dangerously so.
``scripts/regen_tenant_template.py`` rewrites only dot-qualified
``practice.`` occurrences into ``__TENANT_SCHEMA__``. A pinned
``SET search_path TO practice, pg_catalog`` carries no trailing dot, so
it would survive verbatim into ``tenant_template.sql`` and point every
freshly-provisioned tenant's function at the ``practice`` schema —
turning this latent hazard into a live cross-tenant leak. Qualify the
table reference in the body instead; that the regex does rewrite.
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

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


def _make_tenant(engine: Engine, tag: str) -> str:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    schema = f"practice_test_hpa_{tag}_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    return schema


@pytest.fixture(scope="module")
def tenants(engine: Engine) -> Iterator[tuple[str, str]]:
    a, b = _make_tenant(engine, "a"), _make_tenant(engine, "b")
    yield a, b
    for schema in (a, b):
        with engine.connect() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.commit()


def _seed_patient(engine: Engine, schema: str, patient_id: str, clinician: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": clinician})
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, status, session_count, created_at, updated_at) "
                "VALUES (CAST(:id AS uuid), 'T', 'P', 't', 'p', 'active', 0, now(), now()) "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": patient_id},
        )


def _grant(engine: Engine, schema: str, patient_id: str, clinician: str) -> None:
    _seed_patient(engine, schema, patient_id, clinician)
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": clinician})
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, role, granted_by) "
                "VALUES (CAST(:p AS uuid), CAST(:u AS uuid), 'primary', CAST(:u AS uuid)) "
                "ON CONFLICT DO NOTHING"
            ),
            {"p": patient_id, "u": clinician},
        )


def _ask(engine: Engine, *, function_schema: str, path: str, patient: str, clinician: str) -> bool:
    """Call one tenant's function with some other tenant's search_path."""
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {path}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": clinician})
        return bool(
            conn.execute(
                text(f"SELECT {function_schema}.has_patient_access(CAST(:p AS uuid), :u)"),
                {"p": patient, "u": clinician},
            ).scalar_one()
        )


class TestTheGrantTableIsResolvedInTheFunctionsOwnSchema:
    def test_a_foreign_tenants_grant_does_not_answer_for_this_one(
        self, engine: Engine, tenants: tuple[str, str]
    ) -> None:
        """The escape, stated as the thing that must not happen.

        The clinician holds a treating grant in tenant B only. Tenant A's
        chart carries the same patient id — plausible, since a person can
        be a patient of two practices, and certain either way because the
        id is the caller's to choose. Asking tenant A's function with B on
        the search_path must still answer about tenant A, where there is
        no grant.
        """
        a, b = tenants
        clinician = str(uuid.uuid4())
        patient = str(uuid.uuid4())

        _grant(engine, b, patient, clinician)
        _seed_patient(engine, a, patient, clinician)

        crossed = _ask(engine, function_schema=a, path=b, patient=patient, clinician=clinician)

        assert crossed is False

    def test_the_honest_answer_is_still_yes_where_the_grant_lives(
        self, engine: Engine, tenants: tuple[str, str]
    ) -> None:
        """Binding the lookup must not break the lookup.

        Without this the test above passes just as well against a function
        that always returns false.
        """
        _a, b = tenants
        clinician = str(uuid.uuid4())
        patient = str(uuid.uuid4())

        _grant(engine, b, patient, clinician)

        assert _ask(engine, function_schema=b, path=b, patient=patient, clinician=clinician) is True

    def test_a_grant_in_this_tenant_answers_regardless_of_the_callers_path(
        self, engine: Engine, tenants: tuple[str, str]
    ) -> None:
        """The other direction of the same binding: the function should not
        MISS its own tenant's grant because the caller's path points
        elsewhere. A fix that merely made the lookup fail closed would
        pass the first test and fail this one."""
        a, b = tenants
        clinician = str(uuid.uuid4())
        patient = str(uuid.uuid4())

        _grant(engine, a, patient, clinician)

        assert _ask(engine, function_schema=a, path=b, patient=patient, clinician=clinician) is True
