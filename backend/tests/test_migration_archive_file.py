# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Opening one file from an unpacked import archive.

The path arrives as a query parameter, so the only files it may reach are the
ones inside the archive's own directory. Each refusal here returns ``None``,
which the route turns into its ordinary "not part of this import" 404.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.migration.ledger import archive_file

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    archive = tmp_path / "archive"
    (archive / "Clients" / "Pat").mkdir(parents=True)
    (archive / "Clients" / "Pat" / "note.pdf").write_bytes(b"%PDF note")
    (tmp_path / "outside.pdf").write_bytes(b"%PDF outside")
    return archive


def test_a_file_in_the_archive_opens(root: Path) -> None:
    target = archive_file(root, "Clients/Pat/note.pdf")
    assert target == (root / "Clients" / "Pat" / "note.pdf").resolve()
    assert target.read_bytes() == b"%PDF note"


@pytest.mark.parametrize(
    "relative",
    [
        "../outside.pdf",
        "Clients/../../outside.pdf",
        "/etc/passwd",
        "..%2Foutside.pdf",
        "Clients/Pat",
        "",
        "Clients/Pat/missing.pdf",
    ],
)
def test_anything_but_a_file_inside_the_archive_is_refused(root: Path, relative: str) -> None:
    assert archive_file(root, relative) is None


def test_a_link_pointing_out_of_the_archive_is_refused(root: Path, tmp_path: Path) -> None:
    (root / "link.pdf").symlink_to(tmp_path / "outside.pdf")
    assert archive_file(root, "link.pdf") is None
