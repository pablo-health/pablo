# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The files of a practice's website, checked before anything is stored.

A website arrives either as a zip of a static folder (Settings > Website) or as
a mapping of path to bytes from something a deployment runs itself
(:meth:`app.sites.service.PracticeSiteService.save_draft_files`). Both end up
as :class:`SiteFiles` through the same checks:

* a path is relative, ``/``-separated, with no ``.`` or ``..`` segment, no
  empty segment, no backslash or control character, and at most
  :data:`MAX_PATH_LENGTH` characters;
* its extension is on :data:`CONTENT_TYPES`. The type a file is served with
  comes from there, never from whatever uploaded it;
* at most :data:`MAX_FILES` files, each at most :data:`MAX_FILE_BYTES`, all
  together at most :data:`MAX_TOTAL_BYTES`;
* ``index.html`` at the root.

A zip additionally may be at most :data:`MAX_ARCHIVE_BYTES`, may not hold a
symlink or an encrypted entry, and is read with the sizes counted as bytes come
out rather than trusted from its directory, so a zip that understates them
stops at the limit instead of filling memory. A zip whose files all sit in one
top-level folder is read as that folder. Finder's ``__MACOSX/`` and
``.DS_Store`` entries are left out.
"""

from __future__ import annotations

import io
import stat
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_FILES = 300
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_PATH_LENGTH = 512
INDEX = "index.html"
NOT_FOUND_PAGE = "404.html"

_HTML = "text/html; charset=utf-8"
#: Every extension a website may hold, and the type it is served with.
CONTENT_TYPES: dict[str, str] = {
    "html": _HTML,
    "htm": _HTML,
    "css": "text/css; charset=utf-8",
    "js": "text/javascript; charset=utf-8",
    "mjs": "text/javascript; charset=utf-8",
    "json": "application/json",
    "map": "application/json",
    "txt": "text/plain; charset=utf-8",
    "xml": "application/xml",
    "ico": "image/x-icon",
    "svg": "image/svg+xml",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "avif": "image/avif",
    "woff": "font/woff",
    "woff2": "font/woff2",
    "ttf": "font/ttf",
    "otf": "font/otf",
    "pdf": "application/pdf",
}

_IGNORED_FOLDERS = ("__MACOSX/",)
_IGNORED_NAMES = (".DS_Store",)
_READ_CHUNK = 64 * 1024


class SiteFilesError(ValueError):
    """The files cannot be a website; the message says why, in words for the practice."""


class SiteTooLargeError(SiteFilesError):
    """Past one of the size limits."""


@dataclass(frozen=True)
class SiteFiles:
    """A checked website: relative path to bytes, ``index.html`` among them."""

    files: Mapping[str, bytes]

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(len(data) for data in self.files.values())


def _megabytes(limit: int) -> str:
    return f"{limit // (1024 * 1024)} MB"


def content_type_for(path: str) -> str | None:
    """The type *path* is served with, or ``None`` when its extension is not allowed."""
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return None
    return CONTENT_TYPES.get(name.rsplit(".", 1)[1].lower())


def is_html(path: str) -> bool:
    return content_type_for(path) == _HTML


def check_path(path: str) -> str:
    """*path* if a website may hold a file there; :class:`SiteFilesError` if not."""
    if not path or len(path) > MAX_PATH_LENGTH:
        raise SiteFilesError(f"{path[:80] or 'A file'} has a name that is empty or too long.")
    if path.startswith("/") or "\\" in path or ":" in path.split("/", 1)[0]:
        raise SiteFilesError(f"{path} must be a path inside the folder.")
    if not path.isprintable():
        raise SiteFilesError("A file name holds a character that is not allowed.")
    if any(segment in {"", ".", ".."} for segment in path.split("/")):
        raise SiteFilesError(f"{path} must be a path inside the folder.")
    if content_type_for(path) is None:
        raise SiteFilesError(f"{path} is not a file type a website can use.")
    return path


def _ignored(name: str) -> bool:
    return name.startswith(_IGNORED_FOLDERS) or name.rsplit("/", 1)[-1] in _IGNORED_NAMES


def _unwrapped(names: list[str]) -> str:
    """The one top-level folder every name sits in, when ``index.html`` is not at the root."""
    if INDEX in names:
        return ""
    tops = {name.split("/", 1)[0] for name in names}
    if len(tops) == 1 and all("/" in name for name in names):
        return f"{tops.pop()}/"
    return ""


def _require_index(files: Mapping[str, bytes]) -> None:
    if INDEX not in files:
        raise SiteFilesError("The folder needs an index.html at the top.")


def _check_counts(count: int, total: int) -> None:
    if count > MAX_FILES:
        raise SiteTooLargeError(f"A website can have at most {MAX_FILES} files.")
    if total > MAX_TOTAL_BYTES:
        raise SiteTooLargeError(
            f"A website can be at most {_megabytes(MAX_TOTAL_BYTES)} once unzipped."
        )


def _read_capped(archive: zipfile.ZipFile, info: zipfile.ZipInfo, budget: int) -> bytes:
    """The entry's bytes, stopping as soon as it passes its own limit or *budget*."""
    limit = min(MAX_FILE_BYTES, budget)
    out = bytearray()
    with archive.open(info) as entry:
        while chunk := entry.read(_READ_CHUNK):
            out.extend(chunk)
            if len(out) > limit:
                if len(out) > MAX_FILE_BYTES:
                    raise SiteTooLargeError(
                        f"{info.filename} is larger than {_megabytes(MAX_FILE_BYTES)}."
                    )
                raise SiteTooLargeError(
                    f"A website can be at most {_megabytes(MAX_TOTAL_BYTES)} once unzipped."
                )
    return bytes(out)


def _entries(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """The archive's file entries, with symlinks, encryption and declared sizes refused."""
    entries = []
    for info in archive.infolist():
        if info.is_dir() or _ignored(info.filename):
            continue
        if stat.S_ISLNK(info.external_attr >> 16):
            raise SiteFilesError(f"{info.filename} is a link, which a website cannot hold.")
        if info.flag_bits & 0x1:
            raise SiteFilesError("The zip is password-protected.")
        if info.file_size > MAX_FILE_BYTES:
            raise SiteTooLargeError(f"{info.filename} is larger than {_megabytes(MAX_FILE_BYTES)}.")
        entries.append(info)
    _check_counts(len(entries), sum(info.file_size for info in entries))
    return entries


def read_zip(data: bytes) -> SiteFiles:
    """The website in the zip *data*; :class:`SiteFilesError` saying what is wrong."""
    if len(data) > MAX_ARCHIVE_BYTES:
        raise SiteTooLargeError(f"The zip can be at most {_megabytes(MAX_ARCHIVE_BYTES)}.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SiteFilesError("That file is not a zip.") from exc
    with archive:
        entries = _entries(archive)
        prefix = _unwrapped([info.filename for info in entries])
        files: dict[str, bytes] = {}
        budget = MAX_TOTAL_BYTES
        for info in entries:
            path = check_path(info.filename.removeprefix(prefix))
            if path in files:
                raise SiteFilesError(f"{path} is in the zip more than once.")
            try:
                content = _read_capped(archive, info, budget)
            except (zipfile.BadZipFile, NotImplementedError, OSError) as exc:
                raise SiteFilesError(f"{info.filename} could not be read from the zip.") from exc
            budget -= len(content)
            files[path] = content
    _require_index(files)
    return SiteFiles(files)


def check_files(files: Mapping[str, bytes]) -> SiteFiles:
    """*files* as a website, held to the same rules as a zip's contents."""
    checked: dict[str, bytes] = {}
    for path, content in files.items():
        if len(content) > MAX_FILE_BYTES:
            raise SiteTooLargeError(f"{path} is larger than {_megabytes(MAX_FILE_BYTES)}.")
        checked[check_path(path)] = bytes(content)
    _check_counts(len(checked), sum(len(content) for content in checked.values()))
    _require_index(checked)
    return SiteFiles(checked)
