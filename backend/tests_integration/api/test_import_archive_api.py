# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Import a captured SimplePractice export through the real API, on real Postgres.

The routes, the background preview and apply (run by the test client as part
of the request), the ledger, and every landing path run against a provisioned
practice schema with RLS on. What the chart holds afterwards is counted with
the clinician's own RLS principal armed, so nothing here can see rows the
clinician could not.

Three journeys:

* the main fixture end to end — preview, the questions it asks, apply,
  exactly what landed, a second upload that changes nothing, and undo;
* a client who already exists here without a birthday — the duplicate
  question, and a merge that creates no second patient;
* two same-named clients with no birthday — every record asked about,
  apply refused until answered, each landing only where assigned.

Run: ``make test-integration``.
"""

from __future__ import annotations

import io
import os
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.testclient import TestClient
    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured; testcontainers sets DATABASE_URL and DATABASE_BACKEND.",
)

os.environ.setdefault("ENVIRONMENT", "development")

_USER = "4c2e8a1f-6b3d-4e70-9c15-2a7d8e9f0b31"
_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
MAIN = _FIXTURES / "simplepractice_export"
NO_DOB = _FIXTURES / "simplepractice_export_same_name_no_dob"
LULU, PABLO_A, PABLO = "112092152", "112092194", "112093920"


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture
def schema(engine: Engine) -> Iterator[str]:
    """A fresh practice per test: each journey starts from an empty chart."""
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    name = f"practice_test_import_{uuid.uuid4().hex[:10]}"
    create_practice_schema(engine, name)
    yield name
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
        conn.commit()


@pytest.fixture
def client(schema: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    from app.auth.service import (  # noqa: PLC0415
        TenantContext,
        get_current_user,
        get_current_user_id,
        get_current_user_no_mfa,
        get_tenant_context,
        require_active_subscription,
        require_baa_acceptance,
    )
    from app.db import arm_current_user_id, get_db_session  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415
    from app.models import User  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    user = User(
        id=_USER,
        email="import-e2e@example.com",
        name="Import Tester",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )
    monkeypatch.setattr(
        "app.db.middleware._resolve_schema_from_request", lambda _r: (schema, "resolved")
    )
    monkeypatch.setattr("app.migration.jobs.archive_base", lambda: tmp_path)
    monkeypatch.setattr("app.routes.migration.archive_base", lambda: tmp_path)

    def _ctx() -> TenantContext:
        arm_current_user_id(get_db_session(), _USER)
        return TenantContext(user_id=_USER, practice_id="import-test", practice_schema=schema)

    app.dependency_overrides[get_current_user_id] = lambda: _USER
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_user_no_mfa] = lambda: user
    app.dependency_overrides[require_active_subscription] = lambda: user
    app.dependency_overrides[require_baa_acceptance] = lambda: user
    app.dependency_overrides[get_tenant_context] = _ctx
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- helpers


def _zip(folder: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.name != "README.md" and path.suffix != ".py":
                zf.write(path, f"Export - Complete - test/{path.relative_to(folder).as_posix()}")
    return buf.getvalue()


def _upload(client: TestClient, folder: Path) -> dict[str, Any]:
    resp = client.post(
        "/api/migration/runs",
        files={"file": ("export.zip", _zip(folder), "application/zip")},
        data={"scope": "patients"},
    )
    assert resp.status_code == 202, resp.text
    run = client.get(f"/api/migration/runs/{resp.json()['id']}")
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["state"] == "previewed", body.get("error")
    return body


def _apply(client: TestClient, run_id: str, decisions: dict[str, Any]) -> dict[str, Any]:
    resp = client.post(f"/api/migration/runs/{run_id}/apply", json=decisions)
    assert resp.status_code == 202, resp.text
    done = client.get(f"/api/migration/runs/{run_id}").json()
    assert done["state"] == "applied", done.get("error")
    return done


def _upload_answers(run: dict[str, Any], card_id: str) -> dict[str, str]:
    return {
        f"{r['record_type']}:{r['source_id']}": card_id
        for g in run["preview"]["questions"]["same_name"]
        for r in g["records"]
    }


#: Every count the journeys read, as fixed statements. Each runs with the
#: clinician's RLS principal armed, so it sees only what they could.
_COUNTS = {
    "patients": "SELECT count(*) FROM patients WHERE deleted_at IS NULL",
    "imported_patients": (
        "SELECT count(*) FROM patients WHERE deleted_at IS NULL AND origin = 'simplepractice'"
    ),
    "notes": "SELECT count(*) FROM notes WHERE deleted_at IS NULL",
    "restricted": "SELECT count(*) FROM notes WHERE deleted_at IS NULL AND restricted",
    "finalized": (
        "SELECT count(*) FROM notes WHERE deleted_at IS NULL AND finalized_at IS NOT NULL"
    ),
    "sessions": "SELECT count(*) FROM therapy_sessions WHERE deleted_at IS NULL",
    "appointments": "SELECT count(*) FROM appointments WHERE status = 'completed'",
    "measures": "SELECT count(*) FROM outcome_measures WHERE deleted_at IS NULL",
    "threads": "SELECT count(*) FROM patient_message_threads",
    "messages": "SELECT count(*) FROM patient_messages",
    "notes_for_patient": (
        "SELECT count(*) FROM notes WHERE deleted_at IS NULL AND patient_id = :patient_id"
    ),
}


def _count(engine: Engine, schema: str, which: str, params: dict[str, str] | None = None) -> int:
    with engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('search_path', :p, false)"),
            {"p": f"{schema}, platform, public"},
        )
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _USER})
        value = conn.execute(text(_COUNTS[which]), params or {}).scalar_one()
        conn.rollback()
    return int(value)


def _chart(engine: Engine, schema: str) -> dict[str, int]:
    return {k: _count(engine, schema, k) for k in _COUNTS if k != "notes_for_patient"}


def _notes_for(engine: Engine, schema: str, patient_id: str) -> int:
    return _count(engine, schema, "notes_for_patient", {"patient_id": patient_id})


# --------------------------------------------------------------------------- journeys


def test_main_export_end_to_end(client: TestClient, engine: Engine, schema: str) -> None:
    run = _upload(client, MAIN)
    preview = run["preview"]
    assert preview["counts"]["note"] == {"new": 15}
    assert run["missing"], "a first preview must have questions"
    assert preview["questions"]["providers"] == [{"name": "Avery Provider"}]
    (group,) = preview["questions"]["same_name"]
    assert group["candidates"] == [PABLO_A, PABLO]
    assert len(group["records"]) == 2

    # Apply refuses while questions are open.
    refused = client.post(f"/api/migration/runs/{run['id']}/apply", json={})
    assert refused.status_code == 422, refused.text

    done = _apply(
        client,
        run["id"],
        {"providers": {"Avery Provider": "me"}, "assignments": _upload_answers(run, PABLO_A)},
    )
    report = done["report"]
    assert report["counts"]["contact"] == {"created": 3}
    assert report["counts"]["note"] == {"new": 15}
    assert report["counts"]["questionnaire"] == {"new": 2}
    assert report["counts"]["thread"] == {"new": 1}
    # This stack has no document bucket, so the uploads are reported, not lost.
    assert report["counts"]["upload"] == {"skipped": 2}
    assert done["has_archive"] is False  # deleted once applied

    chart = _chart(engine, schema)
    assert chart == {
        "patients": 3,
        "imported_patients": 3,
        "notes": 15,
        "restricted": 6,
        "finalized": 15,
        "sessions": 6,
        "appointments": 6,
        "measures": 2,
        "threads": 1,
        "messages": 1,
    }

    # Lulu's first progress note landed verbatim, dated from its visit, with provenance.
    with engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('search_path', :p, false)"),
            {"p": f"{schema}, platform, public"},
        )
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _USER})
        content = conn.execute(
            text(
                "SELECT n.content FROM notes n JOIN therapy_sessions s ON s.id = n.session_id "
                "WHERE s.session_date = '2026-09-24 13:00:00-04'"
            )
        ).scalar_one()
        conn.rollback()
    assert content["note"]["body"].startswith("Client arrived on time and was oriented")
    assert content["__source"]["source_id"] == "1007836363"
    assert content["__source"]["billing_code"] == "90834"
    assert [d["code"] for d in content["__source"]["diagnoses"]] == ["F41.1", "F33.1", "F43.10"]
    assert "203.0.113" not in str(content)

    # A second upload of the same export changes nothing.
    again = _upload(client, MAIN)
    assert again["preview"]["counts"]["note"] == {"unchanged": 15}
    assert {c["match_evidence"] for c in again["preview"]["clients"]} == {"ledger"}
    second = _apply(
        client,
        again["id"],
        {"providers": {"Avery Provider": "me"}, "assignments": _upload_answers(again, PABLO_A)},
    )
    assert second["report"]["counts"]["note"] == {"unchanged": 15}
    assert second["report"]["counts"]["contact"] == {"unchanged": 3}
    assert _chart(engine, schema) == chart

    # Undo the first run: everything it created goes.
    undone = client.post(f"/api/migration/runs/{run['id']}/undo", json={})
    assert undone.status_code == 200, undone.text
    assert undone.json()["state"] == "undone"
    after = _chart(engine, schema)
    assert after["patients"] == 0
    assert after["notes"] == 0
    assert after["measures"] == 0
    assert after["threads"] == 0

    history = client.get("/api/migration/runs").json()["runs"]
    assert [r["state"] for r in history] == ["applied", "undone"]


def test_existing_client_without_birthday_is_merged_on_request(
    client: TestClient, engine: Engine, schema: str
) -> None:
    made = client.post("/api/patients", json={"first_name": "Lulu", "last_name": "Llama"})
    assert made.status_code == 201, made.text
    existing_id = made.json()["id"]

    run = _upload(client, MAIN)
    assert run["preview"]["questions"]["duplicates"] == [
        {"card_id": LULU, "possible_duplicates": [existing_id]}
    ]
    assert any("duplicate decision" in m for m in run["missing"])
    _apply(
        client,
        run["id"],
        {
            "providers": {"Avery Provider": "me"},
            "assignments": _upload_answers(run, PABLO_A),
            "duplicates": {LULU: f"merge:{existing_id}"},
        },
    )
    chart = _chart(engine, schema)
    assert chart["patients"] == 3  # the existing Lulu plus two Pablo Bears
    assert chart["imported_patients"] == 2
    # 2 progress, 2 psychotherapy, chart note, treatment plan
    assert _notes_for(engine, schema, existing_id) == 6


def test_same_name_without_birthdays_lands_only_where_assigned(
    client: TestClient, engine: Engine, schema: str
) -> None:
    run = _upload(client, NO_DOB)
    (group,) = run["preview"]["questions"]["same_name"]
    assert group["candidates"] == [PABLO_A, PABLO]
    assert {r["record_type"] for r in group["records"]} == {"note", "upload"}
    assert len(group["records"]) == 4

    # Viewing a record's source file works while the archive is held.
    note_path = next(
        r["path"]
        for r in run["preview"]["records"]
        if r["record_type"] == "note" and r["kind"] == "progress"
    )
    viewed = client.get(f"/api/migration/runs/{run['id']}/files", params={"path": note_path})
    assert viewed.status_code == 200
    assert viewed.content.startswith(b"%PDF")
    outside = client.get(f"/api/migration/runs/{run['id']}/files", params={"path": "../x"})
    assert outside.status_code == 404

    notes = [r for r in group["records"] if r["record_type"] == "note"]
    answers = {f"note:{notes[0]['source_id']}": PABLO, f"note:{notes[1]['source_id']}": PABLO}
    answers.update(
        {
            f"upload:{r['source_id']}": "skip"
            for r in group["records"]
            if r["record_type"] == "upload"
        }
    )
    partial = client.post(
        f"/api/migration/runs/{run['id']}/apply",
        json={
            "providers": {"Avery Provider": "me"},
            "assignments": {k: v for k, v in answers.items() if k.startswith("note")},
        },
    )
    assert partial.status_code == 422  # uploads still unanswered

    done = _apply(
        client, run["id"], {"providers": {"Avery Provider": "me"}, "assignments": answers}
    )
    assert done["report"]["counts"]["note"] == {"new": 2}
    assert done["report"]["counts"]["upload"] == {"skipped": 2}
    on_pablo = _notes_for(engine, schema, done["report"]["patients"][PABLO])
    on_other = _notes_for(engine, schema, done["report"]["patients"][PABLO_A])
    assert (on_pablo, on_other) == (2, 0)
