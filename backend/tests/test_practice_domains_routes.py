# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Settings > Domains: the routes a practice manages its own hosts with.

Mounts the real router on a fresh app with auth, the repository and the audit
service overridden. Ownership goes through the real owner check, with the
practice row and the email-to-practice mapping patched in, the same way
``test_user_profile.py`` exercises it. The Postgres repository, the partial
unique index and the migration are proven in
``tests_integration/database/test_practice_domains_db.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import require_active_subscription
from app.models.audit import AuditAction, ResourceType
from app.models.practice_domain import PracticeDomain
from app.repositories.practice_domain import InMemoryPracticeDomainRepository
from app.routes import practice_domains
from app.services.audit_service import get_audit_service
from app.services.practice_domain_service import (
    PracticeDomainService,
    get_practice_domain_service,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models import User

PRACTICE_ID = "practice-1"
OTHER_PRACTICE = "practice-2"
URL = "/api/practice/domains"
CNAME_TARGET = "sites.example.net"


class _RecordingAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def log(self, action: Any, *_args: Any, **kwargs: Any) -> None:
        self.entries.append({"action": action, **kwargs})


@pytest.fixture
def repo() -> InMemoryPracticeDomainRepository:
    return InMemoryPracticeDomainRepository()


@pytest.fixture
def audit() -> _RecordingAudit:
    return _RecordingAudit()


@pytest.fixture
def owner_email() -> str:
    """The practice's registered owner; the test user is test@example.com."""
    return "test@example.com"


@pytest.fixture
def client(
    mock_user: User,
    repo: InMemoryPracticeDomainRepository,
    audit: _RecordingAudit,
    owner_email: str,
) -> Iterator[TestClient]:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(practice_domains.router)
    service = PracticeDomainService(
        repo,
        reserved_hosts=frozenset({"app.example.org", CNAME_TARGET}),
        cname_target=CNAME_TARGET,
    )
    app.dependency_overrides[require_active_subscription] = lambda: mock_user
    app.dependency_overrides[get_practice_domain_service] = lambda: service
    app.dependency_overrides[get_audit_service] = lambda: audit

    session = MagicMock()
    session.get.return_value = SimpleNamespace(id=PRACTICE_ID, owner_email=owner_email)
    with (
        patch(
            "app.auth.service._resolve_practice_from_email",
            return_value=(PRACTICE_ID, "practice_1"),
        ),
        patch("app.db.get_db_session", return_value=session),
    ):
        yield TestClient(app)


def _row(domain: str, **overrides: Any) -> PracticeDomain:
    fields: dict[str, Any] = {
        "domain": domain,
        "practice_id": PRACTICE_ID,
        "purpose": "portal",
        "kind": "vanity",
        "status": "active",
        "is_primary": False,
        "created_at": datetime(2026, 9, 1, tzinfo=UTC),
    }
    fields.update(overrides)
    return PracticeDomain(**fields)


def _by_domain(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {d["domain"]: d for d in body["domains"]}


def _code(response: Any) -> str:
    return response.json()["error"]["code"]


class TestList:
    def test_lists_only_this_practices_hosts_with_records(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        repo.put(_row("portal.ours.example", is_primary=True))
        repo.put(_row("portal.theirs.example", practice_id=OTHER_PRACTICE))

        response = client.get(URL)

        assert response.status_code == 200
        domains = response.json()["domains"]
        assert [d["domain"] for d in domains] == ["portal.ours.example"]
        assert domains[0]["is_primary"] is True
        assert domains[0]["dns_records"] == [
            {"type": "CNAME", "name": "portal.ours.example", "value": CNAME_TARGET}
        ]

    def test_without_a_target_there_are_no_records_to_show(
        self, mock_user: User, repo: InMemoryPracticeDomainRepository
    ) -> None:
        domain = _row("portal.ours.example")
        assert PracticeDomainService(repo).dns_records(domain) == []

    @pytest.mark.parametrize("owner_email", ["someone-else@example.com"])
    def test_a_non_owner_can_read(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        repo.put(_row("portal.ours.example"))
        assert client.get(URL).status_code == 200


class TestAdd:
    def test_adds_a_pending_portal_host_and_audits_it(
        self, client: TestClient, audit: _RecordingAudit
    ) -> None:
        response = client.post(URL, json={"domain": "Portal.Ours.Example.", "purpose": "portal"})

        assert response.status_code == 201
        added = _by_domain(response.json())["portal.ours.example"]
        assert added["status"] == "pending"
        assert added["is_primary"] is False
        assert added["purpose"] == "portal"
        assert audit.entries == [
            {
                "action": AuditAction.PRACTICE_DOMAIN_ADDED,
                "resource_type": ResourceType.PRACTICE,
                "resource_id": PRACTICE_ID,
                "changes": {"domain": "portal.ours.example", "purpose": "portal"},
            }
        ]

    def test_a_website_apex_brings_its_www_alias(self, client: TestClient) -> None:
        response = client.post(URL, json={"domain": "ours.example", "purpose": "site"})

        assert response.status_code == 201
        domains = _by_domain(response.json())
        assert set(domains) == {"ours.example", "www.ours.example"}
        assert all(d["purpose"] == "site" and not d["is_primary"] for d in domains.values())

    def test_the_www_alias_can_be_declined(self, client: TestClient) -> None:
        response = client.post(
            URL, json={"domain": "ours.example", "purpose": "site", "include_www": False}
        )
        assert set(_by_domain(response.json())) == {"ours.example"}

    def test_a_longer_website_name_gets_www_only_when_asked(self, client: TestClient) -> None:
        plain = client.post(URL, json={"domain": "ours.co.example", "purpose": "site"})
        assert set(_by_domain(plain.json())) == {"ours.co.example"}

        asked = client.post(
            URL, json={"domain": "theirs.co.example", "purpose": "site", "include_www": True}
        )
        assert "www.theirs.co.example" in _by_domain(asked.json())

    def test_a_portal_host_never_gets_www(self, client: TestClient) -> None:
        response = client.post(
            URL, json={"domain": "ours.example", "purpose": "portal", "include_www": True}
        )
        assert set(_by_domain(response.json())) == {"ours.example"}

    def test_a_www_alias_already_held_is_left_alone(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        repo.put(_row("www.ours.example", purpose="site", status="active"))
        response = client.post(URL, json={"domain": "ours.example", "purpose": "site"})

        assert response.status_code == 201
        assert _by_domain(response.json())["www.ours.example"]["status"] == "active"

    def test_a_host_another_practice_holds_is_409_and_writes_nothing(
        self,
        client: TestClient,
        repo: InMemoryPracticeDomainRepository,
        audit: _RecordingAudit,
    ) -> None:
        repo.put(_row("www.taken.example", practice_id=OTHER_PRACTICE, purpose="site"))

        response = client.post(URL, json={"domain": "taken.example", "purpose": "site"})

        assert response.status_code == 409
        assert _code(response) == "DOMAIN_TAKEN"
        assert repo.get("taken.example") is None
        assert audit.entries == []

    def test_adding_a_host_twice_is_409(self, client: TestClient) -> None:
        client.post(URL, json={"domain": "portal.ours.example", "purpose": "portal"})
        again = client.post(URL, json={"domain": "portal.ours.example", "purpose": "site"})

        assert again.status_code == 409
        assert _code(again) == "DOMAIN_ALREADY_ADDED"

    @pytest.mark.parametrize(
        "domain", ["https://ours.example/path", "192.0.2.1", "*.ours.example", "app.example.org"]
    )
    def test_an_unusable_name_is_422_with_words(self, client: TestClient, domain: str) -> None:
        response = client.post(URL, json={"domain": domain, "purpose": "portal"})

        assert response.status_code == 422
        assert _code(response) == "DOMAIN_INVALID"
        assert response.json()["error"]["message"]

    def test_an_unknown_purpose_is_refused(self, client: TestClient) -> None:
        response = client.post(URL, json={"domain": "ours.example", "purpose": "email"})
        assert response.status_code == 422

    @pytest.mark.parametrize("owner_email", ["someone-else@example.com"])
    def test_a_non_owner_cannot_add(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        response = client.post(URL, json={"domain": "ours.example", "purpose": "portal"})

        assert response.status_code == 403
        assert _code(response) == "NOT_PRACTICE_OWNER"
        assert repo.get("ours.example") is None


class TestPrimary:
    def test_an_active_host_becomes_the_only_primary_for_its_purpose(
        self,
        client: TestClient,
        repo: InMemoryPracticeDomainRepository,
        audit: _RecordingAudit,
    ) -> None:
        repo.put(_row("old.ours.example", is_primary=True))
        repo.put(_row("new.ours.example"))
        repo.put(_row("ours.example", purpose="site", is_primary=True))

        response = client.post(f"{URL}/new.ours.example/primary")

        assert response.status_code == 200
        domains = _by_domain(response.json())
        assert domains["new.ours.example"]["is_primary"] is True
        assert domains["old.ours.example"]["is_primary"] is False
        # The website's primary is a different purpose and stays.
        assert domains["ours.example"]["is_primary"] is True
        assert audit.entries[-1]["action"] == AuditAction.PRACTICE_DOMAIN_MADE_PRIMARY
        assert audit.entries[-1]["changes"] == {
            "domain": "new.ours.example",
            "purpose": "portal",
            "previous": "old.ours.example",
        }

    @pytest.mark.parametrize("status", ["pending", "verifying", "error"])
    def test_a_host_that_is_not_active_cannot_be_primary(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository, status: str
    ) -> None:
        repo.put(_row("new.ours.example", status=status))

        response = client.post(f"{URL}/new.ours.example/primary")

        assert response.status_code == 409
        assert _code(response) == "DOMAIN_NOT_ACTIVE"
        assert repo.get("new.ours.example").is_primary is False  # type: ignore[union-attr]  # put above

    def test_another_practices_host_is_404(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        repo.put(_row("theirs.example", practice_id=OTHER_PRACTICE))

        response = client.post(f"{URL}/theirs.example/primary")

        assert response.status_code == 404
        assert repo.get("theirs.example").is_primary is False  # type: ignore[union-attr]  # put above

    @pytest.mark.parametrize("owner_email", ["someone-else@example.com"])
    def test_a_non_owner_cannot_choose_the_primary(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        repo.put(_row("new.ours.example"))
        assert client.post(f"{URL}/new.ours.example/primary").status_code == 403


class TestRemove:
    def test_removing_the_primary_leaves_none(
        self,
        client: TestClient,
        repo: InMemoryPracticeDomainRepository,
        audit: _RecordingAudit,
    ) -> None:
        repo.put(_row("old.ours.example", is_primary=True))
        repo.put(_row("other.ours.example"))

        response = client.delete(f"{URL}/old.ours.example")

        assert response.status_code == 200
        domains = response.json()["domains"]
        assert [d["domain"] for d in domains] == ["other.ours.example"]
        assert not any(d["is_primary"] for d in domains)
        assert audit.entries[-1]["action"] == AuditAction.PRACTICE_DOMAIN_REMOVED
        assert audit.entries[-1]["changes"]["was_primary"] is True

    def test_another_practices_host_is_404_and_stays(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        repo.put(_row("theirs.example", practice_id=OTHER_PRACTICE))

        assert client.delete(f"{URL}/theirs.example").status_code == 404
        assert repo.get("theirs.example") is not None

    @pytest.mark.parametrize("owner_email", ["someone-else@example.com"])
    def test_a_non_owner_cannot_remove(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        repo.put(_row("ours.example"))

        assert client.delete(f"{URL}/ours.example").status_code == 403
        assert repo.get("ours.example") is not None
