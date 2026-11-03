# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which practice's portal a host serves.

* ``GET /api/portal/hosts/{host}`` — UNAUTHENTICATED. The web app asks this
  before serving a request whose host it does not otherwise know: given the
  host, which practice's portal goes there, and which host is that practice's
  primary, what theme does the portal wear there, and which website does it
  link back to? Returns ``{slug, primary_host, theme, site_host}`` and nothing
  else. Every host that
  serves no portal — unknown, not yet working, a website host, a practice that
  has turned the portal off — is the same 404, so the route says nothing about
  which practices hold which hosts in which state.

No PHI: a hostname in, a slug, hostnames and a theme out, all of them public
(the theme and the website host are the practice's own published website's).
The answer is kept for a minute either way
(:class:`~app.portal.practice_hosts.PortalHostCache`), so a request costs at
most a few primary-key reads a minute per host, and the
number of hosts kept is bounded. There is no per-address limit: the caller is
the web app's server, one address for every visitor, and a limit there would
let anyone sending made-up hostnames switch every practice's host off.

* ``GET /api/portal/hosts/{host}/booking-links/{slug}`` — UNAUTHENTICATED. Asked
  before the web app serves ``/book/{slug}`` on a practice's own host: ``{slug}`` when
  the host serves that practice's portal and the link is the practice's and on
  (:mod:`app.portal.booking_hosts`), and the same 404 otherwise. Kept and
  limited the same way, per host and link.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel

from ..auth.route_security import truly_public
from ..models.booking_link import SLUG_PATTERN
from ..sites.theme import PracticeTheme  # noqa: TC001 — pydantic resolves it at runtime
from .booking_hosts import (
    BookingHostCache,
    booking_host_key,
    get_booking_host_cache,
    load_books_on_host,
)
from .practice_hosts import (
    PortalHostCache,
    get_portal_host_cache,
    load_portal_host,
    normalize_request_host,
)

router = APIRouter(tags=["patient-portal"])

_BOOKING_SLUG = re.compile(SLUG_PATTERN)


class PortalHostResponse(BaseModel):
    """What the web app learns about a host — nothing more."""

    #: The practice's portal address; the portal is served as ``/portal/{slug}``.
    slug: str
    #: The practice's primary portal host when it is working. A request on any
    #: other of its hosts is sent there.
    primary_host: str | None
    #: The colors, fonts and header the portal wears on the practice's hosts,
    #: from its live website (:mod:`app.portal.practice_site`); ``None`` for its
    #: own look.
    theme: PracticeTheme | None = None
    #: The host the practice's live website is served at, which the portal links
    #: back to; ``None`` with no live website.
    site_host: str | None = None


@router.get("/api/portal/hosts/{host}", response_model=PortalHostResponse)
def resolve_portal_host_route(
    host: Annotated[str, Path(max_length=512)],
    cache: Annotated[PortalHostCache, Depends(get_portal_host_cache)],
    _public: None = Depends(truly_public),
) -> PortalHostResponse:
    """The portal *host* serves, or 404.

    The host is read the way a ``Host`` header would be: any case, with or
    without a port or a trailing dot.
    """
    normalized = normalize_request_host(host)
    found = cache.get_or_load(normalized, load_portal_host) if normalized else None
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    return PortalHostResponse(
        slug=found.slug,
        primary_host=found.primary_host,
        theme=found.theme,
        site_host=found.site_host,
    )


class BookingLinkOnHostResponse(BaseModel):
    """The link the host serves the booking page of."""

    slug: str


@router.get(
    "/api/portal/hosts/{host}/booking-links/{slug}", response_model=BookingLinkOnHostResponse
)
def booking_link_on_host_route(
    host: Annotated[str, Path(max_length=512)],
    slug: Annotated[str, Path(max_length=64)],
    cache: Annotated[BookingHostCache, Depends(get_booking_host_cache)],
    _public: None = Depends(truly_public),
) -> BookingLinkOnHostResponse:
    """The link when *host* serves the booking page of the link at *slug*, else 404."""
    normalized = normalize_request_host(host)
    serves = (
        normalized is not None
        and _BOOKING_SLUG.match(slug) is not None
        and cache.get_or_load(booking_host_key(normalized, slug), load_books_on_host)
    )
    if not serves:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    return BookingLinkOnHostResponse(slug=slug)
