# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where an imported note came from, as the archive importer wrote it.

An archive import lands each note with its provenance in the note's content,
under ``__source``, beside the note's sections: the system it came from, what
the note was there, who signed it and when, the visit it belongs to and the
diagnosis codes it carried. The web app never renders that key as a section;
the note API reads it into :class:`NoteSource` so every screen can say where a
note came from without parsing the content itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .note import Note

#: The key the archive importer writes a note's provenance under.
SOURCE_KEY = "__source"


@dataclass(frozen=True)
class NoteSource:
    """Where an imported note came from, as its document and export state it."""

    system: str
    kind: str | None
    title: str | None
    signed_by: str | None
    signed_at: datetime | None
    created_at: datetime | None
    visit_date: datetime | None
    #: ``(code, description)`` pairs, in the order the note listed them.
    diagnoses: tuple[tuple[str, str], ...]


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def read_note_source(content: dict[str, Any] | None) -> NoteSource | None:
    """The provenance in one content dict, or ``None`` when it carries none."""
    raw = content.get(SOURCE_KEY) if isinstance(content, dict) else None
    if not isinstance(raw, dict) or not isinstance(raw.get("system"), str) or not raw["system"]:
        return None
    raw_visit = raw.get("visit")
    visit: dict[str, Any] = raw_visit if isinstance(raw_visit, dict) else {}
    raw_diagnoses = raw.get("diagnoses")
    listed: list[Any] = raw_diagnoses if isinstance(raw_diagnoses, list) else []
    return NoteSource(
        system=raw["system"],
        kind=_text(raw.get("kind")),
        title=_text(raw.get("title")),
        signed_by=_text(raw.get("signed_by")),
        signed_at=_when(raw.get("signed_at")),
        created_at=_when(raw.get("created_at")),
        visit_date=_when(visit.get("starts_at")),
        diagnoses=tuple(
            (str(d["code"]), str(d.get("description") or ""))
            for d in listed
            if isinstance(d, dict) and d.get("code")
        ),
    )


def note_source(note: Note) -> NoteSource | None:
    """The provenance an imported note carries, or ``None`` for a note written here.

    The archive importer writes it into ``content``; a restricted note may only
    start empty, so there it sits in ``content_edited``.
    """
    return read_note_source(note.content) or read_note_source(note.content_edited)
