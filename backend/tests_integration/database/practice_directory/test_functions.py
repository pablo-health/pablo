# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Functions behavior."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

from .helpers import (
    _A,
    _B,
    _COLUMNS,
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


class TestTheFunction:
    def test_it_is_owned_by_the_directory_role_with_a_pinned_path(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT pg_get_userbyid(proowner) AS owner, prosecdef, proconfig "
                    "FROM pg_proc WHERE oid = to_regprocedure(:fn)"
                ),
                {"fn": f"{practice['schema']}.practice_client_directory()"},
            ).one()
        assert row.owner == _ROLE
        assert row.prosecdef is True
        assert row.proconfig == [f"search_path=pg_catalog, {practice['schema']}, pg_temp"]

    def test_it_returns_only_its_listed_columns(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        conn = _as(engine, practice["schema"], _A)
        try:
            result = conn.execute(text("SELECT * FROM practice_client_directory()"))
            assert list(result.keys()) == _COLUMNS
        finally:
            conn.close()

    def test_it_shows_every_live_chart_with_who_sees_it(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        conn = _as(engine, practice["schema"], _A)
        try:
            assert _directory(conn) == {
                practice["mine"]: [_A],
                practice["theirs"]: [_B],
                practice["shared_mine"]: [_A],
                practice["shared_theirs"]: [_B],
            }
        finally:
            conn.close()

    def test_it_returns_nothing_without_a_clinician(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        conn = _as(engine, practice["schema"], None)
        try:
            assert _directory(conn) == {}
        finally:
            conn.close()

    def test_it_reads_only_its_own_practice(self, engine: Engine, practice: dict[str, str]) -> None:
        other = _new_schema(engine, "directory_other")
        try:
            elsewhere = _chart(engine, other, _A, _Chart("Ada", "Lovelace"))
            conn = _as(engine, other, _A)
            try:
                assert _directory(conn) == {elsewhere: [_A]}
            finally:
                conn.close()
            conn = _as(engine, practice["schema"], _A)
            try:
                assert elsewhere not in _directory(conn)
            finally:
                conn.close()
        finally:
            _drop(engine, other)


class TestNothingElseWidens:
    def test_a_clinician_still_reads_only_their_own_charts(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        for me, mine, theirs in ((_A, "mine", "theirs"), (_B, "theirs", "mine")):
            conn = _as(engine, practice["schema"], me)
            try:
                seen = {str(r) for r in conn.execute(text("SELECT id FROM patients")).scalars()}
                assert practice[mine] in seen
                assert practice[theirs] not in seen
                grants = {
                    str(r)
                    for r in conn.execute(text("SELECT user_id FROM patient_clinicians")).scalars()
                }
                assert grants == {me}
            finally:
                conn.close()

    def test_the_directory_role_reaches_only_the_listed_columns(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        conn = _as(engine, practice["schema"], _A)
        try:
            conn.execute(text(f"SET ROLE {_ROLE}"))
            # The control: what the function reads is readable.
            names = conn.execute(text("SELECT first_name FROM patients")).scalars().all()
            assert "Grace" in names
            conn.execute(text("RESET ROLE"))
            conn.commit()
            for denied in (
                "SELECT diagnosis FROM patients",
                "SELECT role FROM patient_clinicians",
                "SELECT id FROM notes",
                # The claim source reads a visit's codes, never what was said.
                "SELECT title FROM appointments",
                "SELECT notes FROM appointments",
                "SELECT license_number FROM clinician_profiles",
                "SELECT name FROM appointment_types",
            ):
                conn.execute(text(f"SET ROLE {_ROLE}"))
                with pytest.raises(ProgrammingError, match="permission denied"):
                    conn.execute(text(denied))
                conn.rollback()
        finally:
            conn.close()

    def test_the_directory_role_reads_nothing_without_a_clinician(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        """The role's own policies make the function's armed-clinician check too."""
        for user_id, expected_rows in ((_A, True), (None, False)):
            conn = _as(engine, practice["schema"], user_id)
            try:
                conn.execute(text(f"SET ROLE {_ROLE}"))
                patients = conn.execute(text("SELECT id FROM patients")).all()
                grants = conn.execute(text("SELECT user_id FROM patient_clinicians")).all()
                assert bool(patients) is expected_rows
                assert bool(grants) is expected_rows
                conn.execute(text("RESET ROLE"))
            finally:
                conn.close()
