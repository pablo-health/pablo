# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A deployment's own practice-schema tables get row-level security from the templates it names.

``TENANT_RLS_EXTRA_TEMPLATES`` is the whole contract for such a table: named,
its template's policies are applied; not named, provisioning refuses the
practice rather than hand it out with a table that has no row-level security.

Runs as the ``pablo`` role (``NOSUPERUSER NOBYPASSRLS``, from the integration
conftest). Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from app.db import rls_runtime
from app.db.provisioning import (
    TEMPLATE_ROW_SECURITY_MARKER,
    create_practice_schema,
    reconcile_practice_rls,
)
from app.db.rls_apply import tenant_rls_differences
from app.settings import get_settings
from sqlalchemy import text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

_TABLE = "deployment_widgets"
_POLICY = (
    f"CREATE POLICY rls_widget_owner ON __TENANT_SCHEMA__.{_TABLE} "
    "USING ((user_id = current_setting('app.current_user_id'::text, true)));"
)
_EXTRA_TEMPLATE = f"""\
CREATE TABLE __TENANT_SCHEMA__.{_TABLE} (
    id text,
    user_id text NOT NULL
);

{TEMPLATE_ROW_SECURITY_MARKER}
ALTER TABLE ONLY __TENANT_SCHEMA__.{_TABLE} FORCE ROW LEVEL SECURITY;

ALTER TABLE __TENANT_SCHEMA__.{_TABLE} ENABLE ROW LEVEL SECURITY;

{_POLICY}
"""


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    """Each test reads the setting it sets, and leaves nothing cached for the next."""
    get_settings.cache_clear()
    rls_runtime._desired_for.cache_clear()
    yield
    get_settings.cache_clear()
    rls_runtime._desired_for.cache_clear()


@pytest.fixture
def practice_with_extra_table(engine: Engine) -> Iterator[str]:
    """A provisioned practice holding one table the engine's template does not create."""
    schema = f"practice_test_extra_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE TABLE "{schema}".{_TABLE} (id text, user_id text NOT NULL)'))
    yield schema
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def test_strict_refuses_a_table_no_template_creates(
    engine: Engine, practice_with_extra_table: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TENANT_RLS_EXTRA_TEMPLATES", raising=False)

    with pytest.raises(RuntimeError, match=_TABLE):
        reconcile_practice_rls(engine, practice_with_extra_table)


def test_a_named_template_gives_its_table_the_policy(
    engine: Engine,
    practice_with_extra_table: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    extra = tmp_path / "deployment_template.sql"
    extra.write_text(_EXTRA_TEMPLATE)
    monkeypatch.setenv("TENANT_RLS_EXTRA_TEMPLATES", f" {extra} , ")
    schema = practice_with_extra_table

    reconcile_practice_rls(engine, schema)

    with engine.connect() as conn:
        flags = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_catalog.pg_class "
                "WHERE oid = to_regclass(:t)"
            ),
            {"t": f'"{schema}".{_TABLE}'},
        ).one()
        policies = conn.execute(
            text(
                "SELECT policyname FROM pg_catalog.pg_policies "
                "WHERE schemaname = :s AND tablename = :t"
            ),
            {"s": schema, "t": _TABLE},
        ).scalars()
        assert tuple(flags) == (True, True)
        assert set(policies) == {"rls_widget_owner"}
    with Session(engine) as session:
        assert tenant_rls_differences(session, schema, rls_runtime.desired_rls()) == {}


def test_a_named_template_that_does_not_exist_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "not_shipped.sql"
    monkeypatch.setenv("TENANT_RLS_EXTRA_TEMPLATES", str(missing))

    with pytest.raises(RuntimeError, match=r"not_shipped\.sql"):
        rls_runtime.desired_rls()
