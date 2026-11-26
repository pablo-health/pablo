# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``practice_claim_source``: a colleague's visit, as a claim names it, and nothing more."""

from __future__ import annotations

import os
import uuid
from dataclasses import fields
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

from .helpers import _A, _B, _ROLE, _as, _Chart, _chart, _drop, _new_schema

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

_NPI = "1234567893"
_TAXONOMY = "101YM0800X"
_FEE = 15000


@pytest.fixture(scope="module")
def visit(engine: Engine) -> Iterator[dict[str, str]]:
    """B's session with B's client: a type with a fee, B's profile, an active coverage."""
    schema = _new_schema(engine, "claim_source")
    patient_id = _chart(engine, schema, _B, _Chart("Grace", "Hopper"))
    gone_id = _chart(engine, schema, _B, _Chart("Gone", "Away", deleted=True))
    ids = {"schema": schema, "patient": patient_id}
    for key in ("appointment", "gone_appointment", "type", "payer", "coverage"):
        ids[key] = str(uuid.uuid4())
    conn = _as(engine, schema, _B)
    try:
        conn.execute(
            text(
                "INSERT INTO appointment_types (id, user_id, name, default_fee_cents) "
                "VALUES (CAST(:t AS uuid), :u, 'Intake', :fee)"
            ),
            {"t": ids["type"], "u": _B, "fee": _FEE},
        )
        conn.execute(
            text(
                "INSERT INTO clinician_profiles (user_id, practice_id, joined_at, npi_number, "
                "taxonomy_code) VALUES (:u, 'p', now(), :npi, :tax)"
            ),
            {"u": _B, "npi": _NPI, "tax": _TAXONOMY},
        )
        conn.execute(
            text(
                "INSERT INTO payers (id, name, payer_id, created_at, updated_at) "
                "VALUES (CAST(:p AS uuid), 'Acme Health', 'ACME1', now(), now())"
            ),
            {"p": ids["payer"]},
        )
        conn.execute(
            text(
                "INSERT INTO patient_coverage (id, patient_id, payer_id, member_id, active, "
                "created_at, updated_at) VALUES (CAST(:c AS uuid), CAST(:pid AS uuid), "
                "CAST(:p AS uuid), 'M-1', true, now(), now())"
            ),
            {"c": ids["coverage"], "pid": patient_id, "p": ids["payer"]},
        )
        for appointment, client, days in (
            (ids["appointment"], patient_id, 0),
            (ids["gone_appointment"], gone_id, 1),
        ):
            conn.execute(
                text(
                    "INSERT INTO appointments (id, user_id, patient_id, title, start_at, end_at, "
                    "duration_minutes, status, session_type, is_exception, reminder_24h_sent, "
                    "reminder_1h_sent, created_at, updated_at, appointment_type_id, "
                    "service_code, diagnosis_codes, notes) VALUES (CAST(:a AS uuid), :u, "
                    "CAST(:pid AS uuid), 'Session', now() + make_interval(days => :d), "
                    "now() + make_interval(days => :d, mins => 50), 50, "
                    "'completed', 'individual', false, false, false, now(), now(), "
                    "CAST(:t AS uuid), '90837', '[\"F41.1\"]'::jsonb, 'private note')"
                ),
                {"a": appointment, "u": _B, "pid": client, "t": ids["type"], "d": days},
            )
        conn.commit()
    finally:
        conn.close()
    yield ids
    _drop(engine, schema)


def _source(engine: Engine, schema: str, user: str | None, appointment: str) -> list[dict]:
    conn = _as(engine, schema, user)
    try:
        rows = conn.execute(
            text("SELECT * FROM practice_claim_source(CAST(:a AS uuid))"), {"a": appointment}
        )
        return [dict(row) for row in rows.mappings()]
    finally:
        conn.close()


def test_it_is_owned_by_the_directory_role_with_a_pinned_path(
    engine: Engine, visit: dict[str, str]
) -> None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT pg_get_userbyid(proowner) AS owner, prosecdef, proconfig "
                "FROM pg_proc WHERE oid = to_regprocedure(:fn)"
            ),
            {"fn": f"{visit['schema']}.practice_claim_source(uuid)"},
        ).one()
    assert row.owner == _ROLE
    assert row.prosecdef is True
    assert row.proconfig == [f"search_path=pg_catalog, {visit['schema']}, pg_temp"]


def test_it_returns_only_the_claim_fields(engine: Engine, visit: dict[str, str]) -> None:
    from app.billing_access import ClaimSource

    conn = _as(engine, visit["schema"], _A)
    try:
        keys = list(
            conn.execute(
                text("SELECT * FROM practice_claim_source(CAST(:a AS uuid))"),
                {"a": visit["appointment"]},
            ).keys()
        )
    finally:
        conn.close()
    assert keys == [f.name for f in fields(ClaimSource)]
    assert not {"notes", "email", "title", "transcript", "session_notes"} & set(keys)


def test_a_colleague_reads_the_visit_their_own_reads_cannot_see(
    engine: Engine, visit: dict[str, str]
) -> None:
    schema = visit["schema"]
    conn = _as(engine, schema, _A)
    try:
        # Under A's own policies none of B's rows are there.
        for table in (
            "appointments",
            "patient_coverage",
            "clinician_profiles",
            "appointment_types",
        ):
            count = conn.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608 — literals
            assert count.scalar() == 0, table
    finally:
        conn.close()

    [row] = _source(engine, schema, _A, visit["appointment"])
    assert str(row["clinician_id"]) == _B
    assert str(row["patient_id"]) == visit["patient"]
    assert (row["npi_number"], row["taxonomy_code"]) == (_NPI, _TAXONOMY)
    assert row["default_fee_cents"] == _FEE
    assert (str(row["coverage_id"]), row["member_id"]) == (visit["coverage"], "M-1")
    assert row["diagnosis_codes"] == ["F41.1"]
    assert (row["first_name"], row["last_name"]) == ("Grace", "Hopper")


def test_it_returns_nothing_without_a_clinician(engine: Engine, visit: dict[str, str]) -> None:
    assert _source(engine, visit["schema"], None, visit["appointment"]) == []


def test_it_returns_nothing_for_a_deleted_client(engine: Engine, visit: dict[str, str]) -> None:
    assert _source(engine, visit["schema"], _A, visit["gone_appointment"]) == []


def test_read_claim_source_types_the_row_for_someone_who_may_bill(
    engine: Engine, visit: dict[str, str]
) -> None:
    from app.billing_access import read_claim_source

    conn = _as(engine, visit["schema"], _A)
    session = Session(bind=conn)
    user, practice = object(), object()
    try:
        with patch("app.billing_access.can_bill", return_value=True):
            source = read_claim_source(session, practice, user, visit["appointment"])  # type: ignore[arg-type]
        with patch("app.billing_access.can_bill", return_value=False):
            refused = read_claim_source(session, practice, user, visit["appointment"])  # type: ignore[arg-type]
    finally:
        session.close()
        conn.close()
    assert refused is None
    assert source is not None
    assert (source.clinician_id, source.coverage_id) == (_B, visit["coverage"])
    assert source.appointment_type_id == visit["type"]
    assert source.service_code == "90837"
