# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note-type catalog API.

Exposes the registered :class:`app.notes.NoteTypeDefinition` entries so the
frontend can render note pickers, viewers, and editors dynamically, and
lets a practice save and retire note types of its own. The routes carry
catalog data and no PHI — tier-gating logic lives in downstream consumers,
not here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field

from ..api_errors import NotFoundError
from ..auth.route_access import subscription_exempt
from ..auth.service import get_current_user, require_baa_acceptance
from ..notes import (
    NoteFieldDef,
    NoteInputDef,
    NoteSectionDef,
    NoteTypeAuthorizer,
    NoteTypeDefinition,
    NoteTypeRegistry,
    get_default_registry,
    get_note_type_authorizer,
)
from ..notes.practice_types import (
    SLUG_PATTERN,
    PracticeNoteTypeSpec,
    practice_key,
    stored_to_definition,
)
from ..repositories import PracticeNoteTypeRepository, get_practice_note_type_repository
from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..models import User

logger = logging.getLogger(__name__)

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


class NoteInputSchema(BaseModel):
    """Serialized :class:`NoteInputDef`: a value supplied when a note is generated."""

    key: str
    label: str
    kind: str = Field(description="'text' or 'choice'.")
    options: list[str] = Field(default_factory=list)
    required: bool = False

    @classmethod
    def from_def(cls, input_def: NoteInputDef) -> NoteInputSchema:
        return cls(
            key=input_def.key,
            label=input_def.label,
            kind=input_def.kind,
            options=list(input_def.options),
            required=input_def.required,
        )


class NoteTypeSchema(BaseModel):
    """Serialized :class:`NoteTypeDefinition`."""

    key: str
    label: str
    description: str
    tier: str = Field(description="'core' or 'extension'.")
    context: str = Field(description="'session', 'patient', or 'practice'.")
    sections: list[NoteSectionSchema]
    inputs: list[NoteInputSchema] = Field(default_factory=list)
    version: int | None = Field(
        default=None,
        description="Version of a practice-defined type; null for built-in types.",
    )
    is_locked: bool = Field(
        default=False,
        description=(
            "True when the injected authorizer does not permit the caller "
            "to create this note type. The open-source authorizer allows "
            "everything, so OSS responses always carry False; a deployment "
            "may inject an authorizer that locks certain extension types per "
            "its own policy. Frontends should render locked types as "
            "unavailable rather than as live picker options."
        ),
    )

    @classmethod
    def from_def(
        cls,
        definition: NoteTypeDefinition,
        *,
        is_locked: bool = False,
    ) -> NoteTypeSchema:
        return cls(
            key=definition.key,
            label=definition.label,
            description=definition.description,
            tier=definition.tier,
            context=definition.context,
            sections=[NoteSectionSchema.from_def(s) for s in definition.sections],
            inputs=[NoteInputSchema.from_def(i) for i in definition.inputs],
            version=definition.version,
            is_locked=is_locked,
        )


class NoteTypeListResponse(BaseModel):
    """Envelope for :meth:`list_note_types`."""

    note_types: list[NoteTypeSchema]


def get_registry() -> NoteTypeRegistry:
    """FastAPI dependency indirection so tests can swap the registry."""
    return get_default_registry()


@router.get("", response_model=NoteTypeListResponse)
def list_note_types(
    context: str | None = Query(
        default=None,
        description=(
            "Filter to note types with the given lifecycle context "
            "('session', 'patient', or 'practice'). Omit to return all."
        ),
    ),
    registry: NoteTypeRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    authorizer: NoteTypeAuthorizer = Depends(get_note_type_authorizer),
    _: None = Depends(subscription_exempt),
) -> NoteTypeListResponse:
    """Return the note types a new note can use, sorted by key.

    That is the built-in types plus the practice's own that have not been
    retired. When ``context`` is provided, returns only note types with a
    matching ``context`` field. An unknown context value returns an empty
    list rather than an error — callers can probe for support without a
    branch on the response shape.

    Each entry carries ``is_locked``, computed from the injected
    :class:`NoteTypeAuthorizer`. OSS ships an allow-all authorizer so
    self-hosters see every type unlocked; a deployment may inject an
    authorizer that locks certain extension types per its own policy.
    """
    definitions = registry.all()
    if context is not None:
        definitions = [d for d in definitions if d.context == context]
    return NoteTypeListResponse(
        note_types=[
            NoteTypeSchema.from_def(d, is_locked=not authorizer.is_allowed(user, d.key))
            for d in definitions
        ],
    )


@router.get("/{key}", response_model=NoteTypeSchema)
def get_note_type(
    key: str,
    version: int | None = Query(
        default=None,
        ge=1,
        description=(
            "For a practice-defined type, the version to return — the one a "
            "note records it was written against. Omit for the latest."
        ),
    ),
    registry: NoteTypeRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    authorizer: NoteTypeAuthorizer = Depends(get_note_type_authorizer),
    _: None = Depends(subscription_exempt),
) -> NoteTypeSchema:
    """Return a single note-type definition by key.

    A retired practice type is still returned here, so the notes written
    with it keep rendering; it is only left out of the list above.
    """
    try:
        definition = registry.get(key, version)
    except KeyError as exc:
        raise NotFoundError(f"Note type {key!r} not found") from exc
    return NoteTypeSchema.from_def(definition, is_locked=not authorizer.is_allowed(user, key))


_SLUG = Path(pattern=SLUG_PATTERN, description="The part of the key after 'custom.'.")


@router.put("/custom/{slug}", response_model=NoteTypeSchema)
def save_practice_note_type(
    spec: PracticeNoteTypeSpec,
    slug: str = _SLUG,
    user: User = Depends(require_baa_acceptance),
    repo: PracticeNoteTypeRepository = Depends(get_practice_note_type_repository),
) -> NoteTypeSchema:
    """Save a new version of one of the practice's own note types.

    The key is ``custom.<slug>``. Each save writes the next version and
    leaves earlier versions in place, so notes already written against them
    still render; saving a retired type makes it available again.
    """
    stored = repo.add_version(
        practice_key(slug),
        spec.model_dump(mode="json"),
        created_by=user.id,
        created_at=utc_now(),
    )
    logger.info("Saved practice note type %s version %d", stored.key, stored.version)
    return NoteTypeSchema.from_def(stored_to_definition(stored))


@router.delete(
    "/custom/{slug}",
    response_model=NoteTypeSchema,
    dependencies=[Depends(require_baa_acceptance)],
)
def retire_practice_note_type(
    slug: str = _SLUG,
    repo: PracticeNoteTypeRepository = Depends(get_practice_note_type_repository),
) -> NoteTypeSchema:
    """Retire one of the practice's own note types.

    New notes can no longer use it; notes already written with it keep
    rendering. Saving it again makes it available.
    """
    stored = repo.retire(practice_key(slug), utc_now())
    if stored is None:
        raise NotFoundError(f"Note type {practice_key(slug)!r} not found")
    logger.info("Retired practice note type %s", stored.key)
    return NoteTypeSchema.from_def(stored_to_definition(stored))
