# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Test directory for single-practice conversion."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from app.db import (
    DEFAULT_PRACTICE_OWN_SCHEMA,
    DEFAULT_PRACTICE_SCHEMA,
)
from app.db.single_practice_migration import (
    migrate,
)
from sqlalchemy import text

from .helpers import _seed_patient

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)


if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def test_the_practice_directory_reaches_the_renamed_schema(old_shape: Engine) -> None:
    """The directory functions move with the rename and have to be re-pointed.

    They come from the template schema, which never had the directory role's
    grants or policies, and each carries a search path naming the schema it
    was created in. The migration gives the renamed schema its access itself;
    nothing else would.
    """
    engine = old_shape
    with engine.begin() as conn:
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, str(uuid.uuid4()))

    migrate(engine)

    with engine.connect() as conn:
        functions = conn.execute(
            text(
                "SELECT p.proname, pg_get_userbyid(p.proowner), p.proconfig FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = :s AND p.proname LIKE 'practice\\_%' ORDER BY 1"
            ),
            {"s": DEFAULT_PRACTICE_OWN_SCHEMA},
        ).all()
        policies = conn.execute(
            text(
                "SELECT tablename FROM pg_policies WHERE schemaname = :s "
                "AND policyname = 'rls_practice_directory_read' ORDER BY 1"
            ),
            {"s": DEFAULT_PRACTICE_OWN_SCHEMA},
        ).scalars()
        assert list(policies) == [
            "appointment_types",
            "appointments",
            "clinician_profiles",
            "patient_clinicians",
            "patient_coverage",
            "patients",
        ]
    path = [f"search_path=pg_catalog, {DEFAULT_PRACTICE_OWN_SCHEMA}, pg_temp"]
    assert [tuple(row) for row in functions] == [
        ("practice_claim_source", "pablo_practice_directory", path),
        ("practice_client_directory", "pablo_practice_directory", path),
        ("practice_outside_appointment", "pablo_practice_directory", path),
    ]
