# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where a saved calendar's files are, and the order they were saved in.

A practice saves its SimplePractice web calendar's responses into a folder
named ``calendar`` (any case) and zips it. A browser names a second save of a
file ``name (2).json``; numbers in names sort as numbers. Pure: no database,
no model.
"""

from __future__ import annotations

import re
from pathlib import Path

CALENDAR_DIR = "calendar"

#: A browser's name for a second save of the same file: ``name (2).json``.
_SAVED_COPY = re.compile(r"^(?P<stem>.*?)(?:\s*\((?P<n>\d+)\))?$")


def calendar_dir(root: Path) -> Path | None:
    """The top-level calendar folder under ``root``, whatever its case."""
    root = Path(root)
    if not root.is_dir():
        return None
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name.lower() == CALENDAR_DIR:
            return child
    return None


def _natural(text: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in re.split(r"(\d+)", text)
        if part
    )


def capture_order(path: Path) -> tuple[object, ...]:
    """The order files were saved in, from their names.

    Numbers sort as numbers (``-2`` before ``-10``), and a browser's
    ``name (n).json`` comes after the ``name.json`` it copies, in ``n`` order.
    """
    match = _SAVED_COPY.match(path.stem)
    stem, copy = (match["stem"], int(match["n"] or 0)) if match else (path.stem, 0)
    return (_natural(stem), copy, path.name)


def calendar_files(root: Path) -> list[Path]:
    """The calendar folder's JSON files, in the order they were saved."""
    folder = calendar_dir(root)
    if folder is None:
        return []
    found = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".json"]
    return sorted(found, key=capture_order)


__all__ = ["CALENDAR_DIR", "calendar_dir", "calendar_files", "capture_order"]
