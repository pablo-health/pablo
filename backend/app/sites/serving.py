# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Answering a request for a file of a website, live or draft.

The backend answers with the file and the type its extension is served with,
or the site's ``404.html``, a plain 404, or a 301 to a folder's slashed form
(:mod:`app.sites.paths`).

These answers come from routes under ``/api``, which on a deployment that
serves the app and its API from one host share the app's origin, and a
practice can upload any HTML it likes. So every one carries a bare ``sandbox``
Content-Security-Policy, with no ``allow-*`` token at all: a page opened there,
deliberately or by a crafted link, runs no script, submits no form, opens no
window and navigates nothing but itself, in an origin of its own. It is inert
static content, which is all the draft preview needs to show the pages, and
never something that could pass for the app's own sign-in page. ``noindex``
keeps it out of search results too.

Visitors to a website host never see these headers: the web app's server
fetches the file and answers them with its own, on the practice's own origin,
where the website's scripts run (``frontend/src/lib/portal-host/practice-site.ts``).
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING

from fastapi import Response

from .files import content_type_for
from .paths import resolve_site_path

if TYPE_CHECKING:
    from ..services.file_storage import FileStorageProvider
    from .storage import SiteFileCache

#: No ``allow-*`` token, deliberately; see the module docstring.
SANDBOX_CSP = "sandbox; frame-ancestors 'none'"
INERT_HEADERS = {
    "Content-Security-Policy": SANDBOX_CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


@dataclass(frozen=True)
class SiteFolder:
    """One version's or draft's files: where they are, and the cache in front of them."""

    cache: SiteFileCache
    storage: FileStorageProvider
    bucket: str
    prefix: str


def plain_not_found() -> Response:
    return Response(
        "Not Found", status_code=404, media_type="text/plain; charset=utf-8", headers=INERT_HEADERS
    )


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    tags = {tag.strip().removeprefix("W/") for tag in if_none_match.split(",")}
    return "*" in tags or etag in tags


def site_file_response(
    folder: SiteFolder,
    request_path: str,
    *,
    etag: str | None = None,
    if_none_match: str | None = None,
) -> Response:
    """The answer to *request_path* (percent-encoded, as sent) from *folder*.

    *etag* names the folder's contents; a request already holding it is
    answered 304 without reading the file.
    """
    files = folder.cache.files_in(folder.storage, folder.bucket, folder.prefix)
    answer = resolve_site_path(request_path, files)
    if answer.location is not None:
        return Response(status_code=301, headers={**INERT_HEADERS, "Location": answer.location})
    if answer.file is None:
        return plain_not_found()
    headers = dict(INERT_HEADERS)
    if etag is not None:
        headers["ETag"] = etag
        if answer.status == HTTPStatus.OK and _etag_matches(if_none_match, etag):
            return Response(status_code=304, headers=headers)
    return Response(
        folder.cache.read(folder.storage, folder.bucket, f"{folder.prefix}{answer.file}"),
        status_code=answer.status,
        media_type=content_type_for(answer.file),
        headers=headers,
    )
