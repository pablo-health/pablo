# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's booking pages on the practice's own host.

The public booking page is ``/book/{link}``. Where a practice's own host serves
its portal (:mod:`app.portal.practice_hosts`), the same host serves the
practice's booking pages too, in the practice's look, so a client who came from
the practice's website books without leaving its address. Two questions follow:

* **Does this host serve this booking link?** :func:`books_on_host`. Only when
  the host serves a practice's portal and the link is that practice's and
  active. Everything else — another practice's link, a link that is off, a link
  nobody has, a host that serves nothing — is the same "no". The web app asks
  this through the public route in :mod:`app.portal.host_routes` before it
  serves ``/book/...`` on a practice's host.
* **Where should a booking email's links point?** :func:`booking_page_origin`.
  The practice's own host when it serves the booking page — the same host a
  portal link goes to — and the app's address otherwise.

No PHI: hostnames, a link's public slug, and whether it is on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from ..db import create_standalone_session
from ..db.platform_models import BookingLinkRow
from ..settings import get_settings
from .hosted import hosted_addresses_ready, hosted_portal_host, practice_slug
from .portal_settings import portal_enabled_in
from .practice_hosts import HostAnswerCache, active_primary_portal_host, practice_serving_portal

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def books_on_host(session: Session, host: str, link_slug: str) -> bool:
    """Whether *host* serves the booking page of the link at *link_slug*.

    *host* is already normalized
    (:func:`app.portal.practice_hosts.normalize_request_host`).
    """
    serving = practice_serving_portal(session, host)
    if serving is None:
        return False
    link = session.execute(
        select(BookingLinkRow.practice_id, BookingLinkRow.is_active).where(
            BookingLinkRow.slug == link_slug, BookingLinkRow.deleted_at.is_(None)
        )
    ).one_or_none()
    return link is not None and link.is_active and link.practice_id == serving[0]


def booking_host(session: Session, practice_id: str) -> str | None:
    """The host the practice's booking pages are linked at, or ``None`` for the app.

    The practice's active primary portal host; else its hosted portal address
    where the deployment serves one. Only while the practice's portal is on and
    has an address, since that is when the host serves anything at all.
    """
    slug = practice_slug(session, practice_id)
    if slug is None or not portal_enabled_in(session, practice_id):
        return None
    own = active_primary_portal_host(session, practice_id)
    if own is not None or not hosted_addresses_ready():
        return own
    return hosted_portal_host(slug)


def booking_page_origin(practice_id: str | None) -> str:
    """The origin a booking email's links use for a link of *practice_id*.

    Asked once per email on a session of its own, never cached, so a link is
    never sent on a host that has just stopped working.
    """
    if practice_id is not None:
        session = create_standalone_session()
        try:
            host = booking_host(session, practice_id)
        finally:
            session.close()
        if host is not None:
            return f"https://{host}"
    return get_settings().app_url


#: Answers to "does this host serve this link", keyed ``"{host} {link}"``:
#: ``True``, or ``None`` for no.
BookingHostCache = HostAnswerCache[bool]

_cache = BookingHostCache()


def get_booking_host_cache() -> BookingHostCache:
    """FastAPI dependency — the process-wide cache, which tests override."""
    return _cache


def booking_host_key(host: str, link_slug: str) -> str:
    return f"{host} {link_slug}"


def load_books_on_host(key: str) -> bool | None:
    """:func:`books_on_host` for a :func:`booking_host_key`, on a session of its own."""
    host, _, link_slug = key.partition(" ")
    session = create_standalone_session()
    try:
        return books_on_host(session, host, link_slug) or None
    finally:
        session.close()
