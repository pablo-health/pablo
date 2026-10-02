# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's own portal hosts: which portal a host serves, and where links go.

A practice can serve its portal from hosts of its own (Settings > Domains, see
:mod:`app.services.practice_domain_service`). Two questions follow, and both are
answered here from the same rule:

* **Which portal does this host serve?** :func:`resolve_portal_host`. Only a
  host whose purpose is ``portal`` and whose status is ``active`` serves
  anything; a pending, failing or website host is the same "nothing" as a host
  nobody has heard of. So does the practice's hosted portal address, where the
  deployment names a hosted domain (:mod:`app.portal.hosted`). The web app asks
  this through the public route in :mod:`app.portal.host_routes` before it
  serves a request for a host it does not otherwise know.
* **Where should a portal link point?** :func:`primary_portal_host_for_slug`.
  The practice's primary portal host, if that host is active; else its hosted
  portal address, if the deployment has one. The default address resolver in
  :mod:`app.portal.factory` prefers it; a deployment that registers its own
  resolver can call it to follow the same rule.

A host is only ever the portal of the practice that holds it — the hostname is
the primary key of ``platform.practice_domains``, and a hosted address carries
the practice's own unique slug — so nothing here can hand one practice's host
to another.

The portal host's answer also carries what the portal takes from the practice's
website there — its theme and the website's host (:mod:`app.portal.practice_site`)
— so the web app learns them in the lookup it already makes.

No PHI: public hostnames, a slug, whether a practice offers the portal, and the
colors and fonts its portal wears.
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING

from sqlalchemy import select

from ..db import create_standalone_session
from ..db.platform_models import PortalPracticeSlugRow, PracticeDomainRow
from .hosted import hosted_portal_host, hosted_practice_id, practice_slug
from .portal_settings import portal_enabled_in
from .practice_site import live_site_host, portal_theme

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

    from ..sites.theme import PracticeTheme

_MAX_HOST_LENGTH = 253
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

#: How long an answer is kept, found or not. A host that becomes active, or
#: stops being, is noticed within this long.
CACHE_TTL_SECONDS = 60.0
#: How many hosts are remembered at once. Past this the oldest answer goes, so
#: a caller inventing hostnames costs memory up to here and no further.
CACHE_MAX_ENTRIES = 10_000


@dataclass(frozen=True)
class PortalHost:
    """What a portal host serves."""

    #: The practice's portal address, the ``{slug}`` in ``/portal/{slug}``.
    slug: str
    #: The practice's primary portal host, when it has one that is active. A
    #: host that is not it sends visitors there.
    primary_host: str | None
    #: The colors and fonts the portal wears there (:mod:`app.portal.practice_site`);
    #: ``None`` for its own look.
    theme: PracticeTheme | None = None
    #: The host the practice's live website is served at, which the portal
    #: links back to; ``None`` with no live website.
    site_host: str | None = None


def normalize_request_host(raw: str) -> str | None:
    """*raw* as a stored hostname would read, or ``None`` when it cannot be one.

    For a ``Host`` header or something taken from one: lowercased, port and
    trailing dot removed. IP addresses and anything that is not a plain DNS
    name come back ``None``, since no practice can hold one.
    """
    host = raw.strip().lower()
    if not host or host.startswith("["):
        return None
    host, _, port = host.partition(":")
    if port and not port.isdigit():
        return None
    host = host.removesuffix(".")
    if not host or len(host) > _MAX_HOST_LENGTH or "." not in host:
        return None
    labels = host.split(".")
    if not all(_LABEL.match(label) for label in labels):
        return None
    if labels[-1].isdigit():
        # The last label of a DNS name is never all digits; this is an IPv4
        # address.
        return None
    return host


def active_primary_portal_host(session: Session, practice_id: str) -> str | None:
    """The practice's primary portal host, if it is active."""
    stmt = select(PracticeDomainRow.domain).where(
        PracticeDomainRow.practice_id == practice_id,
        PracticeDomainRow.purpose == "portal",
        PracticeDomainRow.is_primary.is_(True),
        PracticeDomainRow.status == "active",
    )
    return session.execute(stmt).scalar_one_or_none()


def resolve_portal_host(session: Session, host: str) -> PortalHost | None:
    """The portal *host* serves, or ``None`` when it serves none.

    *host* is already normalized (:func:`normalize_request_host`). ``None`` for
    a host nobody holds, one that is not active, a website host, a practice
    with no portal address yet, and a practice that does not offer the portal —
    the same answer for all of them, so the question cannot be used to learn
    which practices hold which hosts in which state.

    A practice's hosted portal address (:mod:`app.portal.hosted`) serves its
    portal like a working host of its own that is not its primary: when the
    practice has a working primary, the answer names it and visitors are sent
    there.
    """
    row = session.get(PracticeDomainRow, host)
    if row is not None:
        if row.purpose != "portal" or row.status != "active":
            return None
        practice_id: str | None = row.practice_id
    else:
        practice_id = hosted_practice_id(session, host, "portal")
    if practice_id is None:
        return None
    slug = practice_slug(session, practice_id)
    if slug is None or not portal_enabled_in(session, practice_id):
        return None
    return PortalHost(
        slug=slug,
        primary_host=active_primary_portal_host(session, practice_id),
        theme=portal_theme(session, practice_id),
        site_host=live_site_host(session, practice_id),
    )


def primary_portal_host_for_slug(slug: str) -> str | None:
    """The host portal links of the practice whose address is *slug* point at.

    Its active primary portal host; else its hosted portal address, where the
    deployment names a hosted domain; else ``None``. What the default portal
    address resolver uses to put links on the practice's own host
    (``https://{host}/``). A deployment that registers its own resolver
    (:func:`app.portal.factory.register_portal_address_resolver`) can call this
    first and fall back to its own address when it answers ``None``, so its
    links follow the same rule. Opens and closes its own session; asked once
    per link, never cached, so a link is never minted on a host that has just
    stopped working.
    """
    session = create_standalone_session()
    try:
        practice_id = session.execute(
            select(PortalPracticeSlugRow.practice_id).where(PortalPracticeSlugRow.slug == slug)
        ).scalar_one_or_none()
        if practice_id is None:
            return None
        return active_primary_portal_host(session, practice_id) or hosted_portal_host(slug)
    finally:
        session.close()


def portal_host_root_url(host: str) -> str:
    """The portal served at the root of a practice's own host."""
    return f"https://{host}/"


class HostAnswerCache[T]:
    """Answers to "what does this host serve", kept for a short while.

    Found and not-found answers are both kept for ``ttl_seconds``, and at most
    ``max_entries`` hosts are kept, the oldest answer going first. The clock is
    a parameter so tests can move it. The portal's answers are kept in one of
    these, and a practice website's (:mod:`app.sites.hosts`) in another.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = CACHE_TTL_SECONDS,
        max_entries: int = CACHE_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, T | None]] = OrderedDict()
        self._lock = Lock()

    def get_or_load(self, host: str, load: Callable[[str], T | None]) -> T | None:
        """The kept answer for *host*, or *load*'s, which is then kept."""
        now = self._clock()
        with self._lock:
            kept = self._entries.get(host)
            if kept is not None and kept[0] > now:
                return kept[1]
        answer = load(host)
        with self._lock:
            self._entries.pop(host, None)
            self._entries[host] = (now + self._ttl, answer)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
        return answer

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


#: The portal's cache; callable, so tests build their own with a clock.
PortalHostCache = HostAnswerCache[PortalHost]

_cache = PortalHostCache()


def get_portal_host_cache() -> PortalHostCache:
    """FastAPI dependency — the process-wide cache, which tests override."""
    return _cache


def load_portal_host(host: str) -> PortalHost | None:
    """:func:`resolve_portal_host` on a session of its own."""
    session = create_standalone_session()
    try:
        return resolve_portal_host(session, host)
    finally:
        session.close()
