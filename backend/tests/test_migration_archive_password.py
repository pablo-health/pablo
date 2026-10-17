# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A protected export opens with its password, and says so without one.

SimplePractice protects every "Export - Complete" download with the
traditional zip cipher (deflate, flag bit 0, no AES); the fixture was zipped
the same way (``zip -P``) from a two-file synthetic tree and matches the
real download's member flags. The password unpacks the archive once; what
lands on disk is the plain tree, and nothing keeps the password.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from app.migration.ledger import ArchiveError, archive_path, store_archive

FIXTURE = Path(__file__).parent / "fixtures" / "protected_export.zip"
PASSWORD = "pablo-test"


def test_the_password_unpacks_the_export_to_a_plain_tree(tmp_path: Path) -> None:
    ref = store_archive(tmp_path, "practice_x", FIXTURE.read_bytes(), PASSWORD)

    root = archive_path(tmp_path, "practice_x", ref)
    assert root is not None
    # The top-level export folder is unwrapped, as for an unprotected zip.
    card = root / "Contacts" / "Test Person - 1.vcf"
    assert card.read_text().startswith("BEGIN:VCARD")
    assert (root / "Secure messages" / "Provider-Test-Person.txt").exists()


def test_without_the_password_the_screen_is_told_to_ask_for_one(tmp_path: Path) -> None:
    with pytest.raises(ArchiveError) as caught:
        store_archive(tmp_path, "practice_x", FIXTURE.read_bytes())

    assert caught.value.code == "ARCHIVE_PASSWORD_REQUIRED"
    assert "password" in str(caught.value)
    assert list(tmp_path.rglob("*.vcf")) == []  # nothing half-unpacked is left behind


def test_a_wrong_password_is_said_to_be_wrong_not_a_bad_file(tmp_path: Path) -> None:
    with pytest.raises(ArchiveError) as caught:
        store_archive(tmp_path, "practice_x", FIXTURE.read_bytes(), "not-it")

    assert caught.value.code == "ARCHIVE_PASSWORD_WRONG"
    assert list(tmp_path.rglob("*.vcf")) == []


def test_an_unprotected_zip_ignores_a_password_given_anyway(tmp_path: Path) -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            "Export - Complete - plain/Contacts/Test Person - 1.vcf", "BEGIN:VCARD\nEND:VCARD\n"
        )

    ref = store_archive(tmp_path, "practice_x", buffer.getvalue(), "whatever")

    root = archive_path(tmp_path, "practice_x", ref)
    assert root is not None
    assert (root / "Contacts" / "Test Person - 1.vcf").exists()
