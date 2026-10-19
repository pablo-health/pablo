# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Copying a published website into the bucket the load balancer serves it from.

A deployment can serve published websites straight from object storage: a
world-readable bucket (``practice_site_live_bucket``) fronted by the load
balancer, with each website's hosts routed to the live version's folder in it
(see ``app.services.practice_domain_cloud.HostRoute``). The files a practice
publishes stay in the private bucket, drafts beside them; this module copies
one version of them into the public one, where a visitor's request is answered
without reaching the deployment at all.

The copy is the version's folder as published, at the same path
(``sites/<practice_id>/v<N>/``), plus what serving it from a bucket needs:

* a copy of each ``index.html`` at its folder's own key (``.../about/`` for
  ``.../about/index.html``, and the version folder itself for the top one), so
  a request for a folder answers with its index page -- a bucket serves the
  object whose name is the path, nothing more;
* ``Cache-Control`` on every object: a minute for pages and ``robots.txt``, an
  hour for everything else, the same as the frontend sends when it answers
  for a website host;
* when the practice asked search engines not to list the website, a
  ``robots.txt`` disallowing everything, in place of the website's own.

Copying a version again writes the same objects again and drops any the
version no longer has, so it is safe to repeat. The domain reconciler runs
it, before it routes the website's hosts at the folder, and never routes at a
folder it has not copied.

No PHI: a practice's public website.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .files import content_type_for
from .service import site_prefix, version_prefix

if TYPE_CHECKING:
    from collections.abc import Collection

    from ..services.file_storage import FileStorageProvider

INDEX_FILE = "index.html"
ROBOTS_FILE = "robots.txt"
#: What a website that asked not to be listed serves as its ``robots.txt``.
ROBOTS_DISALLOW_ALL = b"User-agent: *\nDisallow: /\n"
PAGE_CACHE_CONTROL = "public, max-age=60"
ASSET_CACHE_CONTROL = "public, max-age=3600"
_OCTET_STREAM = "application/octet-stream"


class SiteMirrorError(Exception):
    """A version could not be copied; ``transient`` says whether to try again
    next sweep (the storage was slow or busy) rather than report it."""

    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


class SiteMirror(Protocol):
    def mirror(self, practice_id: str, version: int, *, hide_from_search: bool) -> None:
        """Make the public bucket hold *version* of the practice's website,
        exactly, with the ``robots.txt`` *hide_from_search* asks for."""
        ...

    def tidy(self, practice_id: str, keep: Collection[int]) -> None:
        """Drop every version of the practice's website from the public bucket
        but *keep*; everything, with *keep* empty."""
        ...


def cache_control_for(name: str) -> str:
    """How long a browser or cache may keep the object *name*."""
    if name.endswith(("/", ".html", ".htm", f"/{ROBOTS_FILE}")) or name == ROBOTS_FILE:
        return PAGE_CACHE_CONTROL
    return ASSET_CACHE_CONTROL


@dataclass(frozen=True)
class StorageSiteMirror:
    """:class:`SiteMirror` between two buckets on one storage provider."""

    storage: FileStorageProvider
    source_bucket: str
    live_bucket: str

    def mirror(self, practice_id: str, version: int, *, hide_from_search: bool) -> None:
        prefix = version_prefix(practice_id, version)
        try:
            names = self.storage.list_names(bucket=self.source_bucket, prefix=prefix)
            if not names:
                msg = f"version {version} of the website has no files to serve"
                raise SiteMirrorError(msg, transient=False)
            before = set(self.storage.list_names(bucket=self.live_bucket, prefix=prefix))
            written: set[str] = set()
            for name in names:
                path = name.removeprefix(prefix)
                if path == ROBOTS_FILE and hide_from_search:
                    continue
                data = self.storage.download_bytes(bucket=self.source_bucket, object_name=name)
                self._put(name, data, content_type_for(path) or _OCTET_STREAM)
                written.add(name)
                if path == INDEX_FILE or path.endswith(f"/{INDEX_FILE}"):
                    folder = name[: -len(INDEX_FILE)]
                    self._put(folder, data, "text/html; charset=utf-8")
                    written.add(folder)
            if hide_from_search:
                self._put(
                    f"{prefix}{ROBOTS_FILE}", ROBOTS_DISALLOW_ALL, "text/plain; charset=utf-8"
                )
                written.add(f"{prefix}{ROBOTS_FILE}")
            for stale in sorted(before - written):
                self.storage.delete(bucket=self.live_bucket, object_name=stale)
        except SiteMirrorError:
            raise
        except Exception as e:
            msg = f"copying version {version} of the website: {type(e).__name__}"
            raise SiteMirrorError(msg, transient=True) from e

    def tidy(self, practice_id: str, keep: Collection[int]) -> None:
        kept = tuple(version_prefix(practice_id, n) for n in keep)
        try:
            for name in self.storage.list_names(
                bucket=self.live_bucket, prefix=site_prefix(practice_id)
            ):
                if not kept or not name.startswith(kept):
                    self.storage.delete(bucket=self.live_bucket, object_name=name)
        except Exception as e:
            msg = f"tidying the website's served versions: {type(e).__name__}"
            raise SiteMirrorError(msg, transient=True) from e

    def _put(self, name: str, data: bytes, content_type: str) -> None:
        self.storage.upload_bytes(
            bucket=self.live_bucket,
            object_name=name,
            data=data,
            content_type=content_type,
            cache_control=cache_control_for(name),
        )
