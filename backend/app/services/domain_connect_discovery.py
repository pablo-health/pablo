# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Finding a domain's DNS provider through Domain Connect, and asking whether
it offers a template.

Discovery (draft-ietf-dconn-domainconnect, "Discovery"):

1. ``_domainconnect.<domain>`` TXT names the provider's settings host, with
   an optional path (``api.example.net/client/v4/dns/domainconnect``). The
   TXT often sits behind a CNAME; the resolver follows it and the final TXT
   answer is the one used.
2. ``GET https://<that>/v2/<domain>/settings`` answers ``providerId``,
   ``providerName``, optionally ``providerDisplayName``, ``urlSyncUX`` (absent
   when the provider has no synchronous flow for this domain) and ``urlAPI``.
3. ``GET <urlAPI>/v2/domainTemplates/providers/<providerId>/services/<serviceId>``
   is 200 when the provider has the template and 404 when it does not.

Everything here is a public lookup with no PHI. Every request is https only,
short, size-capped, refuses redirects and refuses hosts that resolve to
private addresses — the settings host comes from a TXT record anyone can
publish. Answers are cached per domain for a few minutes so a page reload
does not reach the provider again.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

    from .practice_domain_dns import DnsLookup

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 3.0
MAX_BODY_BYTES = 64 * 1024
CACHE_SECONDS = 5 * 60

_DC_ID = re.compile(r"^[A-Za-z0-9._-]{1,63}$")
#: The ``_domainconnect`` TXT: a host name, optionally followed by a path.
_SETTINGS_HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?(/[A-Za-z0-9._~/-]*)?$")
_MAX_NAME_LENGTH = 255


@dataclass(frozen=True)
class Fetched:
    status: int
    body: bytes


class HttpFetch(Protocol):
    def __call__(self, url: str) -> Fetched | None:
        """GET *url*. ``None`` when no answer came back (refused, timed out,
        too large)."""
        ...


@dataclass(frozen=True)
class DnsProvider:
    """What a domain's DNS provider says about Domain Connect for it."""

    provider_id: str
    #: What to call the provider on screen: ``providerDisplayName`` when
    #: given, otherwise ``providerName``.
    display_name: str
    url_sync_ux: str
    url_api: str


def _is_public_address(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


class HttpxFetch:
    """The real fetch: https only, no redirects, short and size-capped, and
    only to hosts that resolve to public addresses."""

    def __call__(self, url: str) -> Fetched | None:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            return None
        try:
            infos = socket.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
        except OSError:
            return None
        if not infos or not all(_is_public_address(str(info[4][0])) for info in infos):
            logger.info("Domain Connect host %s refused: not a public address", parts.hostname)
            return None
        try:
            with (
                httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client,
                client.stream("GET", url, headers={"Accept": "application/json"}) as response,
            ):
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BODY_BYTES:
                        return None
                return Fetched(status=response.status_code, body=body)
        except httpx.HTTPError as e:
            logger.info("Domain Connect request to %s failed: %s", parts.hostname, type(e).__name__)
            return None


def _https_prefix(value: object) -> str | None:
    """A URL prefix the spec allows: https, a host, no query or fragment."""
    if not isinstance(value, str) or len(value) > _MAX_NAME_LENGTH * 4:
        return None
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
        return None
    return value.rstrip("/")


def _display_name(value: object) -> str | None:
    if isinstance(value, str) and 0 < len(value.strip()) <= _MAX_NAME_LENGTH:
        return value.strip()
    return None


def parse_settings(body: bytes) -> DnsProvider | None:
    """The provider, from a settings answer — or ``None`` when the answer is
    not the expected shape or offers no synchronous flow."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    provider_id = data.get("providerId")
    name = _display_name(data.get("providerDisplayName")) or _display_name(data.get("providerName"))
    url_sync_ux = _https_prefix(data.get("urlSyncUX"))
    url_api = _https_prefix(data.get("urlAPI"))
    if not (isinstance(provider_id, str) and _DC_ID.match(provider_id)):
        return None
    if name is None or url_sync_ux is None or url_api is None:
        return None
    return DnsProvider(
        provider_id=provider_id, display_name=name, url_sync_ux=url_sync_ux, url_api=url_api
    )


class _TtlCache:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, ...], tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple[str, ...], now: float) -> tuple[bool, object]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry[0] <= now:
                return False, None
            return True, entry[1]

    def put(self, key: tuple[str, ...], value: object, now: float) -> None:
        with self._lock:
            self._entries = {k: v for k, v in self._entries.items() if v[0] > now}
            self._entries[key] = (now + CACHE_SECONDS, value)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


#: Shared across requests: a discovery is per domain, not per caller.
_cache = _TtlCache()


def clear_cache() -> None:
    _cache.clear()


class DomainConnectDiscovery:
    def __init__(
        self,
        lookup: DnsLookup,
        fetch: HttpFetch,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lookup = lookup
        self._fetch = fetch
        self._clock = clock

    def provider_for(self, apex: str) -> DnsProvider | None:
        """The DNS provider for *apex*, if it offers the synchronous flow."""
        key = ("provider", apex)
        hit, cached = _cache.get(key, self._clock())
        if hit:
            return cached if isinstance(cached, DnsProvider) else None
        provider = self._discover(apex)
        _cache.put(key, provider, self._clock())
        return provider

    def _discover(self, apex: str) -> DnsProvider | None:
        answers = self._lookup(f"_domainconnect.{apex}", "TXT") or []
        host = next((a.strip().lower() for a in answers if _SETTINGS_HOST.match(a.strip())), None)
        if host is None:
            return None
        fetched = self._fetch(f"https://{host}/v2/{apex}/settings")
        if fetched is None or fetched.status != 200:  # noqa: PLR2004
            return None
        return parse_settings(fetched.body)

    def supports(self, provider: DnsProvider, provider_id: str, service_id: str) -> bool | None:
        """Whether *provider* has the template: ``None`` when it could not say."""
        key = ("template", provider.url_api, provider_id, service_id)
        hit, cached = _cache.get(key, self._clock())
        if hit:
            return cached if isinstance(cached, bool) else None
        fetched = self._fetch(
            f"{provider.url_api}/v2/domainTemplates/providers/{provider_id}/services/{service_id}"
        )
        supported: bool | None = None
        if fetched is not None and fetched.status in {200, 404}:
            supported = fetched.status == 200  # noqa: PLR2004
        _cache.put(key, supported, self._clock())
        return supported
