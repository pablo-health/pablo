# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Readers turn an export archive into typed records.

One module per source layout. A reader is pure: it takes a directory and
returns dataclasses; it opens no database and calls no model. Every value it
returns comes from the archive's fixed layout, which is why each reader is
written against a captured fixture of that system's real output and not
against a description of it.

Two layouts are read, and :func:`detect_layout` says which an unpacked
upload is: the "Export - Complete" archive (notes, cards, documents), and a
``calendar/`` folder of saved web-calendar responses (the appointments
ahead). A zip holding both is neither.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from .simplepractice import SimplePracticeArchive, read_simplepractice_archive
from .simplepractice_calendar import (
    CALENDAR_DIR,
    SimplePracticeCalendar,
    read_simplepractice_calendar,
)

Layout = Literal["archive", "calendar", "mixed"]

#: Top-level folders only the export archive has.
_ARCHIVE_TOPS = {
    "Contacts",
    "Medical Records",
    "Psychotherapy Notes",
    "Administrative",
    "Billing Documents",
    "Secure messages",
    "Stored documents",
}

MIXED_LAYOUT_REASON = (
    "This zip holds both an export and a calendar folder. Upload them one at a time."
)
NO_LAYOUT_REASON = (
    "This zip holds no SimplePractice export or calendar folder. For a saved "
    "calendar, put its .json files in a folder named calendar and zip that folder."
)


def detect_layout(root: Path) -> Layout | None:
    """Which layout an unpacked upload has, or ``None`` when it has neither.

    ``calendar`` is a top-level ``calendar/`` folder with at least one JSON
    file in it; ``archive`` is any of the export's top-level folders;
    ``mixed`` is both, which no reader takes.
    """
    root = Path(root)
    calendar = any((root / CALENDAR_DIR).glob("*.json"))
    archive = any((root / top).is_dir() for top in _ARCHIVE_TOPS)
    if calendar and archive:
        return "mixed"
    if calendar:
        return "calendar"
    if archive:
        return "archive"
    return None


__all__ = [
    "MIXED_LAYOUT_REASON",
    "NO_LAYOUT_REASON",
    "Layout",
    "SimplePracticeArchive",
    "SimplePracticeCalendar",
    "detect_layout",
    "read_simplepractice_archive",
    "read_simplepractice_calendar",
]
