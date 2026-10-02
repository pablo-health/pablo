# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What is kept in memory of a website's files, and how much."""

from __future__ import annotations

from app.sites.storage import SiteFileCache


class _CountingStorage:
    """Answers reads from a dict and counts them."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.reads: list[str] = []
        self.lists: list[str] = []

    def download_bytes(self, *, bucket: str, object_name: str) -> bytes:
        self.reads.append(object_name)
        return self.objects[object_name]

    def list_names(self, *, bucket: str, prefix: str) -> list[str]:
        self.lists.append(prefix)
        return [name for name in self.objects if name.startswith(prefix)]


def test_a_folder_is_listed_once() -> None:
    storage = _CountingStorage({"v1/index.html": b"a", "v1/css/s.css": b"b", "v2/index.html": b"c"})
    cache = SiteFileCache()

    for _ in range(3):
        assert cache.files_in(storage, "b", "v1/") == {"index.html", "css/s.css"}
    assert storage.lists == ["v1/"]


def test_files_are_kept_up_to_the_byte_budget_oldest_first_out() -> None:
    storage = _CountingStorage({"a": b"x" * 40, "b": b"y" * 40, "c": b"z" * 40})
    cache = SiteFileCache(max_bytes=100, max_file_bytes=50)

    for name in ("a", "b", "a", "c"):
        cache.read(storage, "bucket", name)
    # a and b fit; c pushes out the least recently used, which is b.
    cache.read(storage, "bucket", "a")
    cache.read(storage, "bucket", "b")

    assert storage.reads == ["a", "b", "c", "b"]


def test_a_large_file_is_read_each_time() -> None:
    storage = _CountingStorage({"big.pdf": b"x" * 60})
    cache = SiteFileCache(max_file_bytes=50)

    cache.read(storage, "bucket", "big.pdf")
    cache.read(storage, "bucket", "big.pdf")

    assert storage.reads == ["big.pdf", "big.pdf"]
