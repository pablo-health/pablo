# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Copying a published website into the bucket the load balancer serves it from.

Storage is a dict, so what is proved is the shape of the copy: the version's
files at their paths, a copy of each index page at its folder's key, cache
lifetimes, the ``robots.txt`` a hidden website gets, that copying again drops
what the version no longer has, and what tidying keeps.
"""

from __future__ import annotations

import pytest
from app.sites.mirror import (
    ASSET_CACHE_CONTROL,
    PAGE_CACHE_CONTROL,
    ROBOTS_DISALLOW_ALL,
    SiteMirrorError,
    StorageSiteMirror,
    cache_control_for,
)

SOURCE, LIVE = "sites-private", "sites-public"
PRACTICE = "practice-1"
V3 = f"sites/{PRACTICE}/v3/"


class _Memory:
    """Two buckets in a dict: ``(bucket, name) -> (data, content_type, cache_control)``."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str, str | None]] = {}
        self.fail = False

    def put(self, bucket: str, name: str, data: bytes = b"x") -> None:
        self.objects[(bucket, name)] = (data, "application/octet-stream", None)

    def names(self, bucket: str) -> list[str]:
        return sorted(n for b, n in self.objects if b == bucket)

    def list_names(self, *, bucket: str, prefix: str) -> list[str]:
        if self.fail:
            raise ConnectionError("storage is away")
        return [n for n in self.names(bucket) if n.startswith(prefix)]

    def download_bytes(self, *, bucket: str, object_name: str) -> bytes:
        return self.objects[(bucket, object_name)][0]

    def upload_bytes(
        self,
        *,
        bucket: str,
        object_name: str,
        data: bytes,
        content_type: str,
        cache_control: str | None = None,
    ) -> None:
        self.objects[(bucket, object_name)] = (data, content_type, cache_control)

    def delete(self, *, bucket: str, object_name: str) -> None:
        self.objects.pop((bucket, object_name), None)


@pytest.fixture
def storage() -> _Memory:
    memory = _Memory()
    memory.put(SOURCE, f"{V3}index.html", b"<h1>home</h1>")
    memory.put(SOURCE, f"{V3}style.css", b"body{}")
    memory.put(SOURCE, f"{V3}about/index.html", b"<h1>about</h1>")
    memory.put(SOURCE, f"{V3}img/photo.jpg", b"\xff\xd8")
    memory.put(SOURCE, f"{V3}robots.txt", b"User-agent: *\nAllow: /\n")
    memory.put(SOURCE, f"sites/{PRACTICE}/draft/abc/index.html", b"draft")
    return memory


@pytest.fixture
def mirror(storage: _Memory) -> StorageSiteMirror:
    return StorageSiteMirror(storage, SOURCE, LIVE)  # type: ignore[arg-type]  # a dict stands in


def test_a_version_is_copied_with_its_folders_answering_for_their_index_pages(
    mirror: StorageSiteMirror, storage: _Memory
) -> None:
    mirror.mirror(PRACTICE, 3, hide_from_search=False)

    assert storage.names(LIVE) == [
        V3,
        f"{V3}about/",
        f"{V3}about/index.html",
        f"{V3}img/photo.jpg",
        f"{V3}index.html",
        f"{V3}robots.txt",
        f"{V3}style.css",
    ]
    data, content_type, cache = storage.objects[(LIVE, V3)]
    assert (data, content_type, cache) == (
        b"<h1>home</h1>",
        "text/html; charset=utf-8",
        PAGE_CACHE_CONTROL,
    )
    assert storage.objects[(LIVE, f"{V3}about/")][0] == b"<h1>about</h1>"
    assert storage.objects[(LIVE, f"{V3}style.css")][1:] == (
        "text/css; charset=utf-8",
        ASSET_CACHE_CONTROL,
    )
    assert storage.objects[(LIVE, f"{V3}img/photo.jpg")][1:] == ("image/jpeg", ASSET_CACHE_CONTROL)
    assert storage.objects[(LIVE, f"{V3}robots.txt")][0] == b"User-agent: *\nAllow: /\n"
    # The draft is not a version; it never leaves the private bucket.
    assert not any("draft" in name for name in storage.names(LIVE))


def test_a_hidden_website_serves_a_robots_file_disallowing_everything(
    mirror: StorageSiteMirror, storage: _Memory
) -> None:
    mirror.mirror(PRACTICE, 3, hide_from_search=True)

    data, content_type, cache = storage.objects[(LIVE, f"{V3}robots.txt")]
    assert (data, cache) == (ROBOTS_DISALLOW_ALL, PAGE_CACHE_CONTROL)
    assert content_type.startswith("text/plain")

    # Listing it again restores the website's own robots.txt.
    mirror.mirror(PRACTICE, 3, hide_from_search=False)
    assert storage.objects[(LIVE, f"{V3}robots.txt")][0] == b"User-agent: *\nAllow: /\n"


def test_copying_again_drops_what_the_version_no_longer_has(
    mirror: StorageSiteMirror, storage: _Memory
) -> None:
    storage.put(LIVE, f"{V3}old.css")
    storage.put(LIVE, f"{V3}gone/")

    mirror.mirror(PRACTICE, 3, hide_from_search=False)

    assert f"{V3}old.css" not in storage.names(LIVE)
    assert f"{V3}gone/" not in storage.names(LIVE)
    assert f"{V3}index.html" in storage.names(LIVE)


def test_a_version_with_no_files_is_refused_for_good(mirror: StorageSiteMirror) -> None:
    with pytest.raises(SiteMirrorError, match="no files") as raised:
        mirror.mirror(PRACTICE, 9, hide_from_search=False)
    assert raised.value.transient is False


def test_a_storage_failure_is_worth_retrying(mirror: StorageSiteMirror, storage: _Memory) -> None:
    storage.fail = True
    with pytest.raises(SiteMirrorError, match="ConnectionError") as raised:
        mirror.mirror(PRACTICE, 3, hide_from_search=False)
    assert raised.value.transient is True


def test_tidying_keeps_only_the_versions_named(mirror: StorageSiteMirror, storage: _Memory) -> None:
    for version in (1, 2, 3):
        storage.put(LIVE, f"sites/{PRACTICE}/v{version}/index.html")
        storage.put(LIVE, f"sites/{PRACTICE}/v{version}/")
    storage.put(LIVE, "sites/other-practice/v1/index.html")

    mirror.tidy(PRACTICE, {2, 3})
    assert storage.names(LIVE) == [
        "sites/other-practice/v1/index.html",
        f"sites/{PRACTICE}/v2/",
        f"sites/{PRACTICE}/v2/index.html",
        f"sites/{PRACTICE}/v3/",
        f"sites/{PRACTICE}/v3/index.html",
    ]

    mirror.tidy(PRACTICE, ())
    assert storage.names(LIVE) == ["sites/other-practice/v1/index.html"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("sites/p/v1/", PAGE_CACHE_CONTROL),
        ("sites/p/v1/index.html", PAGE_CACHE_CONTROL),
        ("sites/p/v1/about/", PAGE_CACHE_CONTROL),
        ("sites/p/v1/robots.txt", PAGE_CACHE_CONTROL),
        ("sites/p/v1/style.css", ASSET_CACHE_CONTROL),
        ("sites/p/v1/fonts/a.woff2", ASSET_CACHE_CONTROL),
    ],
)
def test_pages_are_cached_for_a_minute_and_assets_for_an_hour(name: str, expected: str) -> None:
    assert cache_control_for(name) == expected
