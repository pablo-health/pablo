# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Provisioned and template-only practice fixtures.

App imports stay inside fixtures: the parent configures Postgres after loading
conftests, and importing the app earlier caches settings without its URL.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.db.rls_apply import DesiredTable
    from app.db.rls_state import LiveRls
    from sqlalchemy.engine import Engine


@pytest.fixture(scope="module")
def desired() -> dict[str, DesiredTable]:
    from app.db.rls_apply import desired_from_sql  # noqa: PLC0415

    from .helpers import _TEMPLATE  # noqa: PLC0415

    return desired_from_sql(_TEMPLATE.read_text())


@pytest.fixture(scope="module")
def reference(engine: Engine) -> Iterator[str]:
    """A practice provisioned today and never touched: the state to return to."""
    from .helpers import _drop_schema, _new_schema  # noqa: PLC0415

    schema = _new_schema(engine, "apply_fresh")
    yield schema
    _drop_schema(engine, schema)


@pytest.fixture(scope="module")
def from_the_file(engine: Engine) -> Iterator[str]:
    """A schema built from the template's SQL and nothing else.

    No reconcile runs over it, so its policies are what Postgres makes of the
    file's own statements. For two tables that is not the text the file holds
    (see ``rls_apply``'s docstring), which is why this, and not ``reference``,
    is what a schema rebuilt by the applier is compared with.
    """
    from app.db.provisioning import (  # noqa: PLC0415
        TEMPLATE_ROW_SECURITY_MARKER,
        apply_template_sql,
    )

    from .helpers import _TEMPLATE, _drop_schema  # noqa: PLC0415

    schema = f"practice_test_apply_file_{uuid.uuid4().hex[:8]}"
    body, _, row_security = _TEMPLATE.read_text().partition(TEMPLATE_ROW_SECURITY_MARKER)
    apply_template_sql(engine, schema, body)
    apply_template_sql(engine, schema, row_security)
    yield schema
    _drop_schema(engine, schema)


@pytest.fixture
def practice(engine: Engine) -> Iterator[str]:
    """A practice with two patients, both treated by one clinician.

    Function-scoped: every test damages its schema.
    """
    from .helpers import _TREATING, _drop_schema, _new_schema  # noqa: PLC0415

    schema = _new_schema(engine, "apply")
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _TREATING})
        for first, last in (("Ada", "Lovelace"), ("Grace", "Hopper")):
            patient_id = str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, "
                    "first_name_lower, last_name_lower, status, session_count, "
                    "created_at, updated_at) "
                    "VALUES (CAST(:pid AS uuid), :first, :last, lower(:first), "
                    "lower(:last), 'active', 0, now(), now())"
                ),
                {"pid": patient_id, "first": first, "last": last},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:pid AS uuid), :u, :u)"
                ),
                {"pid": patient_id, "u": _TREATING},
            )
    yield schema
    _drop_schema(engine, schema)


@pytest.fixture(scope="module")
def settled(engine: Engine, desired: dict[str, DesiredTable]) -> Iterator[tuple[str, LiveRls]]:
    """A schema built from the file and settled by one pass, and what it then holds.

    Built from the file, not provisioned, so that a policy put back from the
    file reads the same as the one that was there before.
    """
    from app.db.provisioning import (  # noqa: PLC0415
        TEMPLATE_ROW_SECURITY_MARKER,
        apply_template_sql,
    )
    from app.db.rls_state import read_live_rls  # noqa: PLC0415

    from .helpers import _TEMPLATE, _apply, _drop_schema  # noqa: PLC0415

    schema = f"practice_test_apply_all_{uuid.uuid4().hex[:8]}"
    body, _, row_security = _TEMPLATE.read_text().partition(TEMPLATE_ROW_SECURITY_MARKER)
    apply_template_sql(engine, schema, body)
    apply_template_sql(engine, schema, row_security)
    _apply(engine, schema, desired, leave=())
    with OrmSession(engine) as session:
        holds = read_live_rls(session, schema)
    yield schema, holds
    _drop_schema(engine, schema)
