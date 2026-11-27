# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Template behavior."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

from .catalog import (
    _directory_access,
)
from .helpers import (
    _ROLE,
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


class TestTheTemplateCarriesTheAccess:
    """Provisioning applies the template and runs nothing else for the directory."""

    def test_a_new_practice_has_all_of_it_and_the_access_pass_finds_nothing_to_change(
        self, engine: Engine
    ) -> None:
        from app.db.practice_directory import (
            APPOINTMENT_COLUMNS,
            GRANT_COLUMNS,
            PATIENT_COLUMNS,
            apply_practice_directory_access,
        )
        from app.db.practice_directory_definitions import (
            CLAIM_APPOINTMENT_COLUMNS,
            CLAIM_APPOINTMENT_TYPE_COLUMNS,
            CLAIM_CLINICIAN_COLUMNS,
            CLAIM_COVERAGE_COLUMNS,
            CLAIM_PATIENT_COLUMNS,
        )

        schema = _new_schema(engine, "dir_template")
        try:
            provisioned = _directory_access(engine, schema)

            path = [f"search_path=pg_catalog, {schema}, pg_temp"]
            assert provisioned["functions"] == [
                ("practice_claim_source", _ROLE, path),
                ("practice_client_directory", _ROLE, path),
                ("practice_enrollment_status", _ROLE, path),
                ("practice_outside_appointment", _ROLE, path),
            ]
            assert provisioned["columns"] == sorted(
                {
                    (table, column, "SELECT")
                    for table, columns in (
                        ("appointments", APPOINTMENT_COLUMNS + CLAIM_APPOINTMENT_COLUMNS),
                        ("patient_clinicians", GRANT_COLUMNS),
                        ("patients", PATIENT_COLUMNS + CLAIM_PATIENT_COLUMNS),
                        ("patient_coverage", CLAIM_COVERAGE_COLUMNS),
                        ("appointment_types", CLAIM_APPOINTMENT_TYPE_COLUMNS),
                        ("clinician_profiles", CLAIM_CLINICIAN_COLUMNS),
                    )
                    for column in columns
                }
            )
            # It may use the schema, and it kept none of the right to create in it.
            assert provisioned["schema"] == (True, False)
            policies = provisioned["policies"]
            assert isinstance(policies, list)
            assert [(table, cmd, roles) for table, cmd, roles, _ in policies] == [
                ("appointment_types", "SELECT", [_ROLE]),
                ("appointments", "SELECT", [_ROLE]),
                ("clinician_profiles", "SELECT", [_ROLE]),
                ("patient_clinicians", "SELECT", [_ROLE]),
                ("patient_coverage", "SELECT", [_ROLE]),
                ("patients", "SELECT", [_ROLE]),
            ]

            # The code that does the same work, run over it: nothing differs.
            with engine.begin() as conn:
                apply_practice_directory_access(conn, schema)
            assert _directory_access(engine, schema) == provisioned
        finally:
            _drop(engine, schema)
