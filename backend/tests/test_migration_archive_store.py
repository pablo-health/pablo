# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""An uploaded export is readable from any backend instance, not only the one
that took the upload.

The store unpacks into a per-instance cache and, when an object root is set,
keeps one plain zip per run in the object store. Another instance, with an
empty cache, fetches and unpacks it; deleting the run's archive clears both
copies; a deployment with no root behaves as before.
"""

from __future__ import annotations

import shutil
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from app.migration.archive_store import ArchiveStore
from app.migration.ledger import ArchiveError
from app.services.file_storage import LocalFileStorage

PROTECTED = Path(__file__).parent / "fixtures" / "protected_export.zip"
PASSWORD = "pablo-test"
PRACTICE = "practice_abc"


def _export_zip() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "Export - Complete - t/Contacts/Test Person - 1.vcf", "BEGIN:VCARD\nEND:VCARD\n"
        )
        zf.writestr("Export - Complete - t/Medical Records/Test Person/note.pdf", b"%PDF-1.4 note")
    return buffer.getvalue()


@pytest.fixture
def stores(tmp_path: Path) -> tuple[ArchiveStore, ArchiveStore, Path]:
    """Two instances sharing one object root (a local directory standing in for
    a bucket), each with its own cache."""
    root = tmp_path / "bucket"
    root.mkdir()
    provider = LocalFileStorage()
    first = ArchiveStore(tmp_path / "cache-a", str(root), provider_factory=lambda _: provider)
    second = ArchiveStore(tmp_path / "cache-b", str(root), provider_factory=lambda _: provider)
    return first, second, root


def test_put_unpacks_locally_and_keeps_one_zip_in_the_object_store(
    stores: tuple[ArchiveStore, ArchiveStore, Path],
) -> None:
    first, _, root = stores

    ref = first.put(PRACTICE, _export_zip())

    unpacked = first.path(PRACTICE, ref)
    assert unpacked is not None
    assert (unpacked / "Contacts" / "Test Person - 1.vcf").exists()
    (stored,) = list(root.rglob("*.zip"))
    assert stored.name == f"{ref}.zip"
    with zipfile.ZipFile(stored) as zf:
        # The export's top-level folder is unwrapped, and nothing is encrypted.
        assert sorted(zf.namelist()) == [
            "Contacts/Test Person - 1.vcf",
            "Medical Records/Test Person/note.pdf",
        ]
        assert all(not m.flag_bits & 0x1 for m in zf.infolist())


def test_another_instance_fetches_what_it_never_unpacked(
    stores: tuple[ArchiveStore, ArchiveStore, Path],
) -> None:
    first, second, _ = stores
    ref = first.put(PRACTICE, _export_zip())

    fetched = second.path(PRACTICE, ref)

    assert fetched is not None
    assert fetched != first.path(PRACTICE, ref)
    note = fetched / "Medical Records" / "Test Person" / "note.pdf"
    assert note.read_bytes() == b"%PDF-1.4 note"


def test_a_protected_upload_is_stored_unlocked_so_the_password_is_needed_once(
    stores: tuple[ArchiveStore, ArchiveStore, Path],
) -> None:
    first, second, _ = stores
    ref = first.put(PRACTICE, PROTECTED, PASSWORD)

    fetched = second.path(PRACTICE, ref)

    assert fetched is not None
    assert (fetched / "Contacts" / "Test Person - 1.vcf").read_text().startswith("BEGIN:VCARD")


def test_delete_clears_the_cache_and_the_object(
    stores: tuple[ArchiveStore, ArchiveStore, Path],
) -> None:
    first, second, root = stores
    ref = first.put(PRACTICE, _export_zip())
    assert second.path(PRACTICE, ref) is not None

    assert first.delete(PRACTICE, ref) is True

    assert list(root.rglob("*.zip")) == []
    assert first.path(PRACTICE, ref) is None
    # The other instance's cache is its own; the object it would re-fetch is gone.
    shutil.rmtree(second.path(PRACTICE, ref) or root / "none", ignore_errors=True)
    assert second.path(PRACTICE, ref) is None
    assert first.delete(PRACTICE, ref) is False


def test_without_an_object_root_the_cache_is_the_only_copy(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "cache", None)
    ref = store.put(PRACTICE, _export_zip())
    unpacked = store.path(PRACTICE, ref)
    assert unpacked is not None
    assert store.remote is False

    shutil.rmtree(unpacked)

    assert store.path(PRACTICE, ref) is None


def test_a_bad_upload_leaves_nothing_behind_anywhere(
    stores: tuple[ArchiveStore, ArchiveStore, Path], tmp_path: Path
) -> None:
    first, _, root = stores

    with pytest.raises(ArchiveError):
        first.put(PRACTICE, b"not a zip")

    assert list(root.rglob("*")) == []
    # The practice's cache directory may exist; nothing is in it.
    assert [p for p in (tmp_path / "cache-a").rglob("*") if p.is_file()] == []


@pytest.mark.parametrize("ref", ["", "../x", "a/b"])
def test_a_reference_that_is_not_a_name_is_refused(
    stores: tuple[ArchiveStore, ArchiveStore, Path], ref: str
) -> None:
    first, _, _ = stores
    assert first.path(PRACTICE, ref) is None
    assert first.delete(PRACTICE, ref) is False
