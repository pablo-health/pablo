# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""One-click DNS setup on Settings > Domains: what is offered, to whom, and
what coming back from the DNS provider does.

Mounts the real routers on a fresh app the way ``test_practice_domains_routes``
does: the real owner check with the practice row patched in, an in-memory
repository, and the DNS provider answered from the captured Squarespace
settings plus a stub template-support answer. Links are signed with a local
key and checked with the provider's verification procedure.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import require_active_subscription
from app.models.audit import AuditAction, ResourceType
from app.models.practice_domain import PracticeDomain, PracticeDomainApex
from app.repositories.practice_domain import InMemoryPracticeDomainRepository
from app.routes import domain_connect, practice_domains
from app.services import domain_connect_discovery
from app.services.audit_service import get_audit_service
from app.services.domain_connect import (
    DomainConnectConfig,
    DomainConnectService,
    get_domain_connect_service,
)
from app.services.domain_connect_discovery import DomainConnectDiscovery, Fetched
from app.services.domain_connect_signing import RsaKeySigner, verify_signed_query
from app.services.domain_connect_state import ConnectState, mint_state
from app.services.practice_domain_dns import get_dns_lookup
from app.services.practice_domain_service import (
    PracticeDomainService,
    get_practice_domain_service,
)
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models import User

FIXTURES = Path(__file__).parent / "fixtures" / "domain_connect"
PRACTICE_ID = "practice-1"
URL = "/api/practice/domains/connect"
APEX = "example.com"
CNAME_TARGET = "sites.provider.example"
SITE_IP = "203.0.113.10"
STATE_KEY = b"dummy-state-key"
#: The domain's ownership token, as the routes hand it to the template.
VERIFY = "test-token"
REDIRECT = "https://app.example.org/dashboard/settings/domains"
SYNC_UX = "https://domains.squarespace.com"
TEMPLATE_URL = f"{SYNC_UX}/v2/domainTemplates/providers/provider.example/services"
CONFIG = DomainConnectConfig(
    provider_id="provider.example",
    portal_service_id="practice-domain",
    website_service_id="practice-website",
    key_host="dev1",
    portal_target="sites",
    cname_target=CNAME_TARGET,
    apex_ips=(SITE_IP,),
    redirect_uri=REDIRECT,
)


class _RecordingAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def log(self, action: Any, *_args: Any, **kwargs: Any) -> None:
        self.entries.append({"action": action, **kwargs})


class _Web:
    """The captured Squarespace settings for example.com, and a template
    answer per service id (200 unless told otherwise)."""

    def __init__(self) -> None:
        settings = json.loads((FIXTURES / "squarespace_settings.json").read_text())
        self.pages: dict[str, Fetched] = {
            settings["request"].removeprefix("GET "): Fetched(200, settings["body"].encode()),
            f"{TEMPLATE_URL}/practice-domain": Fetched(200, b"{}"),
            f"{TEMPLATE_URL}/practice-website": Fetched(200, b"{}"),
        }
        self.requests: list[str] = []

    def __call__(self, url: str) -> Fetched | None:
        self.requests.append(url)
        return self.pages.get(url)


@pytest.fixture(scope="module")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def repo() -> InMemoryPracticeDomainRepository:
    return InMemoryPracticeDomainRepository()


@pytest.fixture
def audit() -> _RecordingAudit:
    return _RecordingAudit()


@pytest.fixture
def web() -> _Web:
    return _Web()


@pytest.fixture
def zone() -> dict[tuple[str, str], list[str]]:
    return {("_domainconnect.example.com", "TXT"): ["domains.squarespace.com"]}


@pytest.fixture
def config() -> DomainConnectConfig:
    return CONFIG


@pytest.fixture
def owner_email() -> str:
    return "test@example.com"


@pytest.fixture
def client(
    mock_user: User,
    repo: InMemoryPracticeDomainRepository,
    audit: _RecordingAudit,
    web: _Web,
    zone: dict[tuple[str, str], list[str]],
    config: DomainConnectConfig,
    owner_email: str,
    private_key: rsa.RSAPrivateKey,
) -> Iterator[TestClient]:
    domain_connect_discovery.clear_cache()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(practice_domains.router)
    app.include_router(domain_connect.router)

    def lookup(name: str, rdtype: str) -> list[str]:
        return zone.get((name, rdtype), [])

    connect = DomainConnectService(
        repo,
        DomainConnectDiscovery(lookup, web),
        RsaKeySigner(private_key),
        STATE_KEY,
        config,
    )
    domains = PracticeDomainService(repo, cname_target=CNAME_TARGET, apex_ips=(SITE_IP,))
    app.dependency_overrides[require_active_subscription] = lambda: mock_user
    app.dependency_overrides[get_domain_connect_service] = lambda: connect
    app.dependency_overrides[get_practice_domain_service] = lambda: domains
    app.dependency_overrides[get_audit_service] = lambda: audit
    app.dependency_overrides[get_dns_lookup] = lambda: lookup

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
    domain_connect_discovery.clear_cache()


def _host(domain: str, purpose: str, cert: str | None = "test-cert") -> PracticeDomain:
    return PracticeDomain(
        domain=domain,
        practice_id=PRACTICE_ID,
        purpose=purpose,  # type: ignore[arg-type]  # Literal from a test parameter
        kind="vanity",
        status="pending",
        is_primary=False,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        cert_auth_value=cert,
    )


def _apex(dkim: list[str] | None = None) -> PracticeDomainApex:
    return PracticeDomainApex(
        apex=APEX,
        practice_id=PRACTICE_ID,
        verify_token=VERIFY,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        email_dkim_tokens=["test-dkim-one", "test-dkim-two", "test-dkim-three"]
        if dkim is None
        else dkim,
    )


def _portal_ready(repo: InMemoryPracticeDomainRepository) -> None:
    repo.put(_host(f"portal.{APEX}", "portal", cert="test-cert-portal"))
    repo.put_apex(_apex())


def _website_ready(repo: InMemoryPracticeDomainRepository) -> None:
    repo.put(_host(APEX, "site", cert="test-cert-bare"))
    repo.put(_host(f"www.{APEX}", "site", cert="test-cert-www"))


def _offers(client: TestClient) -> dict[str, dict[str, Any]]:
    response = client.get(URL)
    assert response.status_code == 200
    return {
        offer["service_id"]: offer
        for apex in response.json()["domains"]
        for offer in apex["offers"]
    }


def _query(url: str) -> dict[str, str]:
    return {name: values[0] for name, values in parse_qs(urlsplit(url).query).items()}


class TestOffers:
    def test_portal_link_carries_the_records_and_verifies(
        self,
        client: TestClient,
        repo: InMemoryPracticeDomainRepository,
        private_key: rsa.RSAPrivateKey,
    ) -> None:
        _portal_ready(repo)
        offer = _offers(client)["practice-domain"]

        assert offer["supported"] is True
        assert offer["provider_name"] == "Squarespace"
        assert offer["purpose"] == "portal"
        assert offer["reason"] is None
        url = offer["url"]
        assert url.startswith(f"{TEMPLATE_URL}/practice-domain/apply?")
        query = _query(url)
        assert {k: v for k, v in query.items() if k not in {"state", "sig"}} == {
            "domain": APEX,
            "verify": VERIFY,
            "dkim1": "test-dkim-one",
            "dkim2": "test-dkim-two",
            "dkim3": "test-dkim-three",
            "portaltarget": "sites",
            "certauth": "test-cert-portal",
            "redirect_uri": REDIRECT,
            "key": "dev1",
        }
        verify_signed_query(urlsplit(url).query, private_key.public_key())

    def test_website_link(
        self,
        client: TestClient,
        repo: InMemoryPracticeDomainRepository,
        private_key: rsa.RSAPrivateKey,
    ) -> None:
        _website_ready(repo)
        offers = _offers(client)
        assert set(offers) == {"practice-website"}
        url = offers["practice-website"]["url"]
        query = _query(url)
        assert query["siteip"] == SITE_IP
        assert query["certauth"] == "test-cert-bare"
        assert query["certauthwww"] == "test-cert-www"
        verify_signed_query(urlsplit(url).query, private_key.public_key())

    def test_issuing_links_is_audited_with_the_domain_only(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository, audit: _RecordingAudit
    ) -> None:
        _portal_ready(repo)
        _website_ready(repo)
        _offers(client)
        assert audit.entries == [
            {
                "action": AuditAction.PRACTICE_DOMAIN_CONNECT_LINK_ISSUED,
                "resource_type": ResourceType.PRACTICE,
                "resource_id": PRACTICE_ID,
                "changes": {"domains": [APEX]},
            }
        ]

    def test_a_portal_under_another_name_is_not_offered(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository, web: _Web
    ) -> None:
        repo.put(_host(f"clients.{APEX}", "portal"))
        repo.put_apex(_apex())
        offer = _offers(client)["practice-domain"]
        assert offer["url"] is None
        assert offer["reason"] == "hosts_differ"
        assert web.requests == []

    def test_hosts_being_removed_are_never_offered(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository, web: _Web
    ) -> None:
        _portal_ready(repo)
        _website_ready(repo)
        for host in (f"portal.{APEX}", APEX, f"www.{APEX}"):
            removing = repo.get(host)
            assert removing is not None
            removing.status = "removing"
            repo.put(removing)

        assert _offers(client) == {}
        assert web.requests == []

    def test_a_website_being_removed_leaves_the_portal_offered(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        _portal_ready(repo)
        _website_ready(repo)
        www = repo.get(f"www.{APEX}")
        assert www is not None
        www.status = "removing"
        repo.put(www)

        offers = _offers(client)
        assert offers["practice-website"]["reason"] == "hosts_differ"
        assert offers["practice-domain"]["url"] is not None

    def test_a_website_without_its_www_alias_is_not_offered(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        repo.put(_host(APEX, "site"))
        assert _offers(client)["practice-website"]["reason"] == "hosts_differ"

    @pytest.mark.parametrize("missing", ["cert", "dkim"])
    def test_not_offered_until_every_value_is_known(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository, missing: str
    ) -> None:
        repo.put(_host(f"portal.{APEX}", "portal", cert=None if missing == "cert" else "x"))
        repo.put_apex(_apex(dkim=[] if missing == "dkim" else None))
        offer = _offers(client)["practice-domain"]
        assert (offer["url"], offer["reason"]) == (None, "records_pending")

    def test_a_provider_without_the_template(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository, web: _Web
    ) -> None:
        web.pages[f"{TEMPLATE_URL}/practice-domain"] = Fetched(404, b"{}")
        _portal_ready(repo)
        offer = _offers(client)["practice-domain"]
        assert offer["supported"] is False
        assert (offer["url"], offer["reason"]) == (None, "template_unsupported")
        assert offer["provider_name"] == "Squarespace"

    def test_a_domain_without_domain_connect(
        self,
        client: TestClient,
        repo: InMemoryPracticeDomainRepository,
        zone: dict[tuple[str, str], list[str]],
    ) -> None:
        zone.clear()
        _portal_ready(repo)
        offer = _offers(client)["practice-domain"]
        assert (offer["url"], offer["reason"]) == (None, "provider_unsupported")

    @pytest.mark.parametrize("owner_email", ["someone-else@example.com"])
    def test_a_clinician_who_is_not_the_owner_gets_no_link(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository, web: _Web
    ) -> None:
        _portal_ready(repo)
        offer = _offers(client)["practice-domain"]
        assert (offer["url"], offer["reason"]) == (None, "not_allowed")
        assert web.requests == []

    @pytest.mark.parametrize(
        "config",
        [
            DomainConnectConfig(),
            DomainConnectConfig(**{**CONFIG.__dict__, "provider_id": ""}),
        ],
    )
    def test_off_unless_configured(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        _portal_ready(repo)
        assert client.get(URL).json() == {"domains": []}

    @pytest.mark.parametrize(
        "config",
        [
            DomainConnectConfig(**{**CONFIG.__dict__, "portal_target": "other"}),
            DomainConnectConfig(**{**CONFIG.__dict__, "apex_ips": (SITE_IP, "203.0.113.11")}),
            DomainConnectConfig(**{**CONFIG.__dict__, "apex_ips": ("2001:db8::1",)}),
        ],
    )
    def test_a_template_that_would_not_match_the_listed_records_is_not_offered(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        _portal_ready(repo)
        _website_ready(repo)
        offered = set(_offers(client))
        assert len(offered) == 1


class TestReturn:
    def _state(self, practice_id: str = PRACTICE_ID) -> str:
        return mint_state(STATE_KEY, ConnectState(practice_id, APEX, "practice-domain"))

    def test_runs_the_dns_check_and_answers_with_what_it_found(
        self,
        client: TestClient,
        repo: InMemoryPracticeDomainRepository,
        zone: dict[tuple[str, str], list[str]],
        audit: _RecordingAudit,
    ) -> None:
        _portal_ready(repo)
        zone[(f"portal.{APEX}", "CNAME")] = [CNAME_TARGET]
        zone[(f"_pablo-verify.{APEX}", "TXT")] = [f"pablo-verify={VERIFY}"]

        response = client.post(f"{URL}/return", json={"state": self._state()})

        assert response.status_code == 200
        body = response.json()
        assert body["apex"] == APEX
        assert body["error"] is None
        records = {(r["type"], r["name"]): r["check"] for r in body["domains"][0]["dns_records"]}
        assert records[("CNAME", f"portal.{APEX}")] == "ok"
        assert records[("TXT", f"_pablo-verify.{APEX}")] == "ok"
        assert records[("CNAME", f"test-dkim-one._domainkey.{APEX}")] == "missing"
        # Coming back is not the host working.
        assert body["domains"][0]["status"] == "pending"
        assert [e["action"] for e in audit.entries] == [
            AuditAction.PRACTICE_DOMAIN_CONNECT_RETURNED,
            AuditAction.PRACTICE_DOMAIN_OWNERSHIP_CONFIRMED,
        ]
        assert audit.entries[0]["changes"] == {"domain": APEX}

    def test_passes_on_the_providers_error(
        self, client: TestClient, repo: InMemoryPracticeDomainRepository
    ) -> None:
        _portal_ready(repo)
        response = client.post(
            f"{URL}/return", json={"state": self._state(), "error": "access_denied"}
        )
        assert response.status_code == 200
        assert response.json()["error"] == "access_denied"

    @pytest.mark.parametrize("state", ["not-a-state", "x.y"])
    def test_refuses_a_state_it_did_not_issue(
        self, client: TestClient, audit: _RecordingAudit, state: str
    ) -> None:
        response = client.post(f"{URL}/return", json={"state": state})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "DOMAIN_CONNECT_STATE"
        assert audit.entries == []

    def test_refuses_another_practices_state(self, client: TestClient) -> None:
        response = client.post(f"{URL}/return", json={"state": self._state("practice-2")})
        assert response.status_code == 422

    @pytest.mark.parametrize("owner_email", ["someone-else@example.com"])
    def test_only_the_owner_can_return(self, client: TestClient) -> None:
        response = client.post(f"{URL}/return", json={"state": self._state()})
        assert response.status_code == 403
