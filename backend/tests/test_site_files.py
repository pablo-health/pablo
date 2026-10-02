# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a website upload may hold: every reason a zip, or files made in memory, is refused."""

from __future__ import annotations

import io
import stat
import zipfile

import pytest
from app.sites import files
from app.sites.files import (
    MAX_ARCHIVE_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    SiteFilesError,
    SiteTooLargeError,
    check_files,
    content_type_for,
    read_zip,
)


def _zip(entries: dict[str, bytes], *, method: int = zipfile.ZIP_DEFLATED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", method) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return out.getvalue()


def _zip_with(info: zipfile.ZipInfo, data: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("index.html", b"home")
        archive.writestr(info, data)
    return out.getvalue()


SITE = {"index.html": b"<h1>Home</h1>", "css/site.css": b"body{}", "img/logo.png": b"\x89PNG"}


class TestAcceptedZips:
    def test_a_folder_with_index_at_the_top(self) -> None:
        site = read_zip(_zip(SITE))
        assert dict(site.files) == SITE
        assert site.file_count == 3
        assert site.total_bytes == sum(len(v) for v in SITE.values())

    def test_one_top_level_folder_is_unwrapped(self) -> None:
        site = read_zip(_zip({f"my-site/{k}": v for k, v in SITE.items()}))
        assert set(site.files) == set(SITE)

    def test_finder_leftovers_are_left_out(self) -> None:
        entries = {
            **{f"my-site/{k}": v for k, v in SITE.items()},
            "__MACOSX/my-site/._index.html": b"junk",
            "my-site/.DS_Store": b"junk",
        }
        assert set(read_zip(_zip(entries)).files) == set(SITE)

    def test_a_single_page(self) -> None:
        assert set(read_zip(_zip({"index.html": b"hi"})).files) == {"index.html"}

    def test_folder_entries_are_not_files(self) -> None:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("about/", b"")
            archive.writestr("index.html", b"hi")
            archive.writestr("about/index.html", b"about")
        assert set(read_zip(out.getvalue()).files) == {"index.html", "about/index.html"}


class TestRefusedZips:
    def test_not_a_zip(self) -> None:
        with pytest.raises(SiteFilesError, match="not a zip"):
            read_zip(b"plain text")

    def test_too_large_to_upload(self) -> None:
        with pytest.raises(SiteTooLargeError, match="at most 25 MB"):
            read_zip(b"x" * (MAX_ARCHIVE_BYTES + 1))

    def test_without_index(self) -> None:
        with pytest.raises(SiteFilesError, match=r"index\.html"):
            read_zip(_zip({"home.html": b"hi"}))

    def test_index_only_in_a_folder_beside_others(self) -> None:
        with pytest.raises(SiteFilesError, match=r"index\.html"):
            read_zip(_zip({"a/index.html": b"hi", "b/x.css": b"y"}))

    @pytest.mark.parametrize(
        "name",
        ["/etc/passwd.txt", "../escape.html", "a/../../escape.html", "a\\b.html", "C:/x.html"],
    )
    def test_a_path_outside_the_folder(self, name: str) -> None:
        with pytest.raises(SiteFilesError, match="inside the folder"):
            read_zip(_zip({"index.html": b"hi", name: b"x"}))

    def test_a_symlink(self) -> None:
        info = zipfile.ZipInfo("link.html")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with pytest.raises(SiteFilesError, match="link"):
            read_zip(_zip_with(info, b"/etc/passwd"))

    def test_an_encrypted_entry(self) -> None:
        # The stdlib cannot write an encrypted entry, so set the "encrypted"
        # flag on the last entry's central directory record by hand.
        data = bytearray(_zip_with(zipfile.ZipInfo("secret.html"), b"x"))
        central = data.rfind(b"PK\x01\x02")
        data[central + 8] |= 0x1
        with pytest.raises(SiteFilesError, match="password"):
            read_zip(bytes(data))

    @pytest.mark.parametrize("name", ["run.php", "app.exe", "page.shtml", ".htaccess", "README"])
    def test_a_file_type_not_on_the_list(self, name: str) -> None:
        with pytest.raises(SiteFilesError, match="not a file type"):
            read_zip(_zip({"index.html": b"hi", name: b"x"}))

    def test_too_many_files(self) -> None:
        entries = {"index.html": b"hi", **{f"p{i}.html": b"x" for i in range(MAX_FILES)}}
        with pytest.raises(SiteTooLargeError, match=f"at most {MAX_FILES} files"):
            read_zip(_zip(entries))

    def test_a_file_past_the_per_file_limit(self) -> None:
        entries = {"index.html": b"hi", "big.pdf": b"\0" * (MAX_FILE_BYTES + 1)}
        with pytest.raises(SiteTooLargeError, match=r"big\.pdf is larger than 10 MB"):
            read_zip(_zip(entries))

    def test_a_bomb_past_the_unzipped_total(self) -> None:
        # Eleven files of zeros, each just under the per-file limit: a few
        # kilobytes zipped, over a hundred megabytes unzipped.
        entries = {"index.html": b"hi"}
        entries |= {f"z{i}.txt": b"\0" * (MAX_FILE_BYTES - 1) for i in range(11)}
        data = _zip(entries)
        assert len(data) < 1024 * 1024
        with pytest.raises(SiteTooLargeError, match="once unzipped"):
            read_zip(data)

    def test_a_bomb_that_understates_its_size_stops_at_the_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The directory says each file is small; the bytes say otherwise. The
        # read counts what comes out and stops, whatever the directory says.
        monkeypatch.setattr(files, "MAX_FILE_BYTES", 1000)
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("index.html", b"hi")
            archive.writestr("big.txt", b"\0" * 5000)
        data = bytearray(out.getvalue())
        # Rewrite big.txt's uncompressed size, in its local header and in the
        # central directory, to claim 10 bytes.
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive:
            info = archive.getinfo("big.txt")
        real = (5000).to_bytes(4, "little")
        claimed = (10).to_bytes(4, "little")
        start = info.header_offset
        data[start + 22 : start + 26] = claimed
        central = data.rfind(b"PK\x01\x02")
        assert data[central + 24 : central + 28] == real
        data[central + 24 : central + 28] = claimed
        with pytest.raises(SiteFilesError):
            read_zip(bytes(data))

    def test_the_same_path_twice(self) -> None:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("index.html", b"one")
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("index.html", b"two")
        with pytest.raises(SiteFilesError, match="more than once"):
            read_zip(out.getvalue())


class TestFilesMadeInMemory:
    def test_held_to_the_same_rules(self) -> None:
        assert dict(check_files(SITE).files) == SITE
        with pytest.raises(SiteFilesError, match=r"index\.html"):
            check_files({"about.html": b"x"})
        with pytest.raises(SiteFilesError, match="inside the folder"):
            check_files({"index.html": b"x", "../up.html": b"x"})
        with pytest.raises(SiteFilesError, match="not a file type"):
            check_files({"index.html": b"x", "run.sh": b"x"})
        with pytest.raises(SiteTooLargeError):
            check_files({"index.html": b"\0" * (MAX_FILE_BYTES + 1)})


class TestContentTypes:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("index.html", "text/html; charset=utf-8"),
            ("A/PAGE.HTM", "text/html; charset=utf-8"),
            ("app.mjs", "text/javascript; charset=utf-8"),
            ("logo.svg", "image/svg+xml"),
            ("font.woff2", "font/woff2"),
            ("app.js.map", "application/json"),
            ("noextension", None),
            ("evil.php", None),
        ],
    )
    def test_from_the_extension_only(self, path: str, expected: str | None) -> None:
        assert content_type_for(path) == expected
