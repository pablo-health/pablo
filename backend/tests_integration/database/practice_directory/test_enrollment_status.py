# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``practice_enrollment_status``: a colleague's panel status here, and no other practice's."""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
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


def _participation(engine: Engine, practice: str, payer: str, status: str) -> None:
    """B's row for ``payer`` in ``practice``, written as B, as the app writes it."""
    conn = _as(engine, practice, _B)
    try:
        conn.execute(
            text(
                "INSERT INTO platform.payer_participations (id, user_id, practice_id, payer_id, "
                "status, provider_id_with_payer, created_at, updated_at) VALUES (:id, :u, :pr, "
                "CAST(:p AS uuid), :s, 'PRV-1', now(), now())"
            ),
            {"id": str(uuid.uuid4()), "u": _B, "pr": practice, "p": payer, "s": status},
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def visit(engine: Engine) -> Iterator[dict[str, str]]:
    """B's session here: B is credentialed with one payer, and in network elsewhere."""
    schema = _new_schema(engine, "enrollment")
    elsewhere = _new_schema(engine, "enrollment_other")
    patient_id = _chart(engine, schema, _B, _Chart("Grace", "Hopper"))
    ids = {
        "schema": schema,
        "elsewhere": elsewhere,
        "appointment": str(uuid.uuid4()),
        "here": str(uuid.uuid4()),
        "only_elsewhere": str(uuid.uuid4()),
    }
    conn = _as(engine, schema, _B)
    try:
        conn.execute(
            text(
                "INSERT INTO appointments (id, user_id, patient_id, title, start_at, end_at, "
                "duration_minutes, status, session_type, is_exception, reminder_24h_sent, "
                "reminder_1h_sent, created_at, updated_at) VALUES (CAST(:a AS uuid), :u, "
                "CAST(:pid AS uuid), 'Session', now(), now() + interval '50 minutes', 50, "
                "'completed', 'individual', false, false, false, now(), now())"
            ),
            {"a": ids["appointment"], "u": _B, "pid": patient_id},
        )
        conn.commit()
    finally:
        conn.close()
    _participation(engine, schema, ids["here"], "credentialed")
    _participation(engine, elsewhere, ids["here"], "in_network")
    _participation(engine, elsewhere, ids["only_elsewhere"], "in_network")
    yield ids
    conn = _as(engine, schema, _B)
    try:
        conn.execute(
            text("DELETE FROM platform.payer_participations WHERE practice_id IN (:a, :b)"),
            {"a": schema, "b": elsewhere},
        )
        conn.commit()
    finally:
        conn.close()
    _drop(engine, schema)
    _drop(engine, elsewhere)


def _status(engine: Engine, schema: str, user: str | None, appointment: str, payer: str) -> list:
    conn = _as(engine, schema, user)
    try:
        rows = conn.execute(
            text(
                "SELECT status FROM practice_enrollment_status(CAST(:a AS uuid), CAST(:p AS uuid))"
            ),
            {"a": appointment, "p": payer},
        )
        return [row.status for row in rows]
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
            {"fn": f"{visit['schema']}.practice_enrollment_status(uuid,uuid)"},
        ).one()
    assert row.owner == _ROLE
    assert row.prosecdef is True
    assert row.proconfig == [f"search_path=pg_catalog, {visit['schema']}, pg_temp"]


def test_a_colleague_reads_the_status_their_own_policy_hides(
    engine: Engine, visit: dict[str, str]
) -> None:
    conn = _as(engine, visit["schema"], _A)
    try:
        hidden = conn.execute(text("SELECT count(*) FROM platform.payer_participations"))
        assert hidden.scalar() == 0
    finally:
        conn.close()
    assert _status(engine, visit["schema"], _A, visit["appointment"], visit["here"]) == [
        "credentialed"
    ]


def test_another_practices_row_for_the_same_clinician_is_not_read(
    engine: Engine, visit: dict[str, str]
) -> None:
    assert _status(engine, visit["schema"], _A, visit["appointment"], visit["only_elsewhere"]) == [
        None
    ]


def test_the_function_filters_on_its_own_practice_without_the_policy(
    engine: Engine, visit: dict[str, str]
) -> None:
    """The tight check alone: with the policy widened to every practice, rolled back after."""
    conn = _as(engine, visit["schema"], _A)
    try:
        conn.execute(
            text(
                "ALTER POLICY rls_practice_directory_read ON platform.payer_participations "
                "USING (true)"
            )
        )
        rows = conn.execute(
            text(
                "SELECT status FROM practice_enrollment_status(CAST(:a AS uuid), CAST(:p AS uuid))"
            ),
            {"a": visit["appointment"], "p": visit["only_elsewhere"]},
        ).all()
    finally:
        conn.rollback()
        conn.close()
    assert [row.status for row in rows] == [None]


def test_the_role_sees_only_its_own_practices_rows_through_the_policy(
    engine: Engine, visit: dict[str, str]
) -> None:
    """The backstop alone: the role, pinned to one practice, reading the table directly."""
    conn = _as(engine, visit["schema"], _A)
    try:
        conn.execute(text(f"SET search_path = pg_catalog, {visit['schema']}, pg_temp"))
        conn.execute(text(f"SET ROLE {_ROLE}"))
        rows = conn.execute(
            text("SELECT practice_id, payer_id::text AS payer FROM platform.payer_participations")
        ).all()
        conn.execute(text("SET search_path = pg_catalog, pg_temp"))
        unpinned = conn.execute(text("SELECT count(*) FROM platform.payer_participations"))
        assert unpinned.scalar() == 0
        conn.execute(text("RESET ROLE"))
    finally:
        conn.rollback()
        conn.close()
    assert [tuple(row) for row in rows] == [(visit["schema"], visit["here"])]


def test_the_role_may_read_only_four_columns(engine: Engine, visit: dict[str, str]) -> None:
    with engine.connect() as conn:
        columns = conn.execute(
            text(
                "SELECT column_name, privilege_type FROM information_schema.column_privileges "
                "WHERE grantee = :role AND table_schema = 'platform' "
                "AND table_name = 'payer_participations' ORDER BY column_name"
            ),
            {"role": _ROLE},
        ).all()
    assert [tuple(row) for row in columns] == [
        ("payer_id", "SELECT"),
        ("practice_id", "SELECT"),
        ("status", "SELECT"),
        ("user_id", "SELECT"),
    ]


def test_it_returns_nothing_without_a_clinician(engine: Engine, visit: dict[str, str]) -> None:
    assert _status(engine, visit["schema"], None, visit["appointment"], visit["here"]) == []


def _claim(appointment: str, payer: str) -> SimpleNamespace:
    return SimpleNamespace(
        lines=[SimpleNamespace(appointment_id=appointment)],
        payer_id=payer,
        owner_user_id=_B,
    )


def test_read_enrollment_status_answers_a_biller_and_the_clinician_only(
    engine: Engine, visit: dict[str, str]
) -> None:
    from app.billing_access import read_enrollment_status

    conn = _as(engine, visit["schema"], _A)
    session = Session(bind=conn)
    practice = object()
    colleague, clinician = SimpleNamespace(id=_A), SimpleNamespace(id=_B)
    here = _claim(visit["appointment"], visit["here"])
    no_row = _claim(visit["appointment"], visit["only_elsewhere"])
    try:
        with patch("app.billing_access.can_bill", return_value=True):
            billed = read_enrollment_status(session, practice, colleague, here)  # type: ignore[arg-type]
            missing = read_enrollment_status(session, practice, colleague, no_row)  # type: ignore[arg-type]
        with patch("app.billing_access.can_bill", return_value=False):
            refused = read_enrollment_status(session, practice, colleague, here)  # type: ignore[arg-type]
            own = read_enrollment_status(session, practice, clinician, here)  # type: ignore[arg-type]
    finally:
        session.close()
        conn.close()
    assert (billed, missing, refused, own) == (
        "credentialed",
        "out_of_network",
        None,
        "credentialed",
    )
