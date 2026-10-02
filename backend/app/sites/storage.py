# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading a website's files for visitors, with what never changes kept in memory.

A published version's folder and a draft's folder never change once written
(see :mod:`app.sites.service`), so the list of a folder's files and the files
themselves are kept here for as long as there is room: the lists for up to
:data:`MAX_LISTS` folders, the files up to :data:`MAX_CACHED_BYTES` in all,
each the least recently used going first. A visitor's request costs storage
reads only the first time.
"""

from __future__ import annotations

from collections import OrderedDict
from functools import cache
from threading import Lock
from typing import TYPE_CHECKING

from ..services.file_storage import file_storage_from_settings
from ..settings import get_settings

if TYPE_CHECKING:
    from ..services.file_storage import FileStorageProvider

MAX_LISTS = 512
MAX_CACHED_BYTES = 64 * 1024 * 1024
#: A file larger than this is read each time rather than kept.
MAX_CACHED_FILE_BYTES = 2 * 1024 * 1024


@cache
def site_storage() -> FileStorageProvider:
    """The deployment's storage provider, made once."""
    return file_storage_from_settings(get_settings())


class SiteFileCache:
    """Folder lists and file bytes, bounded, least recently used first out."""

    def __init__(
        self,
        *,
        max_lists: int = MAX_LISTS,
        max_bytes: int = MAX_CACHED_BYTES,
        max_file_bytes: int = MAX_CACHED_FILE_BYTES,
    ) -> None:
        self._max_lists = max_lists
        self._max_bytes = max_bytes
        self._max_file_bytes = max_file_bytes
        self._lists: OrderedDict[tuple[str, str], frozenset[str]] = OrderedDict()
        self._files: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self._bytes = 0
        self._lock = Lock()

    def files_in(self, storage: FileStorageProvider, bucket: str, prefix: str) -> frozenset[str]:
        """The paths under *prefix*, relative to it."""
        key = (bucket, prefix)
        with self._lock:
            kept = self._lists.get(key)
            if kept is not None:
                self._lists.move_to_end(key)
                return kept
        found = frozenset(
            name.removeprefix(prefix) for name in storage.list_names(bucket=bucket, prefix=prefix)
        )
        with self._lock:
            self._lists[key] = found
            while len(self._lists) > self._max_lists:
                self._lists.popitem(last=False)
        return found

    def read(self, storage: FileStorageProvider, bucket: str, object_name: str) -> bytes:
        key = (bucket, object_name)
        with self._lock:
            kept = self._files.get(key)
            if kept is not None:
                self._files.move_to_end(key)
                return kept
        data = storage.download_bytes(bucket=bucket, object_name=object_name)
        if len(data) > self._max_file_bytes:
            return data
        with self._lock:
            if key not in self._files:
                self._files[key] = data
                self._bytes += len(data)
            while self._bytes > self._max_bytes:
                _, dropped = self._files.popitem(last=False)
                self._bytes -= len(dropped)
        return data

    def clear(self) -> None:
        with self._lock:
            self._lists.clear()
            self._files.clear()
            self._bytes = 0


_file_cache = SiteFileCache()


def get_site_file_cache() -> SiteFileCache:
    """FastAPI dependency — the process-wide cache, which tests override."""
    return _file_cache
