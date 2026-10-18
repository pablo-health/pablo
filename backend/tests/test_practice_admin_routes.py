# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Who may make a clinician a practice admin, and what an admin may then do.

* the owner lists the practice's clinicians, grants admin and revokes it, and
  each grant and revoke is audited once;
* an admin, or any other clinician, can neither list, grant nor revoke;
* nobody grants or revokes the owner, and nobody outside the practice can be
  granted;
* ``require_practice_admin`` admits the owner and a granted admin, and refuses
  a plain clinician with 403.

The routes run against in-memory repositories with the practice row stood in;
the table and its place in the practice schema are proven against Postgres in
``tests_integration/api/test_practice_admin_routes.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import get_current_user, require_active_subscription
from app.models import User
from app.models.audit import AuditAction
from app.repositories import get_practice_admin_repository, get_user_repository
from app.repositories.user import InMemoryUserRepository
from app.routes import practice_admins as practice_admin_routes
from app.routes.users import require_practice_admin
from app.services.audit_service import get_audit_service
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.repositories.practice_admin import InMemoryPracticeAdminRepository

URL = "/api/practice/admins"
OWNER_ID = "11111111-1111-1111-1111-111111111111"
COLLEAGUE_ID = "22222222-2222-2222-2222-222222222222"
THIRD_ID = "33333333-3333-3333-3333-333333333333"
OUTSIDER_ID = "44444444-4444-4444-4444-444444444444"


def _user(user_id: str, email: str, name: str) -> User:
    return User(
        id=user_id,
        email=email,
        name=name,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )


OWNER = _user(OWNER_ID, "owner@example.com", "Olive Owner")
COLLEAGUE = _user(COLLEAGUE_ID, "colleague@example.com", "Casey Colleague")
THIRD = _user(THIRD_ID, "third@example.com", "Avery Third")
OUTSIDER = _user(OUTSIDER_ID, "outsider@example.com", "Nobody Here")


class _RecordingAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def log(self, action: Any, *_args: Any, **kwargs: Any) -> None:
        self.entries.append({"action": action, **kwargs})


@pytest.fixture
def audit() -> _RecordingAudit:
    return _RecordingAudit()


@pytest.fixture
def acting() -> dict[str, User]:
    """Who is signed in; a test switches it by assigning ``acting["user"]``."""
    return {"user": OWNER}


@pytest.fixture
def app(
    acting: dict[str, User],
    audit: _RecordingAudit,
    practice_admins: InMemoryPracticeAdminRepository,
) -> Iterator[FastAPI]:
    users = InMemoryUserRepository()
    for member in (OWNER, COLLEAGUE, THIRD, OUTSIDER):
        users.update(member)

    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(practice_admin_routes.router)

    @application.get("/gated")
    def gated(practice: Any = Depends(require_practice_admin)) -> dict[str, str]:
        return {"practice_id": practice.id}

    application.dependency_overrides[get_current_user] = lambda: acting["user"]
    application.dependency_overrides[require_active_subscription] = lambda: acting["user"]
    application.dependency_overrides[get_user_repository] = lambda: users
    application.dependency_overrides[get_practice_admin_repository] = lambda: practice_admins
    application.dependency_overrides[get_audit_service] = lambda: audit

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
        patch.object(practice_admin_routes, "get_db_session", return_value=session),
        patch.object(
            practice_admin_routes,
            "practice_clinicians",
            return_value=sorted([OWNER_ID, COLLEAGUE_ID, THIRD_ID]),
        ),
    ):
        yield application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _admins(response: Any) -> dict[str, bool]:
    return {c["email"]: c["is_admin"] for c in response.json()["clinicians"]}


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


class TestTheOwner:
    def test_lists_the_owner_first_and_nobody_else_an_admin(self, client: TestClient) -> None:
        response = client.get(URL)
        assert response.status_code == 200
        clinicians = response.json()["clinicians"]
        assert [c["email"] for c in clinicians] == [
            "owner@example.com",
            "third@example.com",
            "colleague@example.com",
        ]
        assert clinicians[0]["is_owner"] is True
        assert _admins(response) == {
            "owner@example.com": True,
            "third@example.com": False,
            "colleague@example.com": False,
        }

    def test_grants_and_revokes_each_audited_once(
        self, client: TestClient, audit: _RecordingAudit
    ) -> None:
        granted = client.put(f"{URL}/{COLLEAGUE_ID}")
        assert granted.status_code == 200
        assert _admins(granted)["colleague@example.com"] is True
        # Granting again changes nothing and records nothing new.
        assert client.put(f"{URL}/{COLLEAGUE_ID}").status_code == 200

        revoked = client.delete(f"{URL}/{COLLEAGUE_ID}")
        assert revoked.status_code == 200
        assert _admins(revoked)["colleague@example.com"] is False
        assert client.delete(f"{URL}/{COLLEAGUE_ID}").status_code == 200

        assert [(e["action"], e["changes"]) for e in audit.entries] == [
            (AuditAction.PRACTICE_ADMIN_GRANTED, {"user_id": COLLEAGUE_ID}),
            (AuditAction.PRACTICE_ADMIN_REVOKED, {"user_id": COLLEAGUE_ID}),
        ]
        assert {e["resource_id"] for e in audit.entries} == {"practice-1"}

    def test_cannot_grant_or_revoke_the_owner(
        self, client: TestClient, audit: _RecordingAudit
    ) -> None:
        for method in ("put", "delete"):
            response = client.request(method, f"{URL}/{OWNER_ID}")
            assert response.status_code == 400, method
            assert _code(response) == "PRACTICE_OWNER"
        assert audit.entries == []

    def test_cannot_grant_someone_outside_the_practice(
        self, client: TestClient, practice_admins: InMemoryPracticeAdminRepository
    ) -> None:
        response = client.put(f"{URL}/{OUTSIDER_ID}")
        assert response.status_code == 404
        assert _code(response) == "NOT_A_CLINICIAN"
        assert practice_admins.list_admin_ids() == set()


class TestAnAdmin:
    @pytest.fixture(autouse=True)
    def _signed_in_as_admin(
        self, acting: dict[str, User], practice_admins: InMemoryPracticeAdminRepository
    ) -> None:
        practice_admins.grant(COLLEAGUE_ID, granted_by=OWNER_ID, granted_at=datetime.now(UTC))
        acting["user"] = COLLEAGUE

    def test_passes_the_practice_admin_dependency(self, client: TestClient) -> None:
        response = client.get("/gated")
        assert response.status_code == 200
        assert response.json() == {"practice_id": "practice-1"}

    def test_cannot_list_grant_or_revoke(
        self,
        client: TestClient,
        audit: _RecordingAudit,
        practice_admins: InMemoryPracticeAdminRepository,
    ) -> None:
        """Deciding who else may change settings stays with the owner."""
        assert client.get(URL).status_code == 403
        assert _code(client.put(f"{URL}/{THIRD_ID}")) == "NOT_PRACTICE_OWNER"
        assert _code(client.delete(f"{URL}/{COLLEAGUE_ID}")) == "NOT_PRACTICE_OWNER"
        assert practice_admins.list_admin_ids() == {COLLEAGUE_ID}
        assert audit.entries == []


class TestAPlainClinician:
    @pytest.fixture(autouse=True)
    def _signed_in_as_colleague(self, acting: dict[str, User]) -> None:
        acting["user"] = COLLEAGUE

    def test_is_refused_by_the_practice_admin_dependency(self, client: TestClient) -> None:
        response = client.get("/gated")
        assert response.status_code == 403
        assert _code(response) == "NOT_PRACTICE_ADMIN"

    def test_cannot_make_themself_an_admin(
        self, client: TestClient, practice_admins: InMemoryPracticeAdminRepository
    ) -> None:
        response = client.put(f"{URL}/{COLLEAGUE_ID}")
        assert response.status_code == 403
        assert _code(response) == "NOT_PRACTICE_OWNER"
        assert practice_admins.list_admin_ids() == set()

    def test_the_owner_passes_the_dependency(
        self, client: TestClient, acting: dict[str, User]
    ) -> None:
        acting["user"] = OWNER
        assert client.get("/gated").status_code == 200
