# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres proof of the practice export: every chart the admin can open,
and only those.

Three clients on one practice. The admin holds a grant on two of them; the
third is another clinician's alone. The practice archive built as the admin
holds two ``patients/`` folders, two rows in each CSV, and nothing of the
third: not its id, not its name. Each chart's archive is the one the chart
export builds, byte for byte, and the manifest's checksums hold.

Non-vacuity: the third client is shown to exist before its absence means
anything, and the two that are in the copy are asserted by id.

Run: ``make test-integration``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from tests_integration.database.export_wiring import export_service_for

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_ADMIN = "3b8e1f47-6a2d-5c91-8e04-7d1f9a3c6b52"
_OTHER_CLINICIAN = "d47a2c19-8f3e-5b06-9c21-5e8b7f4a1d63"
_PRACTICE_NAME = "Harbor Light Counseling"
_HIDDEN_SURNAME = "HIDDENCLIENTSENTINEL"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    schema = f"practice_test_practice_export_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    shutil.rmtree(_storage_root(schema), ignore_errors=True)
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _storage_root(tenant_schema: str) -> Path:
    return Path(tempfile.gettempdir()) / f"{tenant_schema}-documents"


@pytest.fixture(scope="module")
def clients(engine: Engine, tenant_schema: str) -> dict[str, str]:
    """Three charts: two the admin may open, one that is the other clinician's alone."""
    from app.models import User, UserPreferences  # noqa: PLC0415
    from app.repositories.postgres.user import PostgresUserRepository  # noqa: PLC0415
    from app.services.practice_billing_profile import update_billing_profile  # noqa: PLC0415

    ids = {"mine:a": str(uuid.uuid4()), "mine:b": str(uuid.uuid4()), "theirs": str(uuid.uuid4())}
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        _arm(conn, _ADMIN)
        for key, first, last in (
            ("mine:a", "Ada", "Lovelace"),
            ("mine:b", "Grace", "Hopper"),
            ("theirs", "Hidden", _HIDDEN_SURNAME),
        ):
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                    "last_name_lower, status, session_count, created_at, updated_at) "
                    "VALUES (CAST(:pid AS uuid), :first, :last, :first_lower, :last_lower, "
                    "'active', 0, now(), now())"
                ),
                {
                    "pid": ids[key],
                    "first": first,
                    "last": last,
                    "first_lower": first.lower(),
                    "last_lower": last.lower(),
                },
            )
        for key, user_id in (("mine:a", _ADMIN), ("mine:b", _ADMIN), ("theirs", _OTHER_CLINICIAN)):
            _arm(conn, user_id)
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:pid AS uuid), :u, :u)"
                ),
                {"pid": ids[key], "u": user_id},
            )

    session, tokens = _open_session(engine, tenant_schema, _ADMIN)
    try:
        users = PostgresUserRepository(session)
        users.update(
            User(
                id=_ADMIN,
                email=f"{tenant_schema}@example.test",
                name="Dana Reyes",
                created_at=datetime.now(UTC),
            )
        )
        users.save_preferences(_ADMIN, UserPreferences(timezone="America/Chicago"))
        update_billing_profile(session, {"legal_name": _PRACTICE_NAME})
        session.commit()
    finally:
        _close_session(session, tokens)
    return ids


def _arm(conn: Any, user_id: str) -> None:
    conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user_id})


def _open_session(engine: Engine, tenant_schema: str, user_id: str) -> tuple[Session, tuple]:
    from app.db import (  # noqa: PLC0415
        _current_patient_id,
        _current_tenant_schema,
        _current_user_id,
        arm_current_user_id,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    tokens = (
        _current_tenant_schema.set(tenant_schema),
        _current_user_id.set(user_id),
        _current_patient_id.set(None),
    )
    session = OrmSession(bind=engine)
    session.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
    arm_current_user_id(session, user_id)
    return session, tokens


def _close_session(session: Session, tokens: tuple) -> None:
    from app.db import (  # noqa: PLC0415
        _current_patient_id,
        _current_tenant_schema,
        _current_user_id,
    )

    session.close()
    _current_tenant_schema.reset(tokens[0])
    _current_user_id.reset(tokens[1])
    _current_patient_id.reset(tokens[2])


def _practice_archive(
    engine: Engine, tenant_schema: str, user_id: str, **options: bool
) -> tuple[dict[str, bytes], Any]:
    """The practice archive built as ``user_id``, the way the route builds it."""
    from app.models.export import ExportOptions  # noqa: PLC0415
    from app.repositories.postgres.patient import PostgresPatientRepository  # noqa: PLC0415
    from app.routes.admin import _every_patient  # noqa: PLC0415
    from app.services.practice_export_service import (  # noqa: PLC0415
        PracticeExportState,
        stream_practice_archive,
    )
    from app.services.tenant_export_service import audit_log_csv  # noqa: PLC0415

    session, tokens = _open_session(engine, tenant_schema, user_id)
    try:
        service = export_service_for(
            session, practice_name=_PRACTICE_NAME, storage_root=_storage_root(tenant_schema)
        )
        state = PracticeExportState()
        content = b"".join(
            stream_practice_archive(
                patients=_every_patient(PostgresPatientRepository(session), user_id),
                build=lambda patient_id: service.get_patient_export_data(
                    patient_id, user_id, "zip", **options
                ),
                audit_log=lambda: audit_log_csv(session),
                options=ExportOptions(**options),
                exported_at=datetime.now(UTC),
                state=state,
            )
        )
    finally:
        _close_session(session, tokens)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}, state


def _rows(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode().removeprefix("﻿"))))


class TestThePracticeArchive:
    def test_it_holds_every_chart_the_admin_can_open_and_only_those(
        self, engine: Engine, tenant_schema: str, clients: dict[str, str]
    ) -> None:
        # The hidden chart exists, as its own clinician can see.
        theirs, _ = _practice_archive(engine, tenant_schema, _OTHER_CLINICIAN)
        assert [r["client_id"] for r in _rows(theirs["clients.csv"])] == [clients["theirs"]]

        files, state = _practice_archive(engine, tenant_schema, _ADMIN)

        folders = {name.split("/")[1] for name in files if name.startswith("patients/")}
        assert folders == {clients["mine:a"], clients["mine:b"]}
        assert sorted(r["client_id"] for r in _rows(files["clients.csv"])) == sorted(
            [clients["mine:a"], clients["mine:b"]]
        )
        assert _rows(files["appointments.csv"]) == []
        assert state.summary is not None
        assert state.summary.patients == 2
        assert [p.id for p, _ in state.exported] == [clients["mine:b"], clients["mine:a"]], (
            "as the client list orders them: by surname"
        )
        for name, data in files.items():
            assert clients["theirs"] not in data.decode("latin-1"), name
            assert _HIDDEN_SURNAME not in data.decode("latin-1"), name

    def test_each_chart_archive_is_the_chart_export_byte_for_byte(
        self, engine: Engine, tenant_schema: str, clients: dict[str, str]
    ) -> None:
        files, state = _practice_archive(engine, tenant_schema, _ADMIN)

        assert {p.id for p, _ in state.exported} == {clients["mine:a"], clients["mine:b"]}
        for patient, exported in state.exported:
            path = f"patients/{patient.id}/{exported['filename']}"
            assert files[path] == exported["content"]
            with zipfile.ZipFile(io.BytesIO(files[path])) as inner:
                document = json.loads(inner.read("patient.json"))
            assert document["patient"]["identifier"] == patient.id

        manifest = json.loads(files["manifest.json"])
        listed = {entry["path"]: entry for entry in manifest["files"]}
        assert set(listed) == set(files) - {"manifest.json"}
        for path, entry in listed.items():
            assert entry["bytes"] == len(files[path]), path
            assert entry["sha256"] == hashlib.sha256(files[path]).hexdigest(), path
        assert manifest["options"] == {
            "include_transcripts": False,
            "include_psychotherapy_notes": False,
        }
        assert files["audit_log.csv"] is not None

    def test_the_options_reach_every_archive(
        self, engine: Engine, tenant_schema: str, clients: dict[str, str]
    ) -> None:
        files, state = _practice_archive(engine, tenant_schema, _ADMIN, include_transcripts=True)

        assert json.loads(files["manifest.json"])["options"]["include_transcripts"] is True
        assert len(state.exported) == len([key for key in clients if key.startswith("mine:")])
        for patient, exported in state.exported:
            with zipfile.ZipFile(io.BytesIO(exported["content"])) as inner:
                document = json.loads(inner.read("patient.json"))
            assert document["options"]["include_transcripts"] is True, patient.id
