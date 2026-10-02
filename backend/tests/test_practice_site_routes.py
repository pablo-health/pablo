# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Settings > Website: who may do what, and how a refusal reads.

Mounts the real router with auth and the service overridden. Ownership goes
through the real owner check (the same one Settings > Domains uses), with the
practice row and the email-to-practice mapping patched in. The service itself
— publishing, rolling back, tidying and auditing — is proven against Postgres
in ``tests_integration/database/test_practice_sites_db.py``.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import require_active_subscription
from app.sites import files, routes
from app.sites.service import (
    NoDraftError,
    SiteDraft,
    SiteNotConfiguredError,
    SiteStatus,
    SiteVersion,
    UnknownVersionError,
    get_practice_site_service,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models import User

PRACTICE_ID = "practice-1"
URL = "/api/practice/website"
WHEN = datetime(2026, 9, 1, tzinfo=UTC)


class _StubService:
    """Records what it was asked; answers a fixed status."""

    enabled = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.refusal: Exception | None = None

    def _call(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if self.refusal is not None:
            raise self.refusal

    def status(self, practice_id: str) -> SiteStatus:
        return SiteStatus(
            live_version=2,
            live_host="www.example.com",
            has_active_host=True,
            draft=SiteDraft(file_count=3, total_bytes=120, uploaded_at=WHEN),
            versions=[
                SiteVersion(2, 3, 120, WHEN, "u"),
                SiteVersion(1, 2, 80, WHEN, "u"),
            ],
        )

    def save_draft(self, practice_id: str, site: Any, *_: Any) -> None:
        self._call("save_draft", practice_id, sorted(site.files))

    def discard_draft(self, practice_id: str) -> None:
        self._call("discard_draft", practice_id)

    def mint_preview(self, practice_id: str) -> tuple[str, datetime]:
        self._call("mint_preview", practice_id)
        return "tok", WHEN

    def publish_draft(self, practice_id: str, *_: Any) -> None:
        self._call("publish_draft", practice_id)

    def roll_back(self, practice_id: str, version: int, *_: Any) -> None:
        self._call("roll_back", practice_id, version)


@pytest.fixture
def service() -> _StubService:
    return _StubService()


@pytest.fixture
def owner_email() -> str:
    """The practice's registered owner; the test user is test@example.com."""
    return "test@example.com"


@pytest.fixture
def client(mock_user: User, service: _StubService, owner_email: str) -> Iterator[TestClient]:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(routes.router)
    app.dependency_overrides[require_active_subscription] = lambda: mock_user
    app.dependency_overrides[get_practice_site_service] = lambda: service
    session = MagicMock()
    session.get.return_value = SimpleNamespace(id=PRACTICE_ID, owner_email=owner_email)
    with (
        patch(
            "app.auth.service._resolve_practice_from_email",
            return_value=(PRACTICE_ID, "practice_1"),
        ),
        patch("app.db.get_db_session", return_value=session),
        patch.object(routes, "tidy_practice_site") as tidy,
    ):
        client = TestClient(app)
        client.tidy = tidy  # type: ignore[attr-defined]  # lets a test see the background task
        yield client


def _zip(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return out.getvalue()


def _upload(client: TestClient, data: bytes) -> Any:
    return client.post(f"{URL}/draft", files={"file": ("site.zip", data, "application/zip")})


def _code(response: Any) -> str:
    return response.json()["error"]["code"]


CHANGES = [
    ("delete", f"{URL}/draft"),
    ("post", f"{URL}/draft/preview"),
    ("post", f"{URL}/publish"),
    ("post", f"{URL}/versions/1/live"),
]


class TestTheOwner:
    def test_reads_the_status(self, client: TestClient) -> None:
        body = client.get(URL).json()
        assert body["enabled"] is True
        assert body["live_host"] == "www.example.com"
        assert [(v["version"], v["is_live"]) for v in body["versions"]] == [(2, True), (1, False)]
        assert body["draft"]["file_count"] == 3

    def test_uploads_a_draft_and_it_is_tidied_after(
        self, client: TestClient, service: _StubService
    ) -> None:
        response = _upload(client, _zip({"index.html": b"hi", "a.css": b"x"}))
        assert response.status_code == 200
        assert service.calls == [("save_draft", (PRACTICE_ID, ["a.css", "index.html"]))]
        client.tidy.assert_called_once_with(PRACTICE_ID)  # type: ignore[attr-defined]

    def test_publishes_previews_discards_and_rolls_back(
        self, client: TestClient, service: _StubService
    ) -> None:
        for method, url in CHANGES:
            assert client.request(method, url).status_code == 200, url
        assert [name for name, _ in service.calls] == [
            "discard_draft",
            "mint_preview",
            "publish_draft",
            "roll_back",
        ]
        preview = client.post(f"{URL}/draft/preview").json()
        assert preview["path"] == "/api/practice/website/preview/tok/"


class TestSomeoneElse:
    @pytest.fixture
    def owner_email(self) -> str:
        return "someone-else@example.com"

    def test_can_read_the_status(self, client: TestClient) -> None:
        assert client.get(URL).status_code == 200

    def test_cannot_upload(self, client: TestClient, service: _StubService) -> None:
        response = _upload(client, _zip({"index.html": b"hi"}))
        assert response.status_code == 403
        assert _code(response) == "NOT_PRACTICE_OWNER"
        assert service.calls == []

    @pytest.mark.parametrize(("method", "url"), CHANGES)
    def test_cannot_change_anything(
        self, client: TestClient, service: _StubService, method: str, url: str
    ) -> None:
        response = client.request(method, url)
        assert response.status_code == 403
        assert service.calls == []


class TestRefusals:
    def test_a_zip_that_is_not_a_website_says_why(self, client: TestClient) -> None:
        response = _upload(client, _zip({"home.html": b"hi"}))
        assert response.status_code == 422
        assert _code(response) == "INVALID_SITE"
        assert "index.html" in response.json()["error"]["message"]

    def test_too_large_is_413(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(routes, "MAX_ARCHIVE_BYTES", 10)
        monkeypatch.setattr(files, "MAX_ARCHIVE_BYTES", 10)
        response = _upload(client, b"x" * 11)
        assert response.status_code == 413

    @pytest.mark.parametrize(
        ("refusal", "status", "code"),
        [
            (SiteNotConfiguredError(), 503, "SITE_NOT_CONFIGURED"),
            (NoDraftError(), 409, "NO_DRAFT"),
            (UnknownVersionError(), 404, "NO_SUCH_VERSION"),
        ],
    )
    def test_the_service_refusing(
        self,
        client: TestClient,
        service: _StubService,
        refusal: Exception,
        status: int,
        code: str,
    ) -> None:
        service.refusal = refusal
        response = client.post(f"{URL}/publish")
        assert response.status_code == status
        assert _code(response) == code
