# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which practice's website a host serves, and which version of it.

Only a host whose purpose is ``site`` and whose status is ``active`` serves a
website, and only once its practice has published one. A host nobody holds, one
not yet working, a portal host and a practice with nothing published are all
the same "nothing", so the question says nothing about which practices hold
which hosts in which state. The rule mirrors the portal's
(:mod:`app.portal.practice_hosts`): the practice's working primary website host
is where its other website hosts send visitors.

Answers are kept for a minute either way, in the same bounded cache the portal
uses; a publish or roll back reaches visitors within that minute.

No PHI: hostnames, a practice id and a version number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..db import create_standalone_session
from ..db.platform_models import PracticeDomainRow
from ..portal.practice_hosts import HostAnswerCache
from .store import PracticeSiteStore

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class SiteHost:
    """What a website host serves."""

    practice_id: str
    #: The published version visitors are served.
    live_version: int
    #: The practice's working primary website host, when it has one.
    primary_host: str | None


def resolve_site_host(session: Session, host: str) -> SiteHost | None:
    """The website *host* serves, or ``None`` when it serves none.

    *host* is already normalized (:func:`app.portal.practice_hosts.normalize_request_host`).
    """
    row = session.get(PracticeDomainRow, host)
    if row is None or row.purpose != "site" or row.status != "active":
        return None
    store = PracticeSiteStore(session)
    site = store.get(row.practice_id)
    if site is None or site.live_version is None:
        return None
    primary = next(
        (h.domain for h in store.active_site_hosts(row.practice_id) if h.is_primary), None
    )
    return SiteHost(
        practice_id=row.practice_id, live_version=site.live_version, primary_host=primary
    )


def load_site_host(host: str) -> SiteHost | None:
    """:func:`resolve_site_host` on a session of its own."""
    session = create_standalone_session()
    try:
        return resolve_site_host(session, host)
    finally:
        session.close()


SiteHostCache = HostAnswerCache[SiteHost]

_cache = SiteHostCache()


def get_site_host_cache() -> SiteHostCache:
    """FastAPI dependency — the process-wide cache, which tests override."""
    return _cache
