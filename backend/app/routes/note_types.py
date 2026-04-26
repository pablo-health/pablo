# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note-type catalog API.

Exposes the registered :class:`app.notes.NoteTypeDefinition` entries so the
frontend can render note pickers, viewers, and editors dynamically. The
routes return plain catalog data and carry no PHI — tier-gating logic
lives in downstream overlays, not here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..api_errors import NotFoundError
from ..notes import (
    NoteFieldDef,
    NoteSectionDef,
    NoteTypeDefinition,
    NoteTypeRegistry,
    get_default_registry,
)

router = APIRouter(prefix="/api/note-types", tags=["note-types"])


class NoteFieldSchema(BaseModel):
    """Serialized :class:`NoteFieldDef`."""

    key: str
    label: str
    kind: str = Field(
        description="Field shape: 'text', 'list', or 'structured'.",
    )
    ai_hint: str = ""

    @classmethod
    def from_def(cls, field_def: NoteFieldDef) -> NoteFieldSchema:
        return cls(
            key=field_def.key,
            label=field_def.label,
            kind=field_def.kind,
            ai_hint=field_def.ai_hint,
        )


class NoteSectionSchema(BaseModel):
    """Serialized :class:`NoteSectionDef`."""

    key: str
    label: str
    fields: list[NoteFieldSchema]

    @classmethod
    def from_def(cls, section: NoteSectionDef) -> NoteSectionSchema:
        return cls(
            key=section.key,
            label=section.label,
            fields=[NoteFieldSchema.from_def(f) for f in section.fields],
        )


class NoteTypeSchema(BaseModel):
    """Serialized :class:`NoteTypeDefinition`."""

    key: str
    label: str
    description: str
    tier: str = Field(description="'core' or 'extension'.")
    sections: list[NoteSectionSchema]

    @classmethod
    def from_def(cls, definition: NoteTypeDefinition) -> NoteTypeSchema:
        return cls(
            key=definition.key,
            label=definition.label,
            description=definition.description,
            tier=definition.tier,
            sections=[NoteSectionSchema.from_def(s) for s in definition.sections],
        )


class NoteTypeListResponse(BaseModel):
    """Envelope for :meth:`list_note_types`."""

    note_types: list[NoteTypeSchema]


def get_registry() -> NoteTypeRegistry:
    """FastAPI dependency indirection so tests can swap the registry."""
    return get_default_registry()


@router.get("", response_model=NoteTypeListResponse)
def list_note_types(
    registry: NoteTypeRegistry = Depends(get_registry),
) -> NoteTypeListResponse:
    """Return all registered note types, sorted by key."""
    return NoteTypeListResponse(
        note_types=[NoteTypeSchema.from_def(d) for d in registry.all()],
    )


@router.get("/{key}", response_model=NoteTypeSchema)
def get_note_type(
    key: str,
    registry: NoteTypeRegistry = Depends(get_registry),
) -> NoteTypeSchema:
    """Return a single note-type definition by key."""
    if not registry.has(key):
        raise NotFoundError(f"Note type {key!r} not found")
    return NoteTypeSchema.from_def(registry.get(key))
