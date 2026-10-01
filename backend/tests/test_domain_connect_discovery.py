# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Finding a domain's DNS provider through Domain Connect.

The DNS answers and HTTP responses are captures from real providers
(``fixtures/domain_connect/``, see its README), replayed through a stub
lookup and a stub fetch keyed by URL. The hand-written cases below them are
the malformed shapes a provider has not been seen to send.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from app.services import domain_connect_discovery as discovery_module
from app.services.domain_connect_discovery import (
    DnsProvider,
    DomainConnectDiscovery,
    Fetched,
    HttpxFetch,
    parse_settings,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

FIXTURES = Path(__file__).parent / "fixtures" / "domain_connect"


def _capture(name: str) -> tuple[str, Fetched]:
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    url = data["request"].removeprefix("GET ")
    return url, Fetched(status=data["status"], body=data["body"].encode())


def _dns() -> dict[str, list[str]]:
    return dict(json.loads((FIXTURES / "dns_txt.json").read_text())["answers"])


class _Web:
    """Answers captured URLs; anything else gets no answer."""

    def __init__(self, *captures: str) -> None:
        self.pages = dict(_capture(name) for name in captures)
        self.requests: list[str] = []

    def __call__(self, url: str) -> Fetched | None:
        self.requests.append(url)
        return self.pages.get(url)


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    discovery_module.clear_cache()
    yield
    discovery_module.clear_cache()


def _discovery(web: _Web, txt: dict[str, list[str]] | None = None) -> DomainConnectDiscovery:
    zone = _dns() if txt is None else txt
    return DomainConnectDiscovery(_txt(zone), web)


def _txt(zone: dict[str, list[str]]) -> Any:
    """A lookup that answers TXT questions from *zone* and nothing else."""

    def lookup(name: str, rdtype: str) -> list[str]:
        return zone.get(name, []) if rdtype == "TXT" else []

    return lookup


CAPTURED = (
    "squarespace_settings",
    "cloudflare_settings",
    "ionos_settings_not_found",
    "squarespace_template_supported",
    "squarespace_template_unsupported",
    "cloudflare_template_unsupported",
)


class TestProviderFor:
    def test_squarespace(self) -> None:
        provider = _discovery(_Web(*CAPTURED)).provider_for("example.com")
        assert provider == DnsProvider(
            provider_id="squarespace.com",
            display_name="Squarespace",
            url_sync_ux="https://domains.squarespace.com",
            url_api="https://domains.squarespace.com",
        )

    def test_a_settings_host_with_a_path(self) -> None:
        provider = _discovery(_Web(*CAPTURED)).provider_for("example.net")
        assert provider is not None
        assert provider.display_name == "Cloudflare"
        assert provider.url_sync_ux == "https://dash.cloudflare.com/domainconnect"
        assert provider.url_api == "https://api.cloudflare.com/client/v4/dns/domainconnect"

    def test_a_provider_that_does_not_serve_the_domain(self) -> None:
        web = _Web(*CAPTURED)
        assert _discovery(web).provider_for("example.org") is None
        assert web.requests == ["https://api.domainconnect.ionos.com/v2/example.org/settings"]

    def test_no_discovery_record_asks_no_one(self) -> None:
        web = _Web(*CAPTURED)
        assert _discovery(web).provider_for("example.edu") is None
        assert web.requests == []

    def test_a_discovery_record_that_is_not_a_host_is_ignored(self) -> None:
        web = _Web(*CAPTURED)
        txt = {"_domainconnect.example.com": ["https://evil.example/x", "v=spf1 -all"]}
        assert _discovery(web, txt).provider_for("example.com") is None
        assert web.requests == []

    def test_the_answer_is_cached(self) -> None:
        web = _Web(*CAPTURED)
        found = _discovery(web)
        found.provider_for("example.com")
        found.provider_for("example.com")
        _discovery(web).provider_for("example.com")
        assert len(web.requests) == 1

    def test_the_cache_expires(self) -> None:
        web = _Web(*CAPTURED)
        now = [1000.0]
        found = DomainConnectDiscovery(_txt(_dns()), web, clock=lambda: now[0])
        found.provider_for("example.com")
        now[0] += discovery_module.CACHE_SECONDS + 1
        found.provider_for("example.com")
        assert len(web.requests) == 2


class TestSupports:
    def test_a_template_the_provider_has(self) -> None:
        web = _Web(*CAPTURED)
        found = _discovery(web)
        provider = found.provider_for("example.com")
        assert provider is not None
        assert found.supports(provider, "google.com", "gmail-setup") is True

    def test_a_template_the_provider_does_not_have(self) -> None:
        found = _discovery(_Web(*CAPTURED))
        for apex in ("example.com", "example.net"):
            provider = found.provider_for(apex)
            assert provider is not None
            assert found.supports(provider, "pablo.health", "practice-domain") is False

    def test_no_answer_is_not_a_no(self) -> None:
        found = _discovery(_Web(*CAPTURED))
        provider = found.provider_for("example.com")
        assert provider is not None
        assert found.supports(provider, "provider.example", "other") is None


class TestParseSettings:
    BASE: dict[str, Any] = {  # noqa: RUF012 — read-only template for the cases below
        "providerId": "dns.example",
        "providerName": "Example DNS",
        "urlSyncUX": "https://connect.dns.example",
        "urlAPI": "https://api.dns.example",
    }

    def _parse(self, **changes: Any) -> DnsProvider | None:
        data = {**self.BASE, **changes}
        return parse_settings(json.dumps({k: v for k, v in data.items() if v is not None}).encode())

    def test_falls_back_to_provider_name(self) -> None:
        provider = self._parse()
        assert provider is not None
        assert provider.display_name == "Example DNS"

    @pytest.mark.parametrize(
        "changes",
        [
            {"urlSyncUX": None},
            {"urlSyncUX": "http://connect.dns.example"},
            {"urlAPI": "https://api.dns.example/?x=1"},
            {"providerId": "bad id/"},
            {"providerName": ""},
            {"providerName": 7},
        ],
    )
    def test_refuses_shapes_it_cannot_use(self, changes: dict[str, Any]) -> None:
        assert self._parse(**changes) is None

    def test_refuses_what_is_not_json_or_not_an_object(self) -> None:
        assert parse_settings(b"<html>") is None
        assert parse_settings(b"[]") is None


class TestHttpxFetch:
    def test_refuses_plain_http(self) -> None:
        assert HttpxFetch()("http://example.com/v2/example.com/settings") is None

    def test_refuses_a_host_on_a_private_address(self) -> None:
        assert HttpxFetch()("https://127.0.0.1/v2/example.com/settings") is None
        assert HttpxFetch()("https://10.0.0.1/v2/example.com/settings") is None
