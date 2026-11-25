# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Importing is the practice owner's and admins' act. Nobody else's.

A run's preview lists every client in the archive, and while the archive is
kept its files can be opened, so any other clinician, the one who started a
run before this rule included, is refused:

* starting an import answers 403 and keeps nothing;
* every route that takes a run id answers 404 (never 403, so ids cannot be
  probed), and nothing happens: no audit entry, no step dispatched, no undo,
  the run as it was;
* the history is empty.

The practice owner and an admin the owner named get through every route and
see every run. The owner-or-admin check runs for real against a practice row
stood in for the session, as in ``test_portal_and_scheduling_admin_gate.py``.
The same journeys run against Postgres in
``tests_integration/api/test_import_run_visibility.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from app.db.platform_models import PracticeRow
from app.routes import migration as migration_routes
from app.routes.patients import get_patient_repository
from app.services.audit_service import get_audit_service
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tests.user_fakes import clinician

if TYPE_CHECKING:
    from pathlib import Path

    from app.repositories.practice_admin import InMemoryPracticeAdminRepository

    from tests.audit_fakes import RecordingAudit

PRACTICE_ID = "practice-1"
TENANT = "practice_abc123"
OWNER_ID = "11111111-1111-1111-1111-111111111111"
AVERY_ID = "22222222-2222-2222-2222-222222222222"
BLAKE_ID = "33333333-3333-3333-3333-333333333333"
RUN_ID = "44444444-4444-4444-4444-444444444444"
BLAKE_RUN_ID = "55555555-5555-5555-5555-555555555555"

OWNER = clinician(OWNER_ID, "owner@example.com")
AVERY = clinician(AVERY_ID, "avery@example.com")
BLAKE = clinician(BLAKE_ID, "blake@example.com")

#: A client name in Avery's preview. Letters only, so a match in a body is proof.
SENTINEL = "Quillfeatherx"
ARCHIVE_FILE = "notes/referral.pdf"

#: Every route that takes a run id: (method, path, body, the state the run is
#: in so that the route proceeds, the status it answers when it does).
#: ``test_every_run_route_is_covered`` fails when a route is added without a
#: line here.
RUN_ROUTES = [
    ("GET", "/api/migration/runs/{run_id}", None, "previewed", 200),
    ("POST", "/api/migration/runs/{run_id}/apply", {}, "previewed", 202),
    ("POST", "/api/migration/runs/{run_id}/undo", {}, "applied", 200),
    ("DELETE", "/api/migration/runs/{run_id}/archive", None, "previewed", 200),
    ("GET", "/api/migration/runs/{run_id}/files?path=" + ARCHIVE_FILE, None, "previewed", 200),
]
_IDS = [f"{method} {path.split('?')[0]}" for method, path, *_ in RUN_ROUTES]


def _run(run_id: str, started_by: str, state: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=run_id,
        source_system="simplepractice",
        scope="patients",
        state=state,
        started_by=started_by,
        started_at=datetime(2026, 11, 1, tzinfo=UTC),
        finished_at=datetime(2026, 11, 1, 1, tzinfo=UTC) if state == "applied" else None,
        archive_ref="archive-ref",
        archive_expires_at=datetime(2026, 11, 3, tzinfo=UTC),
        preview={
            "records": [{"path": ARCHIVE_FILE}],
            "clients": [{"display_name": f"{SENTINEL} Client"}],
            "questions": {"same_name": [], "duplicates": [], "providers": []},
        },
        decisions=None,
        counts=None,
        report=None,
        error=None,
    )


class _Store:
    """The archive store, holding one unpacked archive."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.deleted: list[str | None] = []
        self.put_count = 0

    def put(self, _schema: str, _upload: Path, _password: str | None) -> str:
        self.put_count += 1
        return "archive-ref"

    def path(self, _schema: str, ref: str) -> Path | None:
        return self.root if ref else None

    def delete(self, _schema: str, ref: str | None) -> bool:
        self.deleted.append(ref)
        return True


#: The runs ``_Applier`` was asked to undo, cleared by ``harness``.
_UNDONE: list[str] = []


class _Applier:
    def __init__(self, **_kwargs: Any) -> None:
        pass

    def undo(self, *, run_id: str, **_kwargs: Any) -> dict[str, Any]:
        _UNDONE.append(run_id)
        return {"removed": 0, "kept": []}


@pytest.fixture
def harness(
    monkeypatch: pytest.MonkeyPatch, recording_audit: RecordingAudit, tmp_path: Path
) -> dict[str, Any]:
    acting = {"user": AVERY}
    runs: dict[str, SimpleNamespace] = {}
    dispatched: list[tuple[str, str]] = []
    _UNDONE.clear()

    archive = tmp_path / "archive"
    (archive / "notes").mkdir(parents=True)
    (archive / ARCHIVE_FILE).write_bytes(b"%PDF-1.4\n%%EOF\n")
    store = _Store(archive)

    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(migration_routes.router)
    overrides = application.dependency_overrides
    overrides[require_baa_acceptance] = lambda: acting["user"]
    overrides[get_tenant_context] = lambda: TenantContext(
        user_id=acting["user"].id, practice_id=PRACTICE_ID, practice_schema=TENANT
    )
    overrides[get_audit_service] = lambda: recording_audit
    patients = MagicMock()
    patients.get_multiple.return_value = {}
    overrides[get_patient_repository] = lambda: patients

    practice = SimpleNamespace(id=PRACTICE_ID, owner_email=OWNER.email, schema_name=TENANT)
    session = MagicMock()
    session.get.side_effect = lambda model, key: (
        practice if model is PracticeRow and key == PRACTICE_ID else None
    )

    def _new_run(_session: Any, *, started_by: str, **_kw: Any) -> SimpleNamespace:
        run = runs[RUN_ID] = _run(RUN_ID, started_by, "previewing")
        return run

    monkeypatch.setattr(migration_routes, "get_db_session", lambda: session)
    monkeypatch.setattr(migration_routes, "get_run", lambda _s, run_id: runs.get(run_id))
    monkeypatch.setattr(migration_routes, "list_runs", lambda _s: list(runs.values()))
    monkeypatch.setattr(migration_routes, "new_run", _new_run)
    monkeypatch.setattr(migration_routes, "client_records_for_run", lambda _s, _id: [])
    monkeypatch.setattr(migration_routes, "sweep_expired_archives", lambda *_a: None)
    monkeypatch.setattr(migration_routes, "archive_store", lambda: store)
    monkeypatch.setattr(migration_routes, "archive_base", lambda: tmp_path)
    monkeypatch.setattr(migration_routes, "ArchiveApplier", _Applier)
    monkeypatch.setattr(migration_routes, "release_db_connection", lambda: None)
    monkeypatch.setattr(
        migration_routes,
        "_dispatch",
        lambda _bg, step, _ctx, _user_id, run_id: dispatched.append((step, run_id)),
    )
    return {
        "client": TestClient(application),
        "acting": acting,
        "runs": runs,
        "store": store,
        "dispatched": dispatched,
        "audit": recording_audit,
    }


def _call(harness: dict[str, Any], method: str, path: str, body: Any, run_id: str) -> Any:
    return harness["client"].request(method, path.format(run_id=run_id), json=body)


def _snapshot(run: SimpleNamespace) -> dict[str, Any]:
    return dict(vars(run))


# --------------------------------------------------------------------------- per-run routes


@pytest.mark.parametrize("who", [AVERY, BLAKE], ids=["started-it", "colleague"])
@pytest.mark.parametrize(("method", "path", "body", "state", "ok"), RUN_ROUTES, ids=_IDS)
def test_a_clinician_who_is_not_owner_or_admin_gets_404_and_nothing_happens(
    harness: dict[str, Any], who: Any, method: str, path: str, body: Any, state: str, ok: int
) -> None:
    run = harness["runs"][RUN_ID] = _run(RUN_ID, AVERY_ID, state)
    before = _snapshot(run)
    harness["acting"]["user"] = who

    resp = _call(harness, method, path, body, RUN_ID)

    assert resp.status_code == 404, resp.text
    assert SENTINEL not in resp.text
    assert ARCHIVE_FILE not in resp.text
    assert _snapshot(run) == before
    assert harness["audit"].entries == []
    assert harness["dispatched"] == []
    assert harness["store"].deleted == []
    assert _UNDONE == []


@pytest.mark.parametrize(("method", "path", "body", "state", "ok"), RUN_ROUTES, ids=_IDS)
def test_the_404_is_the_same_as_a_run_that_does_not_exist(
    harness: dict[str, Any], method: str, path: str, body: Any, state: str, ok: int
) -> None:
    harness["runs"][RUN_ID] = _run(RUN_ID, AVERY_ID, state)
    harness["acting"]["user"] = BLAKE

    someone_elses = _call(harness, method, path, body, RUN_ID)
    missing = _call(harness, method, path, body, "66666666-6666-6666-6666-666666666666")

    assert someone_elses.status_code == missing.status_code == 404
    assert someone_elses.json() == missing.json()


@pytest.mark.parametrize(("method", "path", "body", "state", "ok"), RUN_ROUTES, ids=_IDS)
def test_the_practice_owner_gets_through_to_a_colleagues_run(
    harness: dict[str, Any], method: str, path: str, body: Any, state: str, ok: int
) -> None:
    harness["runs"][RUN_ID] = _run(RUN_ID, AVERY_ID, state)
    harness["acting"]["user"] = OWNER

    resp = _call(harness, method, path, body, RUN_ID)

    assert resp.status_code == ok, resp.text


@pytest.mark.parametrize(("method", "path", "body", "state", "ok"), RUN_ROUTES, ids=_IDS)
def test_an_admin_the_owner_named_gets_through_to_a_colleagues_run(
    harness: dict[str, Any],
    practice_admins: InMemoryPracticeAdminRepository,
    method: str,
    path: str,
    body: Any,
    state: str,
    ok: int,
) -> None:
    harness["runs"][RUN_ID] = _run(RUN_ID, AVERY_ID, state)
    practice_admins.grant(BLAKE_ID, OWNER_ID, datetime(2026, 11, 2, tzinfo=UTC))
    harness["acting"]["user"] = BLAKE

    resp = _call(harness, method, path, body, RUN_ID)

    assert resp.status_code == ok, resp.text
    assert len(harness["audit"].entries) == 1


def test_every_run_route_is_covered() -> None:
    """A new route that takes a run id has to be added to ``RUN_ROUTES``.

    Adding it there runs every case above against it, so a route that forgot
    the check fails by name rather than shipping unnoticed.
    """
    on_router = {
        (method, route.path)
        for route in migration_routes.router.routes
        if isinstance(route, APIRoute) and "{run_id}" in route.path
        for method in route.methods
    }
    covered = {(method, path.split("?")[0]) for method, path, *_ in RUN_ROUTES}
    assert on_router == covered


# --------------------------------------------------------------------------- start


def _start(harness: dict[str, Any]) -> Any:
    return harness["client"].post(
        "/api/migration/runs",
        files={"file": ("export.zip", b"PK\x05\x06" + b"\0" * 18, "application/zip")},
        data={"scope": "patients"},
    )


def test_a_clinician_who_is_not_owner_or_admin_cannot_start_an_import(
    harness: dict[str, Any],
) -> None:
    harness["acting"]["user"] = AVERY

    resp = _start(harness)

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "IMPORT_OWNER_ONLY"
    assert "practice owner" in resp.json()["error"]["message"]
    assert harness["store"].put_count == 0
    assert harness["runs"] == {}
    assert harness["audit"].entries == []
    assert harness["dispatched"] == []


def test_the_owner_starts_an_import(harness: dict[str, Any]) -> None:
    harness["acting"]["user"] = OWNER

    resp = _start(harness)

    assert resp.status_code == 202, resp.text
    assert harness["runs"][RUN_ID].started_by == OWNER_ID
    assert harness["dispatched"] == [("preview", RUN_ID)]


def test_an_admin_starts_an_import(
    harness: dict[str, Any], practice_admins: InMemoryPracticeAdminRepository
) -> None:
    practice_admins.grant(BLAKE_ID, OWNER_ID, datetime(2026, 11, 2, tzinfo=UTC))
    harness["acting"]["user"] = BLAKE

    assert _start(harness).status_code == 202


# --------------------------------------------------------------------------- apply


def test_apply_lands_a_provider_under_me(harness: dict[str, Any]) -> None:
    run = harness["runs"][RUN_ID] = _run(RUN_ID, AVERY_ID, "previewed")
    run.preview["questions"]["providers"] = [{"name": "Avery Provider"}]
    harness["acting"]["user"] = OWNER

    resp = harness["client"].post(
        f"/api/migration/runs/{RUN_ID}/apply", json={"providers": {"Avery Provider": "me"}}
    )

    assert resp.status_code == 202, resp.text
    assert harness["dispatched"] == [("apply", RUN_ID)]


@pytest.mark.parametrize("value", [AVERY_ID, "not-a-member"], ids=["colleague", "stranger"])
def test_apply_refuses_any_provider_value_but_me_and_writes_nothing(
    harness: dict[str, Any], value: str
) -> None:
    run = harness["runs"][RUN_ID] = _run(RUN_ID, AVERY_ID, "previewed")
    run.preview["questions"]["providers"] = [{"name": "Avery Provider"}]
    before = _snapshot(run)
    harness["acting"]["user"] = OWNER

    resp = harness["client"].post(
        f"/api/migration/runs/{RUN_ID}/apply", json={"providers": {"Avery Provider": value}}
    )

    assert resp.status_code == 422, resp.text
    assert _snapshot(run) == before
    assert harness["audit"].entries == []
    assert harness["dispatched"] == []


# --------------------------------------------------------------------------- history


def _listed(harness: dict[str, Any]) -> set[str]:
    resp = harness["client"].get("/api/migration/runs")
    assert resp.status_code == 200, resp.text
    return {r["id"] for r in resp.json()["runs"]}


@pytest.mark.parametrize("who", [AVERY, BLAKE], ids=["started-it", "colleague"])
def test_a_clinician_who_is_not_owner_or_admin_sees_an_empty_history(
    harness: dict[str, Any], who: Any
) -> None:
    harness["runs"][RUN_ID] = _run(RUN_ID, AVERY_ID, "previewed")
    harness["acting"]["user"] = who

    assert _listed(harness) == set()
    assert harness["audit"].changes == [{"runs": 0}]


def test_the_owner_sees_every_run_in_the_history(harness: dict[str, Any]) -> None:
    harness["runs"][RUN_ID] = _run(RUN_ID, AVERY_ID, "previewed")
    harness["runs"][BLAKE_RUN_ID] = _run(BLAKE_RUN_ID, BLAKE_ID, "previewed")
    harness["acting"]["user"] = OWNER

    assert _listed(harness) == {RUN_ID, BLAKE_RUN_ID}


def test_an_admin_sees_every_run_in_the_history(
    harness: dict[str, Any], practice_admins: InMemoryPracticeAdminRepository
) -> None:
    harness["runs"][RUN_ID] = _run(RUN_ID, AVERY_ID, "previewed")
    harness["runs"][BLAKE_RUN_ID] = _run(BLAKE_RUN_ID, BLAKE_ID, "previewed")
    practice_admins.grant(BLAKE_ID, OWNER_ID, datetime(2026, 11, 2, tzinfo=UTC))
    harness["acting"]["user"] = BLAKE

    assert _listed(harness) == {RUN_ID, BLAKE_RUN_ID}


def test_no_practice_row_means_no_imports(harness: dict[str, Any]) -> None:
    """A caller whose practice cannot be found is nobody's owner or admin."""
    harness["runs"][RUN_ID] = _run(RUN_ID, AVERY_ID, "previewed")
    harness["acting"]["user"] = OWNER
    harness["client"].app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id=OWNER_ID, practice_id="practice-gone", practice_schema=TENANT
    )

    assert _listed(harness) == set()
    resp = harness["client"].get(f"/api/migration/runs/{RUN_ID}")
    assert resp.status_code == 404
    assert _start(harness).status_code == 403
