# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Import a saved SimplePractice calendar feed through the real API, on real Postgres.

The same routes, background steps, ledger and undo path as the archive
journey, over the second layout: a zip whose ``calendar/`` folder holds the
web calendar's saved responses. What the chart holds afterwards is counted
with the clinician's own RLS principal armed.

One journey, end to end: preview, apply (past attended visits completed,
upcoming ones scheduled, past misses left out), the same zip again landing
nothing, a zip where one appointment moved updating that one row, and undo
removing every row each run created. Then the one refusal: a zip holding both
layouts.

Run: ``make test-integration``.
"""

from __future__ import annotations

import io
import json
import os
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

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

_USER = "7d1f3b2a-5c4e-4a81-b6d0-9e2f1c3a4b55"
_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
CALENDAR = _FIXTURES / "simplepractice_calendar" / "calendar"
ARCHIVE = _FIXTURES / "simplepractice_export"
AVERY, BLAKE, CASEY, DANA = "112230097", "112230081", "112230132", "112230150"
MOVED = "3793472377"
#: Blake's 2026-10-13 visit, and the link in its file that names Blake.
BLAKES_VISIT, BLAKES_VISIT_LINK = "3793470566", "1635772867"
#: The day before the fixture's 2026-10-20 visit, so the journey does not
#: depend on the day it runs: two visits are past and attended, two ahead.
NOW = datetime(2026, 10, 19, 16, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    name = f"practice_test_calimport_{uuid.uuid4().hex[:10]}"
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
        email="calendar-import-e2e@example.com",
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
    # The steps judge "today" through this clock; pinned so the journey reads
    # the same on any day.
    monkeypatch.setattr("app.migration.jobs.utc_now", lambda: NOW)

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


def _feed_files(
    moved_minutes: int = 0, *, cancel: bool = False, reassign: bool = False
) -> dict[str, bytes]:
    """The fixture's files as a practice would zip them, optionally with one
    appointment moved later by ``moved_minutes`` or cancelled in the file that
    owns it, and with Blake's past visit moved over to Casey."""
    files: dict[str, bytes] = {}
    for path in sorted(CALENDAR.glob("*.json")):
        data = path.read_bytes()
        if (moved_minutes or cancel) and path.name == "appointments-06.json":
            document = json.loads(data)
            attrs = document["data"][0]["attributes"]
            assert document["data"][0]["id"] == MOVED
            if moved_minutes:
                attrs["startTime"] = f"2026-10-20T10:{moved_minutes:02d}:00-04:00"
                attrs["endTime"] = f"2026-10-20T11:{moved_minutes - 10:02d}:00-04:00"
            if cancel:
                attrs["attendanceStatus"] = "Cancelled"
            data = json.dumps(document).encode()
        if reassign and path.name == "appointments-07.json":
            document = json.loads(data)
            (link,) = [e for e in document["included"] if e["id"] == BLAKES_VISIT_LINK]
            link["relationships"]["client"]["data"]["id"] = CASEY
            data = json.dumps(document).encode()
        files[f"calendar/{path.name}"] = data
    return files


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _upload(client: TestClient, archive: bytes, scope: str = "patients") -> dict[str, Any]:
    resp = client.post(
        "/api/migration/runs",
        files={"file": ("calendar.zip", archive, "application/zip")},
        data={"scope": scope},
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


_COUNTS = {
    "patients": "SELECT count(*) FROM patients WHERE deleted_at IS NULL",
    "imported_patients": (
        "SELECT count(*) FROM patients WHERE deleted_at IS NULL AND origin = 'simplepractice'"
    ),
    "appointments": "SELECT count(*) FROM appointments",
    "scheduled": "SELECT count(*) FROM appointments WHERE status = 'confirmed'",
    "completed": "SELECT count(*) FROM appointments WHERE status = 'completed'",
    "titled_session": "SELECT count(*) FROM appointments WHERE title = 'Session'",
    "notes": "SELECT count(*) FROM notes WHERE deleted_at IS NULL",
}


def _query(
    engine: Engine, schema: str, sql: str, params: dict[str, str] | None = None
) -> list[Any]:
    with engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('search_path', :p, true)"),
            {"p": f"{schema}, platform, public"},
        )
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": _USER})
        rows = conn.execute(text(sql), params or {}).all()
        conn.rollback()
    return rows


def _chart(engine: Engine, schema: str) -> dict[str, int]:
    return {k: int(_query(engine, schema, sql)[0][0]) for k, sql in _COUNTS.items()}


def _ledger(engine: Engine, schema: str) -> dict[str, tuple[str, str]]:
    """Standing appointment ledger rows: source id -> (target id, state)."""
    rows = _query(
        engine,
        schema,
        "SELECT source_id, target_id, state FROM import_records "
        "WHERE record_type = 'calendar_appointment' AND state != 'undone'",
    )
    return {str(r[0]): (str(r[1]), str(r[2])) for r in rows}


def _appointment(engine: Engine, schema: str, appointment_id: str) -> tuple[str, str]:
    """An appointment's status and the patient it is on."""
    (row,) = _query(
        engine,
        schema,
        "SELECT status, patient_id FROM appointments WHERE id = :id",
        {"id": appointment_id},
    )
    return str(row[0]), str(row[1])


def _patient_of(engine: Engine, schema: str, client_id: str) -> str:
    """The patient a calendar import landed for a SimplePractice client id."""
    (row,) = _query(
        engine,
        schema,
        "SELECT target_id FROM import_records "
        "WHERE record_type = 'calendar_client' AND source_id = :id AND state != 'undone'",
        {"id": client_id},
    )
    return str(row[0])


def _start_of(engine: Engine, schema: str, appointment_id: str) -> datetime:
    (row,) = _query(
        engine, schema, "SELECT start_at FROM appointments WHERE id = :id", {"id": appointment_id}
    )
    return row[0]


# --------------------------------------------------------------------------- journeys


def _cancel_and_reassign(
    client: TestClient,
    engine: Engine,
    schema: str,
    ledger: dict[str, tuple[str, str]],
    chart: dict[str, int],
) -> None:
    """A cancellation and a move to another client land on the appointments an
    earlier run created, and undoing that run puts both back."""
    # The client cancels the upcoming visit, and Blake's past visit is moved
    # over to Casey: the cancellation reaches Pablo and the visit follows.
    first_id, visit_id = ledger[MOVED][0], ledger[BLAKES_VISIT][0]
    blake, casey = _patient_of(engine, schema, BLAKE), _patient_of(engine, schema, CASEY)
    assert _appointment(engine, schema, visit_id) == ("completed", blake)
    changed = _upload(client, _zip(_feed_files(cancel=True, reassign=True)))
    assert changed["preview"]["counts"]["appointment"] == {
        "unchanged": 2,
        "changed": 2,
        "skipped": 2,
    }
    fourth = _apply(client, changed["id"], {})
    assert fourth["report"]["counts"]["appointment"] == {"unchanged": 2, "changed": 2}
    assert _appointment(engine, schema, first_id)[0] == "cancelled"
    assert _appointment(engine, schema, visit_id) == ("completed", casey)
    assert _chart(engine, schema)["scheduled"] == 1

    undone = client.post(f"/api/migration/runs/{changed['id']}/undo", json={})
    assert undone.status_code == 200, undone.text
    assert undone.json()["report"]["undo"]["restored"] == {"calendar_appointment": 2}
    assert _appointment(engine, schema, first_id)[0] == "confirmed"
    assert _appointment(engine, schema, visit_id) == ("completed", blake)
    assert _chart(engine, schema) == chart


def test_calendar_feed_end_to_end(client: TestClient, engine: Engine, schema: str) -> None:
    run = _upload(client, _zip(_feed_files()))
    preview = run["preview"]
    assert preview["layout"] == "calendar"
    assert preview["counts"]["contact"] == {"new": 4}
    assert preview["counts"]["appointment"] == {"new": 4, "skipped": 2}
    assert run["missing"] == []  # no providers to map, no same-name folders
    assert {c["what"] for c in preview["cannot_land"]} == {
        "group_appointment",
        "external_event",
        "cancelled",
    }

    done = _apply(client, run["id"], {})
    report = done["report"]
    # The run screen names the clients the import brought in.
    assert (report["counts"]["contact"], len(done["clients"])) == ({"created": 4}, 4)
    assert report["counts"]["appointment"] == {"new": 4}
    assert "chart_updates" not in report  # no notes, nothing to propose from
    assert done["has_archive"] is False

    chart = _chart(engine, schema)
    assert chart == {
        "patients": 4,
        "imported_patients": 4,
        "appointments": 4,
        "scheduled": 2,  # 2026-10-20 and 2026-11-02
        "completed": 2,  # 2026-10-13 and 2026-10-14, both Show
        "titled_session": 4,
        "notes": 0,
    }
    ledger = _ledger(engine, schema)
    assert set(ledger) == {"3793470566", MOVED, "3793478974", "3793480001"}
    assert {state for _, state in ledger.values()} == {"landed"}
    first_id = ledger[MOVED][0]
    assert _start_of(engine, schema, first_id) == datetime(2026, 10, 20, 14, 0, tzinfo=UTC)

    # The same zip again lands nothing new.
    again = _upload(client, _zip(_feed_files()))
    assert again["preview"]["counts"]["appointment"] == {"unchanged": 4, "skipped": 2}
    assert {c["match_evidence"] for c in again["preview"]["clients"]} == {"ledger"}
    second = _apply(client, again["id"], {})
    assert second["report"]["counts"]["appointment"] == {"unchanged": 4}
    assert second["report"]["counts"]["contact"] == {"unchanged": 4}
    assert _chart(engine, schema) == chart

    # One appointment moved 30 minutes: that row moves, nothing else does.
    moved = _upload(client, _zip(_feed_files(moved_minutes=30)))
    states = {r["source_id"]: r["state"] for r in moved["preview"]["records"]}
    assert states[MOVED] == "changed"
    assert moved["preview"]["counts"]["appointment"] == {"unchanged": 3, "changed": 1, "skipped": 2}
    third = _apply(client, moved["id"], {})
    assert third["report"]["counts"]["appointment"] == {"unchanged": 3, "changed": 1}
    assert _chart(engine, schema) == chart
    ledger = _ledger(engine, schema)
    assert ledger[MOVED] == (first_id, "updated")
    assert _start_of(engine, schema, first_id) == datetime(2026, 10, 20, 14, 30, tzinfo=UTC)

    # Undo the move's run: the appointment goes back to where it was, and back
    # to the run that created it. Nothing is deleted.
    undone = client.post(f"/api/migration/runs/{moved['id']}/undo", json={})
    assert undone.status_code == 200, undone.text
    assert undone.json()["report"]["undo"] == {
        "removed": {},
        "kept": [],
        "restored": {"calendar_appointment": 1},
    }
    assert _chart(engine, schema) == chart
    assert _ledger(engine, schema)[MOVED] == (first_id, "landed")
    assert _start_of(engine, schema, first_id) == datetime(2026, 10, 20, 14, 0, tzinfo=UTC)

    _cancel_and_reassign(client, engine, schema, ledger, chart)

    # Undo the first run: every client and appointment it created goes.
    undone = client.post(f"/api/migration/runs/{run['id']}/undo", json={})
    assert undone.status_code == 200, undone.text
    assert undone.json()["report"]["undo"] == {
        "removed": {"calendar_appointment": 4, "calendar_client": 4},
        "kept": [],
    }
    after = _chart(engine, schema)
    assert (after["patients"], after["appointments"]) == (0, 0)
    assert _ledger(engine, schema) == {}


def test_undoing_the_first_run_first_takes_its_moved_appointment_too(
    client: TestClient, engine: Engine, schema: str
) -> None:
    """A later run moved one of the first run's appointments. Undoing the first
    run removes that appointment with the rest, and undoing the later run
    afterwards brings nothing back."""
    first = _upload(client, _zip(_feed_files()))
    _apply(client, first["id"], {})
    moved = _upload(client, _zip(_feed_files(moved_minutes=30)))
    _apply(client, moved["id"], {})

    undone = client.post(f"/api/migration/runs/{first['id']}/undo", json={})
    assert undone.status_code == 200, undone.text
    assert undone.json()["report"]["undo"]["removed"] == {
        "calendar_appointment": 4,
        "calendar_client": 4,
    }
    after = _chart(engine, schema)
    assert (after["patients"], after["appointments"]) == (0, 0)

    undone = client.post(f"/api/migration/runs/{moved['id']}/undo", json={})
    assert undone.status_code == 200, undone.text
    assert undone.json()["report"]["undo"] == {"removed": {}, "kept": []}
    after = _chart(engine, schema)
    assert (after["patients"], after["appointments"]) == (0, 0)
    assert _ledger(engine, schema) == {}


def test_the_practice_scope_lands_nothing_from_a_calendar(
    client: TestClient, engine: Engine, schema: str
) -> None:
    run = _upload(client, _zip(_feed_files()), scope="practice")
    _apply(client, run["id"], {})
    chart = _chart(engine, schema)
    assert (chart["patients"], chart["appointments"]) == (0, 0)


def test_a_zip_holding_both_layouts_is_refused(client: TestClient, schema: str) -> None:
    files = _feed_files()
    card = next(ARCHIVE.glob("Contacts/*.vcf"))
    files[f"Contacts/{card.name}"] = card.read_bytes()
    resp = client.post(
        "/api/migration/runs",
        files={"file": ("both.zip", _zip(files), "application/zip")},
        data={"scope": "patients"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "INVALID_ARCHIVE"
    assert client.get("/api/migration/runs").json()["runs"] == []


def test_a_zip_of_loose_calendar_files_is_refused_with_what_to_do(
    client: TestClient, schema: str
) -> None:
    loose = {name.removeprefix("calendar/"): data for name, data in _feed_files().items()}
    resp = client.post(
        "/api/migration/runs",
        files={"file": ("loose.zip", _zip(loose), "application/zip")},
        data={"scope": "patients"},
    )
    assert resp.status_code == 400, resp.text
    error = resp.json()["error"]
    assert error["code"] == "INVALID_ARCHIVE"
    assert "folder named calendar" in error["message"]
    assert client.get("/api/migration/runs").json()["runs"] == []
