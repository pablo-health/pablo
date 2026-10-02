# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which practice's website a host serves, and which version of it.

Only a host whose purpose is ``site`` and whose status is ``active`` serves a
website, and only once its practice has published one. A host nobody holds, one
not yet working, a portal host and a practice with nothing published are all
the same "nothing", so the question says nothing about which practices hold
which hosts in which state. The rule mirrors the portal's
(:mod:`app.portal.practice_hosts`): the practice's working primary website host
is where its other website hosts send visitors.

Where the deployment names a hosted domain, the practice's hosted website
address (:mod:`app.portal.hosted`) serves its website the same way, and also
names the practice's portal address: ``/portal`` there leads to the portal,
which is never served on a website's origin.

Answers are kept for a minute either way, in the same bounded cache the portal
uses; a publish or roll back reaches visitors within that minute.

No PHI: hostnames, a practice id and a version number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..db import create_standalone_session
from ..db.platform_models import PracticeDomainRow
from ..portal.hosted import hosted_portal_host, hosted_practice_id, practice_slug
from ..portal.practice_hosts import HostAnswerCache, active_primary_portal_host
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
    #: On a hosted website address only: the host the practice's portal is
    #: served on, where ``/portal`` there is sent.
    portal_host: str | None = None


def resolve_site_host(session: Session, host: str) -> SiteHost | None:
    """The website *host* serves, or ``None`` when it serves none.

    *host* is already normalized (:func:`app.portal.practice_hosts.normalize_request_host`).
    """
    row = session.get(PracticeDomainRow, host)
    portal_host: str | None = None
    if row is not None:
        if row.purpose != "site" or row.status != "active":
            return None
        practice_id: str | None = row.practice_id
    else:
        practice_id = hosted_practice_id(session, host, "site")
    if practice_id is None:
        return None
    store = PracticeSiteStore(session)
    site = store.get(practice_id)
    if site is None or site.live_version is None:
        return None
    if row is None:
        slug = practice_slug(session, practice_id)
        portal_host = active_primary_portal_host(session, practice_id) or (
            hosted_portal_host(slug) if slug else None
        )
    primary = next((h.domain for h in store.active_site_hosts(practice_id) if h.is_primary), None)
    return SiteHost(
        practice_id=practice_id,
        live_version=site.live_version,
        primary_host=primary,
        portal_host=portal_host,
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
