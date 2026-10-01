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
from app.models.practice_domain import PracticeDomain, PracticeDomainApex
from app.repositories.practice_domain import InMemoryPracticeDomainRepository
from app.routes import practice_domains
from app.services.audit_service import get_audit_service
from app.services.practice_domain_dns import get_dns_lookup
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
def zone() -> dict[tuple[str, str], list[str] | None]:
    """What the stub DNS answers, by (name, type). Absent is an empty answer;
    ``None`` is no answer in time."""
    return {}


@pytest.fixture
def client(
    mock_user: User,
    repo: InMemoryPracticeDomainRepository,
    audit: _RecordingAudit,
    owner_email: str,
    zone: dict[tuple[str, str], list[str] | None],
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
    app.dependency_overrides[get_dns_lookup] = lambda: (
        lambda name, rdtype: zone.get((name, rdtype), [])
    )

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
        # No domain row was written for it, so there is no ownership record yet.
        assert domains[0]["dns_records"] == [
            {
                "type": "CNAME",
                "name": "portal.ours.example",
                "value": CNAME_TARGET,
                "check": None,
                "found": None,
            }
        ]
        assert domains[0]["apex"] == "ours.example"

    def test_without_a_target_there_are_no_records_to_show(
        self, mock_user: User, repo: InMemoryPracticeDomainRepository
    ) -> None:
        domain = _row("portal.ours.example")
        assert PracticeDomainService(repo).dns_records(domain) == []

    def test_a_bare_domain_gets_address_records_and_an_alias_alternative(
        self, mock_user: User, repo: InMemoryPracticeDomainRepository
    ) -> None:
        """Many DNS providers refuse a CNAME on a bare domain, so it is shown
        A/AAAA records, with the CNAME target offered as an ALIAS/ANAME."""
        service = PracticeDomainService(
            repo, cname_target=CNAME_TARGET, apex_ips=("203.0.113.7", "2001:db8::7")
        )
        bare = _row("ours.example")

        assert [(r.type, r.name, r.value) for r in service.dns_records(bare)] == [
            ("A", "ours.example", "203.0.113.7"),
            ("AAAA", "ours.example", "2001:db8::7"),
        ]
        assert service.alias_alternative(bare) == CNAME_TARGET

    def test_a_name_under_a_domain_keeps_its_cname(
        self, mock_user: User, repo: InMemoryPracticeDomainRepository
    ) -> None:
        service = PracticeDomainService(repo, cname_target=CNAME_TARGET, apex_ips=("203.0.113.7",))
        sub = _row("portal.ours.example")

        assert [(r.type, r.value) for r in service.dns_records(sub)] == [("CNAME", CNAME_TARGET)]
        assert service.alias_alternative(sub) is None

    def test_without_addresses_a_bare_domain_is_shown_the_cname(
        self, mock_user: User, repo: InMemoryPracticeDomainRepository
    ) -> None:
        service = PracticeDomainService(repo, cname_target=CNAME_TARGET)
        bare = _row("ours.example")

        assert [r.type for r in service.dns_records(bare)] == ["CNAME"]
        assert service.alias_alternative(bare) is None

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


def _apex(apex: str, **overrides: Any) -> PracticeDomainApex:
    fields: dict[str, Any] = {
        "apex": apex,
        "practice_id": PRACTICE_ID,
        "verify_token": "tok-" + apex,
        "created_at": datetime(2026, 9, 1, tzinfo=UTC),
    }
    fields.update(overrides)
    return PracticeDomainApex(**fields)


def _records(domain: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(r["type"], r["name"], r["value"]) for r in domain["dns_records"]]


class TestDomainRows:
    """The registrable domain a host sits under: one practice each, written
    with the first host and released with the last."""

    def test_adding_a_host_writes_its_domain_with_a_token(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        response = client.post(
            URL, json={"domain": "portal.ours.example.co.uk", "purpose": "portal"}
        )

        assert response.status_code == 201
        apex = repo.get_apex("example.co.uk")
        assert apex is not None
        assert apex.practice_id == PRACTICE_ID
        assert len(apex.verify_token) >= 32
        added = _by_domain(response.json())["portal.ours.example.co.uk"]
        assert added["apex"] == "example.co.uk"
        assert (
            "TXT",
            "_pablo-verify.example.co.uk",
            f"pablo-verify={apex.verify_token}",
        ) in _records(added)

    def test_a_second_host_under_the_domain_keeps_its_token(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        client.post(URL, json={"domain": "portal.ours.example", "purpose": "portal"})
        token = repo.get_apex("ours.example").verify_token  # type: ignore[union-attr]  # written above
        client.post(URL, json={"domain": "book.ours.example", "purpose": "portal"})

        assert repo.get_apex("ours.example").verify_token == token  # type: ignore[union-attr]  # written above

    def test_a_host_under_another_practices_domain_is_409_and_writes_nothing(
        self,
        client: TestClient,
        repo: InMemoryPracticeDomainRepository,
        audit: _RecordingAudit,
    ) -> None:
        repo.put_apex(_apex("theirs.example", practice_id=OTHER_PRACTICE))

        response = client.post(URL, json={"domain": "portal.theirs.example", "purpose": "portal"})

        assert response.status_code == 409
        assert _code(response) == "DOMAIN_TAKEN"
        assert repo.get("portal.theirs.example") is None
        assert audit.entries == []

    def test_a_public_suffix_is_not_a_domain_anyone_can_add(self, client: TestClient) -> None:
        response = client.post(URL, json={"domain": "co.uk", "purpose": "site"})

        assert response.status_code == 422
        assert _code(response) == "DOMAIN_INVALID"

    def test_a_bare_domain_under_a_two_label_suffix_brings_www(self, client: TestClient) -> None:
        response = client.post(URL, json={"domain": "ours.co.uk", "purpose": "site"})

        assert set(_by_domain(response.json())) == {"ours.co.uk", "www.ours.co.uk"}

    def test_the_domain_is_released_with_its_last_host(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        client.post(URL, json={"domain": "ours.example", "purpose": "site"})

        client.delete(f"{URL}/ours.example")
        assert repo.get_apex("ours.example") is not None  # www.ours.example is still there
        client.delete(f"{URL}/www.ours.example")
        assert repo.get_apex("ours.example") is None

    def test_the_domain_records_are_shown_once_on_the_bare_domain(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        repo.put(_row("portal.ours.example"))
        repo.put(_row("ours.example", purpose="site", created_at=datetime(2026, 9, 2, tzinfo=UTC)))
        repo.put_apex(_apex("ours.example", email_dkim_tokens=["k1", "k2", "k3"]))

        domains = _by_domain(client.get(URL).json())

        bare = _records(domains["ours.example"])
        assert ("TXT", "_pablo-verify.ours.example", "pablo-verify=tok-ours.example") in bare
        assert [r for r in bare if "_domainkey" in r[1]] == [
            ("CNAME", f"{k}._domainkey.ours.example", f"{k}.dkim.amazonses.com")
            for k in ("k1", "k2", "k3")
        ]
        assert _records(domains["portal.ours.example"]) == [
            ("CNAME", "portal.ours.example", CNAME_TARGET)
        ]
        assert domains["portal.ours.example"]["apex"] == "ours.example"

    def test_without_the_bare_domain_the_oldest_host_shows_them(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        repo.put(_row("portal.ours.example"))
        repo.put(_row("book.ours.example", created_at=datetime(2026, 9, 2, tzinfo=UTC)))
        repo.put_apex(_apex("ours.example"))

        domains = _by_domain(client.get(URL).json())

        assert any(r[0] == "TXT" for r in _records(domains["portal.ours.example"]))
        assert not any(r[0] == "TXT" for r in _records(domains["book.ours.example"]))

    def test_a_requested_certificate_shows_its_authorisation_record(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        repo.put(_row("portal.ours.example", cert_auth_value="0f1e2d3c-aaaa.7"))

        (domain,) = client.get(URL).json()["domains"]

        assert (
            "CNAME",
            "_acme-challenge.portal.ours.example",
            "0f1e2d3c-aaaa.7.authorize.certificatemanager.goog",
        ) in _records(domain)


class TestCheck:
    def test_reports_each_record_and_records_ownership(
        self,
        client: TestClient,
        repo: InMemoryPracticeDomainRepository,
        audit: _RecordingAudit,
        zone: dict[tuple[str, str], list[str] | None],
    ) -> None:
        repo.put(_row("portal.ours.example", status="pending", cert_auth_value="abc.1"))
        repo.put_apex(_apex("ours.example"))
        zone[("portal.ours.example", "CNAME")] = [CNAME_TARGET]
        zone[("_pablo-verify.ours.example", "TXT")] = ["pablo-verify=tok-ours.example"]
        zone[("_acme-challenge.portal.ours.example", "CNAME")] = ["somewhere.else.example"]

        response = client.post(f"{URL}/check")

        assert response.status_code == 200
        (domain,) = response.json()["domains"]
        checks = {(r["type"], r["name"]): (r["check"], r["found"]) for r in domain["dns_records"]}
        assert checks == {
            ("CNAME", "portal.ours.example"): ("ok", [CNAME_TARGET]),
            ("CNAME", "_acme-challenge.portal.ours.example"): (
                "wrong",
                ["somewhere.else.example"],
            ),
            ("TXT", "_pablo-verify.ours.example"): ("ok", ["pablo-verify=tok-ours.example"]),
        }
        assert domain["apex_verified_at"] is not None
        assert repo.get_apex("ours.example").verified_at is not None  # type: ignore[union-attr]  # put above
        # Nothing about the host itself changed.
        assert domain["status"] == "pending"
        assert repo.get("portal.ours.example").status == "pending"  # type: ignore[union-attr]  # put above
        assert audit.entries == [
            {
                "action": AuditAction.PRACTICE_DOMAIN_OWNERSHIP_CONFIRMED,
                "resource_type": ResourceType.PRACTICE,
                "resource_id": PRACTICE_ID,
                "changes": {"domains": ["ours.example"]},
            }
        ]

    def test_missing_records_record_nothing(
        self,
        client: TestClient,
        repo: InMemoryPracticeDomainRepository,
        audit: _RecordingAudit,
    ) -> None:
        repo.put(_row("portal.ours.example", status="pending"))
        repo.put_apex(_apex("ours.example"))

        (domain,) = client.post(f"{URL}/check").json()["domains"]

        assert {r["check"] for r in domain["dns_records"]} == {"missing"}
        assert domain["apex_verified_at"] is None
        assert repo.get_apex("ours.example").verified_at is None  # type: ignore[union-attr]  # put above
        assert audit.entries == []

    def test_a_host_from_before_domain_rows_gets_one(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        repo.put(_row("portal.ours.example"))

        (domain,) = client.post(f"{URL}/check").json()["domains"]

        apex = repo.get_apex("ours.example")
        assert apex is not None
        assert ("TXT", "_pablo-verify.ours.example", f"pablo-verify={apex.verify_token}") in (
            _records(domain)
        )

    def test_a_domain_another_practice_holds_is_left_to_them(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        repo.put(_row("portal.shared.example"))
        repo.put_apex(_apex("shared.example", practice_id=OTHER_PRACTICE))

        (domain,) = client.post(f"{URL}/check").json()["domains"]

        assert repo.get_apex("shared.example").practice_id == OTHER_PRACTICE  # type: ignore[union-attr]  # put above
        assert not any(r[0] == "TXT" for r in _records(domain))

    @pytest.mark.parametrize("owner_email", ["someone-else@example.com"])
    def test_a_non_owner_cannot_check(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        repo.put(_row("portal.ours.example"))

        response = client.post(f"{URL}/check")

        assert response.status_code == 403
        assert repo.get_apex("ours.example") is None
