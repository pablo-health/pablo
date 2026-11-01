# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which peers are allowed to speak for the client in forwarded headers.

``X-Forwarded-For`` and ``X-Forwarded-Proto`` are only meaningful when the
immediate peer is a reverse proxy this deployment controls. From anyone else
they are free-form input: a caller reaching the app port directly can send a
fresh ``X-Forwarded-For`` per request and would otherwise get a fresh
rate-limiter key each time, or name any address it likes on an audit row.
``Settings.trusted_proxy_ips`` names the peers that are allowed to set them;
the HTTPS middleware, the rate limiter and the audit trail all consult it
through here so the answer cannot drift between them.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request

    from .settings import Settings


@dataclass(frozen=True)
class TrustedProxies:
    """The parsed form of ``TRUSTED_PROXY_IPS``.

    - ``""`` (default): trust no peer.
    - ``"*"``: trust every peer (Cloud Run, where only the platform front end
      can reach the container).
    - ``"10.0.0.1,10.0.0.2"``: trust exactly these peers.
    """

    trust_all: bool
    addresses: frozenset[str]

    @classmethod
    def from_settings(cls, settings: Settings) -> TrustedProxies:
        return _parse(settings.trusted_proxy_ips)

    def trusts(self, request: Request) -> bool:
        if self.trust_all:
            return True
        if not self.addresses or not request.client:
            return False
        return request.client.host in self.addresses


@lru_cache
def _parse(raw: str) -> TrustedProxies:
    if raw == "*":
        return TrustedProxies(trust_all=True, addresses=frozenset())
    return TrustedProxies(
        trust_all=False,
        addresses=frozenset(ip.strip() for ip in raw.split(",") if ip.strip()),
    )


def is_from_trusted_proxy(request: Request, settings: Settings) -> bool:
    return TrustedProxies.from_settings(settings).trusts(request)


def client_ip(request: Request, settings: Settings) -> str | None:
    """The real client's address, or None when the request has no peer.

    X-Forwarded-For is read only when the immediate peer is a trusted proxy;
    from anyone else it is ignored and the peer's own address is the answer.
    Even then the client controls the *leftmost* entries, while each trusted
    proxy *appends* the address it saw, so the answer is
    ``settings.trusted_proxy_hops`` entries from the right.
    """
    peer = request.client.host if request.client else None
    if not is_from_trusted_proxy(request, settings):
        return peer
    forwarded = request.headers.get("x-forwarded-for")
    parts = [p.strip() for p in forwarded.split(",") if p.strip()] if forwarded else []
    if not parts:
        return peer
    return parts[-min(settings.trusted_proxy_hops, len(parts))]
