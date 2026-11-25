# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Who imports, through the real API on real Postgres.

Three clinicians in one practice: Olive owns it, Avery and Blake work in it.
Importing is the owner's and admins' act, because a run's preview lists every
client in the archive and, while the archive is kept, any file in it can be
opened.

* Avery cannot start an import (403), and nothing is kept.
* Olive uploads a captured SimplePractice export. Avery and Blake list nothing,
  and reading the run, opening one of its files, applying it, undoing it and
  deleting its archive all answer 404, the same 404 as a run that does not
  exist. The run row is exactly as Olive left it. Olive reads it, opens the
  file, applies it and undoes it.
* Blake sees Olive's run and may start one once Olive makes Blake an admin, and
  not after the grant is taken away.
* Apply lands a provider under "me"; any other value is refused with 422 and
  nothing is written.

The routes, the background preview and apply, the ledger and the owner-or-admin
check run for real against a practice schema provisioned the production way and
a practice registered in ``platform.practices``, on the suite's non-superuser
role. Only sign-in is stood in: whoever ``acting`` names is the caller.

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
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models import User
    from fastapi.testclient import TestClient
    from httpx import Response
    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured; testcontainers sets DATABASE_URL and DATABASE_BACKEND.",
)

os.environ.setdefault("ENVIRONMENT", "development")

_SUFFIX = uuid.uuid4().hex[:8]
_PRACTICE_ID = f"import-visibility-{_SUFFIX}"
_OLIVE = str(uuid.uuid4())
_AVERY = str(uuid.uuid4())
_BLAKE = str(uuid.uuid4())
_EMAILS = {
    _OLIVE: f"olive-{_SUFFIX}@example.test",
    _AVERY: f"avery-{_SUFFIX}@example.test",
    _BLAKE: f"blake-{_SUFFIX}@example.test",
}
_MAIN = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "simplepractice_export"
_PABLO_A = "112092194"


def _user(user_id: str) -> User:
    from app.models import User  # noqa: PLC0415

    return User(
        id=user_id,
        email=_EMAILS[user_id],
        name="Test Clinician",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def schema(engine: Engine) -> Iterator[str]:
    """A fresh practice per test, registered with Olive as its owner."""
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    name = f"practice_test_import_vis_{uuid.uuid4().hex[:10]}"
    create_practice_schema(engine, name)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform.practices (id, name, schema_name, owner_email,"
                " owner_user_id, product, status, is_active, created_at)"
                " VALUES (:i, 'Import Visibility', :s, :e, CAST(:o AS uuid), 'pablo',"
                " 'active', true, now())"
            ),
            {"i": _PRACTICE_ID, "s": name, "e": _EMAILS[_OLIVE], "o": _OLIVE},
        )
    yield name
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM platform.practices WHERE id = :p"), {"p": _PRACTICE_ID})
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))


@pytest.fixture
def acting() -> dict[str, User]:
    return {"user": _user(_OLIVE)}


@pytest.fixture
def client(
    schema: str, acting: dict[str, User], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
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
    from fastapi.testclient import TestClient  # noqa: PLC0415

    monkeypatch.setattr(
        "app.db.middleware._resolve_schema_from_request", lambda _r: (schema, "resolved")
    )
    monkeypatch.setattr("app.migration.jobs.archive_base", lambda: tmp_path)
    monkeypatch.setattr("app.routes.migration.archive_base", lambda: tmp_path)

    def _ctx() -> TenantContext:
        user_id = acting["user"].id
        arm_current_user_id(get_db_session(), user_id)
        return TenantContext(user_id=user_id, practice_id=_PRACTICE_ID, practice_schema=schema)

    app.dependency_overrides[get_current_user_id] = lambda: acting["user"].id
    for dep in (
        get_current_user,
        get_current_user_no_mfa,
        require_active_subscription,
        require_baa_acceptance,
    ):
        app.dependency_overrides[dep] = lambda: acting["user"]
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


def _as(acting: dict[str, User], user_id: str) -> None:
    acting["user"] = _user(user_id)


def _start(client: TestClient) -> Response:
    return client.post(
        "/api/migration/runs",
        files={"file": ("export.zip", _zip(_MAIN), "application/zip")},
        data={"scope": "patients"},
    )


def _olive_uploads(client: TestClient, acting: dict[str, User]) -> dict[str, Any]:
    _as(acting, _OLIVE)
    resp = _start(client)
    assert resp.status_code == 202, resp.text
    run = client.get(f"/api/migration/runs/{resp.json()['id']}")
    assert run.status_code == 200, run.text
    body: dict[str, Any] = run.json()
    assert body["state"] == "previewed", body.get("error")
    return body


def _decisions(run: dict[str, Any], provider: str = "me") -> dict[str, Any]:
    assignments = {
        f"{r['record_type']}:{r['source_id']}": _PABLO_A
        for g in run["preview"]["questions"]["same_name"]
        for r in g["records"]
    }
    return {"providers": {"Avery Provider": provider}, "assignments": assignments}


def _a_file(run: dict[str, Any]) -> str:
    return next(r["path"] for r in run["preview"]["records"] if r["path"].endswith(".pdf"))


def _listed(client: TestClient) -> set[str]:
    resp = client.get("/api/migration/runs")
    assert resp.status_code == 200, resp.text
    return {r["id"] for r in resp.json()["runs"]}


def _row(engine: Engine, schema: str, run_id: str) -> dict[str, Any]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT state, archive_ref, archive_expires_at, decisions, report,"  # noqa: S608 — schema name, not user input
                f" finished_at FROM {schema}.import_runs WHERE id = CAST(:i AS uuid)"
            ),
            {"i": run_id},
        ).one()
    return dict(row._mapping)


def _run_requests(client: TestClient, run: dict[str, Any]) -> dict[str, Response]:
    run_id = run["id"]
    return {
        "detail": client.get(f"/api/migration/runs/{run_id}"),
        "file": client.get(f"/api/migration/runs/{run_id}/files", params={"path": _a_file(run)}),
        "apply": client.post(f"/api/migration/runs/{run_id}/apply", json=_decisions(run)),
        "undo": client.post(f"/api/migration/runs/{run_id}/undo", json={}),
        "archive": client.delete(f"/api/migration/runs/{run_id}/archive"),
    }


# --------------------------------------------------------------------------- journeys


def _runs_and_patients(engine: Engine, schema: str) -> tuple[int, int]:
    with engine.connect() as conn:
        return (
            conn.execute(text(f"SELECT count(*) FROM {schema}.import_runs")).scalar_one(),  # noqa: S608 — schema name, not user input
            conn.execute(text(f"SELECT count(*) FROM {schema}.patients")).scalar_one(),  # noqa: S608 — schema name, not user input
        )


def test_a_clinician_who_is_not_owner_or_admin_cannot_start_an_import(
    client: TestClient, acting: dict[str, User], engine: Engine, schema: str
) -> None:
    _as(acting, _AVERY)

    resp = _start(client)

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "IMPORT_OWNER_ONLY"
    assert _runs_and_patients(engine, schema) == (0, 0)


@pytest.mark.parametrize("who", [_AVERY, _BLAKE], ids=["avery", "blake"])
def test_a_clinician_who_is_not_owner_or_admin_cannot_reach_a_run(
    client: TestClient, acting: dict[str, User], engine: Engine, schema: str, who: str
) -> None:
    run = _olive_uploads(client, acting)
    before = _row(engine, schema, run["id"])
    assert before["archive_ref"] is not None

    _as(acting, who)
    assert _listed(client) == set()
    nobody = client.get(f"/api/migration/runs/{uuid.uuid4()}")
    assert nobody.status_code == 404
    for name, resp in _run_requests(client, run).items():
        assert resp.status_code == 404, (name, resp.text)
        if name != "file":
            assert resp.json() == nobody.json(), name
    assert _row(engine, schema, run["id"]) == before

    # Olive, meanwhile, has it all.
    _as(acting, _OLIVE)
    assert _listed(client) == {run["id"]}
    viewed = client.get(f"/api/migration/runs/{run['id']}/files", params={"path": _a_file(run)})
    assert viewed.status_code == 200
    assert viewed.content.startswith(b"%PDF")
    applied = client.post(f"/api/migration/runs/{run['id']}/apply", json=_decisions(run))
    assert applied.status_code == 202, applied.text
    done = client.get(f"/api/migration/runs/{run['id']}").json()
    assert done["state"] == "applied", done.get("error")

    # Applied, it is still out of reach: no read, no undo.
    _as(acting, who)
    assert client.get(f"/api/migration/runs/{run['id']}").status_code == 404
    assert client.post(f"/api/migration/runs/{run['id']}/undo", json={}).status_code == 404
    assert _row(engine, schema, run["id"])["state"] == "applied"

    _as(acting, _OLIVE)
    undone = client.post(f"/api/migration/runs/{run['id']}/undo", json={})
    assert undone.status_code == 200, undone.text
    assert undone.json()["state"] == "undone"


@pytest.mark.parametrize("provider", [_AVERY, str(uuid.uuid4())], ids=["member", "stranger"])
def test_apply_refuses_any_provider_value_but_me_and_writes_nothing(
    client: TestClient, acting: dict[str, User], engine: Engine, schema: str, provider: str
) -> None:
    run = _olive_uploads(client, acting)
    before = _row(engine, schema, run["id"])

    resp = client.post(f"/api/migration/runs/{run['id']}/apply", json=_decisions(run, provider))

    assert resp.status_code == 422, resp.text
    assert _row(engine, schema, run["id"]) == before
    assert _runs_and_patients(engine, schema) == (1, 0)


def test_an_admin_imports_until_the_grant_is_taken_away(
    client: TestClient, acting: dict[str, User], engine: Engine, schema: str
) -> None:
    run = _olive_uploads(client, acting)

    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {schema}.practice_admins (user_id, granted_by, granted_at)"  # noqa: S608 — schema name, not user input
                " VALUES (CAST(:u AS uuid), CAST(:o AS uuid), now())"
            ),
            {"u": _BLAKE, "o": _OLIVE},
        )
    _as(acting, _BLAKE)
    assert _listed(client) == {run["id"]}
    detail = client.get(f"/api/migration/runs/{run['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["preview"] == run["preview"]
    assert _start(client).status_code == 202

    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {schema}.practice_admins"))  # noqa: S608 — schema name, not user input
    assert _listed(client) == set()
    assert client.get(f"/api/migration/runs/{run['id']}").status_code == 404
    assert _start(client).status_code == 403
