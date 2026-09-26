# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres proof that psychotherapy notes leave in an export only on request.

The practice export streams the rows the admin's session can read. Under the
notes row policy that already means an admin sees their own restricted notes
and nobody else's; this file proves the export adds a choice on top of that:

* **flag off (the default)** — not one restricted note ships, the admin's own
  included;
* **flag on** — exactly one ships, and it is the admin's. The other
  clinician's stays out, because the policy never lets the admin read it.

One chart, two clinicians with a grant on it, each of whom wrote a progress
note and a psychotherapy note. The admin is clinician A.

**Non-vacuity is enforced, not hoped for.** The role's RLS-bypass bits are
asserted up front, clinician B's psychotherapy note is shown to exist before
anyone asserts it is missing, and the flag-off export must still carry both
progress notes, so an empty ``notes.json`` cannot pass.

Run: ``make test-integration``.
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection, Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

# The practice admin running the export.
_ADMIN_A = "3b8e1f47-6a2d-5c91-8e04-7d1f9a3c6b52"
# A second clinician on the same chart.
_CLINICIAN_B = "d47a2c19-8f3e-5b06-9c21-5e8b7f4a1d63"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    # Warm the pool so policy CREATEs referencing ``has_patient_access``
    # (which lives in ``practice``) resolve.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_export_notes_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def notes(engine: Engine, tenant_schema: str) -> dict[str, str]:
    """One patient; each clinician holds a grant and writes two notes on it."""
    patient_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        _arm(conn, _ADMIN_A)
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, "
                "first_name_lower, last_name_lower, status, "
                "session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Ada', 'Lovelace', "
                "'ada', 'lovelace', 'active', 0, now(), now())"
            ),
            {"pid": patient_id},
        )
        # The grant table is policed by ``user_id``: each clinician's grant
        # row is written as that clinician, as the app does.
        for user_id in (_ADMIN_A, _CLINICIAN_B):
            _arm(conn, user_id)
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:pid AS uuid), :u, :granted_by)"
                ),
                {"pid": patient_id, "u": user_id, "granted_by": _ADMIN_A},
            )

    ids: dict[str, str] = {}
    for label, user_id in (("a", _ADMIN_A), ("b", _CLINICIAN_B)):
        conn = _as_clinician(engine, tenant_schema, user_id)
        try:
            ids[f"{label}_progress"] = _insert(conn, patient_id, user_id, restricted=False)
            ids[f"{label}_psychotherapy"] = _insert(conn, patient_id, user_id, restricted=True)
            conn.commit()
        finally:
            conn.close()
    return ids


# ---------------------------------------------------------------------------
# Connections, writes and the export itself
# ---------------------------------------------------------------------------


def _arm(conn: Connection, user_id: str) -> None:
    conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user_id})


def _as_clinician(engine: Engine, schema: str, user_id: str) -> Connection:
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_patient_id"))
    _arm(conn, user_id)
    return conn


def _insert(conn: Connection, patient_id: str, author: str, *, restricted: bool) -> str:
    note_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO notes "
            "(id, patient_id, session_id, note_type, content, status, "
            "author_user_id, restricted, created_at, updated_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), NULL, :note_type, "
            "CAST(:content AS jsonb), 'complete', CAST(:author AS uuid), :restricted, "
            ":now, :now)"
        ),
        {
            "id": note_id,
            "pid": patient_id,
            "note_type": "psychotherapy" if restricted else "narrative",
            "content": '{"note": {"body": "x"}}',
            "author": author,
            "restricted": restricted,
            "now": datetime.now(UTC).replace(microsecond=0),
        },
    )
    return note_id


def _export_as_admin(
    engine: Engine, schema: str, *, include_psychotherapy_notes: bool
) -> tuple[dict[str, Any], list[dict[str, Any]], Any]:
    """Run the real export on admin A's session; return manifest, notes, summary.

    The session is armed the way the app arms a request's session. Setting
    the GUC on the connection alone is not enough: every transaction the
    Session opens re-arms ``app.current_user_id`` from ``session.info``,
    falling back to the ambient ContextVar, so a clinician left there by an
    earlier module would replace A and the export would come back empty.
    """
    from app.db import arm_current_user_id  # noqa: PLC0415
    from app.services.tenant_export_service import (  # noqa: PLC0415
        TenantExportState,
        stream_tenant_archive,
    )

    conn = _as_clinician(engine, schema, _ADMIN_A)
    state = TenantExportState()
    try:
        with Session(bind=conn) as db:
            arm_current_user_id(db, _ADMIN_A)
            archive = b"".join(
                stream_tenant_archive(
                    db,
                    export_format="json",
                    include_psychotherapy_notes=include_psychotherapy_notes,
                    state=state,
                )
            )
    finally:
        conn.close()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        manifest = json.loads(_member(tar, "manifest.json"))
        exported_notes = json.loads(_member(tar, "notes.json"))
    return manifest, exported_notes, state.summary


def _member(tar: tarfile.TarFile, name: str) -> bytes:
    member = tar.extractfile(name)
    assert member is not None, name
    return member.read()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRoleReallyEnforcesRls:
    """If this fails, every assertion below about who sees what is meaningless."""

    def test_connecting_role_does_not_bypass_rls(self, engine: Engine) -> None:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).first()
        assert row is not None
        assert not row[0], "connecting role is a superuser; RLS would be bypassed"
        assert not row[1], "connecting role has BYPASSRLS; RLS would be bypassed"

    def test_clinician_b_psychotherapy_note_exists(
        self, engine: Engine, tenant_schema: str, notes: dict[str, str]
    ) -> None:
        """Control: its absence from A's export is the policy, not an empty table."""
        conn = _as_clinician(engine, tenant_schema, _CLINICIAN_B)
        try:
            found = conn.execute(
                text("SELECT id::text FROM notes WHERE id = CAST(:id AS uuid)"),
                {"id": notes["b_psychotherapy"]},
            ).scalars()
            assert list(found) == [notes["b_psychotherapy"]]
        finally:
            conn.close()


class TestPracticeExportPsychotherapyNotes:
    def test_default_export_ships_no_restricted_note(
        self, engine: Engine, tenant_schema: str, notes: dict[str, str]
    ) -> None:
        manifest, exported, summary = _export_as_admin(
            engine, tenant_schema, include_psychotherapy_notes=False
        )

        assert {n["id"] for n in exported} == {notes["a_progress"], notes["b_progress"]}, (
            "control: both progress notes ship, so the export is not simply empty"
        )
        assert [n for n in exported if n["restricted"]] == []
        assert manifest["include_psychotherapy_notes"] is False
        assert manifest["psychotherapy_notes_included"] == 0
        assert "psychotherapy_notes_scope" not in manifest
        assert summary is not None
        assert summary.psychotherapy_notes_included == 0

    def test_opted_in_export_ships_only_the_admins_own(
        self, engine: Engine, tenant_schema: str, notes: dict[str, str]
    ) -> None:
        manifest, exported, summary = _export_as_admin(
            engine, tenant_schema, include_psychotherapy_notes=True
        )

        restricted = [n for n in exported if n["restricted"]]
        assert [n["id"] for n in restricted] == [notes["a_psychotherapy"]]
        assert restricted[0]["author_user_id"] == _ADMIN_A
        assert notes["b_psychotherapy"] not in {n["id"] for n in exported}
        assert manifest["include_psychotherapy_notes"] is True
        assert manifest["psychotherapy_notes_included"] == 1
        assert manifest["psychotherapy_notes_scope"] == (
            "Psychotherapy notes are included only where you are the author; "
            "each clinician exports their own."
        )
        assert manifest["counts"]["notes"]["visible_count"] == 3
        assert summary is not None
        assert summary.include_psychotherapy_notes is True
        assert summary.psychotherapy_notes_included == 1
