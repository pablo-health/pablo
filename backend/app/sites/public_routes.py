# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's website, as visitors and the practice's preview reach it. UNAUTHENTICATED.

* ``GET /api/sites/hosts/{host}`` — the web app asks this before it serves a
  request on a host it does not otherwise know and that is no practice's
  portal: is it a working website host of a practice that has published, and
  which of the practice's website hosts is primary? Answers
  ``{primary_host}``; every other host is the same 404.
* ``GET /api/sites/hosts/{host}/file?path=`` — the web app's server fetches
  the file a visitor asked for here and answers the visitor itself
  (``frontend/src/lib/portal-host/practice-site.ts``). ``path`` is the
  request path as the visitor sent it.
* ``GET /api/practice/website/preview/{token}/{path}`` — the practice's draft,
  for the hour after an owner asked for its address. The token is the only
  thing that grants it; a new draft, a publish, or a newer preview address
  ends it.

Website files are public by design and carry no PHI. These routes are
reachable on the app's own origin, so every answer is inert there: a bare
``sandbox`` policy, no script, no form (:mod:`app.sites.serving`).

They do not refuse a request by its Host. The web app's server reaches them at
its configured API address, which on a deployment serving app and API from one
host is the app's own host, so the Host cannot tell its fetch from a visitor's.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response, status
from pydantic import BaseModel

from ..auth.route_security import truly_public
from ..db import create_standalone_session
from ..portal.practice_hosts import normalize_request_host
from ..settings import get_settings
from ..utcnow import utc_now
from .hosts import SiteHost, SiteHostCache, get_site_host_cache, load_site_host
from .service import draft_prefix, preview_token_hash, version_prefix
from .serving import SiteFolder, plain_not_found, site_file_response
from .storage import SiteFileCache, get_site_file_cache, site_storage
from .store import PracticeSiteStore

router = APIRouter(tags=["practice-website"])

_MAX_PATH = 2048


class SiteHostResponse(BaseModel):
    """What the web app learns about a website host — nothing more."""

    #: The practice's primary website host when it is working. A request on
    #: any other of its website hosts is sent there.
    primary_host: str | None


def _bucket() -> str | None:
    return get_settings().practice_site_bucket or None


def _site_host(host: str, cache: SiteHostCache) -> SiteHost | None:
    """What a working, published website host serves; ``None`` for every other host."""
    normalized = normalize_request_host(host)
    if normalized is None or _bucket() is None:
        return None
    return cache.get_or_load(normalized, load_site_host)


@router.get("/api/sites/hosts/{host}", response_model=SiteHostResponse)
def resolve_site_host_route(
    host: Annotated[str, Path(max_length=512)],
    cache: Annotated[SiteHostCache, Depends(get_site_host_cache)],
    _public: None = Depends(truly_public),
) -> SiteHostResponse:
    """The website *host* serves, or 404. Read the way a ``Host`` header is."""
    found = _site_host(host, cache)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    return SiteHostResponse(primary_host=found.primary_host)


@router.get("/api/sites/hosts/{host}/file", response_model=None)
def site_file_route(
    host: Annotated[str, Path(max_length=512)],
    cache: Annotated[SiteHostCache, Depends(get_site_host_cache)],
    files: Annotated[SiteFileCache, Depends(get_site_file_cache)],
    path: Annotated[str, Query(max_length=_MAX_PATH)] = "/",
    if_none_match: Annotated[str | None, Header()] = None,
    _public: None = Depends(truly_public),
) -> Response:
    """The file *path* names on the live version of the website *host* serves."""
    found = _site_host(host, cache)
    bucket = _bucket()
    if found is None or bucket is None:
        return plain_not_found()
    folder = SiteFolder(
        files, site_storage(), bucket, version_prefix(found.practice_id, found.live_version)
    )
    # A file's address carries no version, so it is the version that names
    # what a visitor already holds.
    return site_file_response(
        folder, path, etag=f'"v{found.live_version}"', if_none_match=if_none_match
    )


def _draft_for_token(token: str) -> str | None:
    """The folder of the draft *token* previews, while the token works."""
    session = create_standalone_session()
    try:
        row = PracticeSiteStore(session).by_preview_token_hash(preview_token_hash(token))
        if row is None or row.draft_id is None or row.preview_expires_at is None:
            return None
        if row.preview_expires_at <= utc_now():
            return None
        return draft_prefix(row.practice_id, row.draft_id)
    finally:
        session.close()


@router.get("/api/practice/website/preview/{token}/{path:path}", response_model=None)
def site_preview_route(
    token: Annotated[str, Path(max_length=64)],
    path: Annotated[str, Path(max_length=_MAX_PATH)],
    files: Annotated[SiteFileCache, Depends(get_site_file_cache)],
    _public: None = Depends(truly_public),
) -> Response:
    """The draft's file *path* names, for whoever holds a working preview token."""
    bucket = _bucket()
    prefix = _draft_for_token(token) if bucket else None
    if bucket is None or prefix is None:
        return plain_not_found()
    # The path arrives decoded; the resolver reads a path as it was sent.
    return site_file_response(SiteFolder(files, site_storage(), bucket, prefix), quote(path))
