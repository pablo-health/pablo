# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which file of a website a request path names.

Static-hosting rules, decided against the version's list of files so that
only a file the version holds can ever be read:

* ``/`` and any path ending in ``/`` mean that folder's ``index.html``;
* a path naming a folder without its trailing slash (``/about`` when
  ``about/index.html`` exists) is sent to the slashed form, so the page's
  relative links resolve inside the folder;
* anything else that is not a file of the version, including a path with a
  ``.`` or ``..`` segment, a backslash or a control character, is missing:
  the site's ``404.html`` with status 404 when it has one, else a plain 404.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote

from .files import INDEX, NOT_FOUND_PAGE

if TYPE_CHECKING:
    from collections.abc import Collection


@dataclass(frozen=True)
class SitePath:
    """What to answer a request path with."""

    status: int
    #: The file to send (with ``status``), or ``None`` for an empty answer.
    file: str | None = None
    #: For a 301: where to, relative to the address the visitor asked for.
    location: str | None = None


_MISSING_SEGMENTS = {"", ".", ".."}


def _is_clean(path: str) -> bool:
    if "\\" in path or not path.isprintable():
        return False
    folder = path.removesuffix("/")
    return not folder or all(segment not in _MISSING_SEGMENTS for segment in folder.split("/"))


def _missing(files: Collection[str]) -> SitePath:
    return SitePath(404, NOT_FOUND_PAGE if NOT_FOUND_PAGE in files else None)


def resolve_site_path(request_path: str, files: Collection[str]) -> SitePath:
    """The answer to *request_path* (as sent, percent-encoded) for a version holding *files*."""
    path = unquote(request_path).removeprefix("/")
    if not _is_clean(path):
        return _missing(files)
    if path == "" or path.endswith("/"):
        candidate = f"{path}{INDEX}"
        return SitePath(200, candidate) if candidate in files else _missing(files)
    if path in files:
        return SitePath(200, path)
    if f"{path}/{INDEX}" in files:
        return SitePath(301, location=f"{quote(path.rsplit('/', 1)[-1])}/")
    return _missing(files)
