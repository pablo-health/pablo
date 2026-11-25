# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Revisions behavior."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

from .catalog import (
    _config,
    _directory_policies,
    _owner,
    _walk,
)
from .helpers import (
    _A,
    _PARENT_REVISION,
    _ROLE,
    _as,
    _Chart,
    _chart,
    _directory,
    _drop,
    _new_schema,
)

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)


class TestRewalkingTheRevision:
    """The migrator no longer owns the function once the access pass runs."""

    def test_a_schema_built_from_the_template_upgrades_again_after_a_stamp_back(
        self, engine: Engine
    ) -> None:
        schema = _new_schema(engine, "directory_rewalk")
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(f"UPDATE {schema}.alembic_version SET version_num = :r"),  # noqa: S608
                    {"r": _PARENT_REVISION},
                )
            _walk(engine, schema, "upgrade", "head")

            assert _owner(engine, schema) == _ROLE
            assert _config(engine, schema) == [f"search_path=pg_catalog, {schema}, pg_temp"]
            mine = _chart(engine, schema, _A, _Chart("Ada", "Lovelace"))
            conn = _as(engine, schema, _A)
            try:
                assert _directory(conn) == {mine: [_A]}
            finally:
                conn.close()
        finally:
            _drop(engine, schema)

    def test_it_downgrades_and_upgrades_again_as_the_app_role(self, engine: Engine) -> None:
        schema = _new_schema(engine, "directory_down")
        try:
            _walk(engine, schema, "downgrade", _PARENT_REVISION)
            assert _owner(engine, schema) is None
            assert _directory_policies(engine, schema) == set()

            _walk(engine, schema, "upgrade", "head")
            assert _owner(engine, schema) == _ROLE
            assert _config(engine, schema) == [f"search_path=pg_catalog, {schema}, pg_temp"]
            assert _directory_policies(engine, schema) == {
                "patients",
                "patient_clinicians",
                "appointments",
            }
        finally:
            _drop(engine, schema)
