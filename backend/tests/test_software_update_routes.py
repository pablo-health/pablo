# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The update notice and the request behind its button.

* with ``self_update_enabled`` off, or no ``latest.json`` from the host, both
  routes answer 404, so the frontend shows nothing;
* a clinician who is neither the practice owner nor an admin gets 403;
* the owner sees whether the latest release is newer than the running one;
* asking for the update audits it and writes ``requests/update.json``, and is
  refused while an update runs or when there is nothing newer;
* release tags compare as numbers, so v1.10.0 is newer than v1.9.0.

The routes run against in-memory repositories with the practice row stood in,
as in ``test_practice_admin_routes.py``; the update directory is a temporary
one laid out as the host lays it out.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import get_current_user
from app.models import User
from app.models.audit import AuditAction, ResourceType
from app.routes import software_update as software_update_routes
from app.services.audit_service import AuditService, get_audit_service
from app.settings import Settings, get_settings
from app.version_check import is_newer_release
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from app.repositories.audit import InMemoryAuditRepository
    from httpx import Response

URL = "/api/software-update"
OWNER_ID = "11111111-1111-1111-1111-111111111111"
COLLEAGUE_ID = "22222222-2222-2222-2222-222222222222"


def _user(user_id: str, email: str) -> User:
    return User(
        id=user_id,
        email=email,
        name="Test Clinician",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )


OWNER = _user(OWNER_ID, "owner@example.com")
COLLEAGUE = _user(COLLEAGUE_ID, "colleague@example.com")


def _write(path: Path, payload: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


@pytest.fixture
def update_dir(tmp_path: Path) -> Path:
    """The host's layout, with v1.10.0 published and nothing under way."""
    (tmp_path / "requests").mkdir()
    _write(
        tmp_path / "host" / "latest.json",
        {
            "version": "v1.10.0",
            "notes_url": "https://github.com/example/pablo/releases/tag/v1.10.0",
            "checked_at": "2026-10-30T08:00:00Z",
        },
    )
    return tmp_path


@pytest.fixture
def settings(update_dir: Path) -> Settings:
    return get_settings().model_copy(
        update={"self_update_enabled": True, "self_update_dir": str(update_dir)}
    )


@pytest.fixture
def acting() -> dict[str, User]:
    """Who is signed in; a test switches it by assigning ``acting["user"]``."""
    return {"user": OWNER}


@pytest.fixture
def client(
    acting: dict[str, User],
    settings: Settings,
    audit_repo: InMemoryAuditRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("PABLO_VERSION", "v1.9.0")
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(software_update_routes.router)
    application.dependency_overrides[get_current_user] = lambda: acting["user"]
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)

    session = MagicMock()
    session.get.return_value = SimpleNamespace(
        id="practice-1", owner_email="owner@example.com", schema_name="practice_1"
    )
    with (
        patch(
            "app.auth.service._resolve_practice_from_email",
            return_value=("practice-1", "practice_1"),
        ),
        patch("app.db.get_db_session", return_value=session),
    ):
        yield TestClient(application)


def _code(response: Response) -> str:
    return str(response.json()["error"]["code"])


class TestWhenTheDeploymentCannotUpdateItself:
    def test_both_routes_404_with_the_setting_off(
        self, client: TestClient, settings: Settings, update_dir: Path
    ) -> None:
        settings.self_update_enabled = False
        assert client.get(URL).status_code == 404
        assert client.post(URL).status_code == 404
        assert not (update_dir / "requests" / "update.json").exists()

    def test_both_routes_404_before_the_host_has_checked(
        self, client: TestClient, update_dir: Path
    ) -> None:
        (update_dir / "host" / "latest.json").unlink()
        assert client.get(URL).status_code == 404
        assert client.post(URL).status_code == 404
        assert not (update_dir / "requests" / "update.json").exists()


class TestWhoMayAsk:
    def test_a_clinician_who_is_not_an_admin_is_refused(
        self, client: TestClient, acting: dict[str, User], update_dir: Path
    ) -> None:
        acting["user"] = COLLEAGUE
        assert client.get(URL).status_code == 403
        response = client.post(URL)
        assert response.status_code == 403
        assert _code(response) == "NOT_PRACTICE_ADMIN"
        assert not (update_dir / "requests" / "update.json").exists()


class TestTheNotice:
    def test_a_newer_release_is_available(self, client: TestClient) -> None:
        response = client.get(URL)
        assert response.status_code == 200
        assert response.json() == {
            "current": "v1.9.0",
            "latest": "v1.10.0",
            "notes_url": "https://github.com/example/pablo/releases/tag/v1.10.0",
            "available": True,
            "status": None,
        }

    def test_the_same_release_is_not_available(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PABLO_VERSION", "v1.10.0")
        assert client.get(URL).json()["available"] is False

    def test_reports_the_hosts_progress(self, client: TestClient, update_dir: Path) -> None:
        _write(
            update_dir / "host" / "status.json",
            {
                "state": "rolled_back",
                "from": "v1.9.0",
                "to": "v1.10.0",
                "message": "Pablo stayed on v1.9.0.",
                "at": "2026-10-30T09:00:00Z",
            },
        )
        status = client.get(URL).json()["status"]
        assert status["state"] == "rolled_back"
        assert status["from"] == "v1.9.0"
        assert status["to"] == "v1.10.0"


class TestAskingForTheUpdate:
    def test_writes_the_request_and_an_audit_row(
        self, client: TestClient, update_dir: Path, audit_repo: InMemoryAuditRepository
    ) -> None:
        response = client.post(URL)
        assert response.status_code == 202
        assert response.json() == {"requested": "v1.10.0"}

        request = json.loads((update_dir / "requests" / "update.json").read_text())
        assert request["requested_by"] == OWNER_ID
        assert datetime.fromisoformat(request["at"])
        # Written beside the request and renamed onto it, so no partial file stays.
        assert [p.name for p in (update_dir / "requests").iterdir()] == ["update.json"]

        (entry,) = audit_repo.list_for_user(OWNER_ID)
        assert entry.action == AuditAction.SOFTWARE_UPDATE_REQUESTED.value
        assert entry.resource_type == ResourceType.PRACTICE.value
        assert entry.resource_id == "practice-1"
        assert entry.patient_id is None
        assert entry.changes == {"from_version": "v1.9.0", "to_version": "v1.10.0"}

    def test_refused_while_an_update_runs(
        self, client: TestClient, update_dir: Path, audit_repo: InMemoryAuditRepository
    ) -> None:
        _write(
            update_dir / "host" / "status.json",
            {"state": "updating", "from": "v1.9.0", "to": "v1.10.0"},
        )
        response = client.post(URL)
        assert response.status_code == 409
        assert _code(response) == "UPDATE_IN_PROGRESS"
        assert not (update_dir / "requests" / "update.json").exists()
        assert audit_repo.list_for_user(OWNER_ID) == []

    def test_refused_when_nothing_is_newer(
        self, client: TestClient, update_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PABLO_VERSION", "v1.10.0")
        response = client.post(URL)
        assert response.status_code == 409
        assert _code(response) == "NO_NEWER_RELEASE"
        assert not (update_dir / "requests" / "update.json").exists()


class TestComparingReleases:
    @pytest.mark.parametrize(
        ("candidate", "current", "newer"),
        [
            ("v1.10.0", "v1.9.0", True),
            ("v1.9.0", "v1.10.0", False),
            ("v2.0.0", "v1.99.99", True),
            ("v1.2.1", "v1.2.0", True),
            ("v1.2.0", "v1.2.0", False),
            ("v1.2.0", "1.1.0", True),
            ("v1.3.0-rc.1", "v1.2.0", False),
            ("v1.3.0", "main", False),
        ],
    )
    def test_compares_as_numbers(self, candidate: str, current: str, newer: bool) -> None:
        assert is_newer_release(candidate, current) is newer
