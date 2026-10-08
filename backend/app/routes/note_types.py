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
from typing import TYPE_CHECKING, Any, Self

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, model_validator

from ..api_errors import BadRequestError, NotFoundError, UnprocessableEntityError
from ..auth.route_access import subscription_exempt
from ..auth.service import get_current_user, require_baa_acceptance
from ..db import release_db_connection
from ..models import Patient, Transcript
from ..models.audit import AuditAction, ResourceType
from ..models.transcript import TranscriptModel  # noqa: TC001 — runtime Pydantic field
from ..notes import (
    NoteFieldDef,
    NoteInputDef,
    NoteSectionDef,
    NoteTypeAuthorizer,
    NoteTypeDefinition,
    NoteTypeRegistry,
    get_default_registry,
    get_note_type_authorizer,
    is_practice_key,
)
from ..notes.note_type_patch import PatchError, resolve_spec
from ..notes.practice_types import (
    SLUG_PATTERN,
    PracticeNoteTypeSpec,
    check_against_base,
    definition_for,
    practice_key,
    stored_to_definition,
    validate_note_inputs,
)
from ..notes.spec_templates import spec_template
from ..repositories import PracticeNoteTypeRepository, get_practice_note_type_repository
from ..services.audit_service import AuditService, get_audit_service
from ..services.note_generation_service import (
    NoteGenerationService,
    TransientNoteGenerationError,
)
from ..utcnow import utc_now
from .notes import get_note_generation_service

if TYPE_CHECKING:
    from ..models import User
    from ..notes.registry import BasedOn

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
    review_only: bool = Field(
        default=False,
        exclude_if=lambda value: value is False,
        description=(
            "Drafted for review beside the note; never shown or printed as part of it. "
            "Left out when false, as a spec stores it."
        ),
    )

    @classmethod
    def from_def(cls, section: NoteSectionDef) -> NoteSectionSchema:
        return cls(
            key=section.key,
            label=section.label,
            fields=[NoteFieldSchema.from_def(f) for f in section.fields],
            review_only=section.review_only,
        )


class NoteInputSchema(BaseModel):
    """Serialized :class:`NoteInputDef`: a value supplied when a note is generated."""

    key: str
    label: str
    kind: str = Field(description="'text' or 'choice'.")
    options: list[str] = Field(default_factory=list)
    required: bool = False
    default: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="The value a note takes when none was chosen. Left out when there is none.",
    )

    @classmethod
    def from_def(cls, input_def: NoteInputDef) -> NoteInputSchema:
        return cls(
            key=input_def.key,
            label=input_def.label,
            kind=input_def.kind,
            options=list(input_def.options),
            required=input_def.required,
            default=input_def.default,
        )


class BasedOnSchema(BaseModel):
    """Serialized :class:`BasedOn`: the base a practice type adjusts, and by how much."""

    key: str
    label: str
    additions: int
    hidden: int

    @classmethod
    def from_def(cls, based_on: BasedOn) -> BasedOnSchema:
        return cls(
            key=based_on.key,
            label=based_on.label,
            additions=based_on.additions,
            hidden=based_on.hidden,
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
    restricted: bool = Field(
        default=False,
        description=(
            "True for a note only its author can read (a psychotherapy note). "
            "Such a note is written by hand and is never generated from a "
            "transcript."
        ),
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
    spec: PracticeNoteTypeSpec | None = Field(
        default=None,
        description=(
            "The stored definition of a practice-defined type, prompts "
            "included, so it can be edited and saved again without losing "
            "them. Returned only by the single-type read; null for built-in "
            "types and in the list."
        ),
    )
    based_on: BasedOnSchema | None = Field(
        default=None,
        description=(
            "For a practice type built on a base: the base, and how many parts "
            "the practice added and hid. Null for every other type."
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
            restricted=definition.restricted,
            is_locked=is_locked,
            based_on=BasedOnSchema.from_def(definition.based_on) if definition.based_on else None,
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


class SampleVisitSchema(BaseModel):
    """A synthetic visit transcript to try a draft on."""

    id: str
    label: str
    transcript: str


class NoteTypeBaseSchema(BaseModel):
    """A built-in type a practice can base its own on, with what the editor needs."""

    key: str
    label: str
    description: str
    slug: str = Field(description="The slug a practice type based on this one saves under.")
    spec: PracticeNoteTypeSpec
    required_fields: list[str] = Field(
        description="Fields ('section.field') a type based on this one cannot hide."
    )
    samples: list[SampleVisitSchema]


class NoteTypeBaseListResponse(BaseModel):
    bases: list[NoteTypeBaseSchema]


@router.get(
    "/bases",
    response_model=NoteTypeBaseListResponse,
    dependencies=[Depends(get_current_user)],
)
def list_note_type_bases(
    registry: NoteTypeRegistry = Depends(get_registry),
    _: None = Depends(subscription_exempt),
) -> NoteTypeBaseListResponse:
    """Built-in note types a practice can start its own from, by adjusting them.

    Those are the built-ins written as specs. Each carries its spec, so the
    editor can show the base beside what the practice changes, and sample
    visits for trying a draft.
    """
    bases = []
    for definition in registry.all():
        template = spec_template(definition.key)
        base = registry.base_for(definition.key)
        if template is None or base is None or base.source_spec is None:
            continue
        bases.append(
            NoteTypeBaseSchema(
                key=base.key,
                label=base.label,
                description=base.description,
                slug=template.slug,
                spec=base.source_spec,
                required_fields=list(base.required_fields),
                samples=[
                    SampleVisitSchema(id=s.id, label=s.label, transcript=s.transcript)
                    for s in template.samples
                ],
            )
        )
    return NoteTypeBaseListResponse(bases=bases)


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
    repo: PracticeNoteTypeRepository = Depends(get_practice_note_type_repository),
    _: None = Depends(subscription_exempt),
) -> NoteTypeSchema:
    """Return a single note-type definition by key.

    A retired practice type is still returned here, so the notes written
    with it keep rendering; it is only left out of the list above. A
    practice type also carries its stored ``spec``, which the definition
    cannot give back: its system prompt has the generation floor appended.
    """
    try:
        definition = registry.get(key, version)
    except KeyError as exc:
        raise NotFoundError(f"Note type {key!r} not found") from exc
    schema = NoteTypeSchema.from_def(definition, is_locked=not authorizer.is_allowed(user, key))
    if is_practice_key(key):
        stored = repo.get(key, definition.version)
        if stored is not None:
            schema.spec = PracticeNoteTypeSpec.model_validate(stored.definition)
    return schema


_SLUG = Path(pattern=SLUG_PATTERN, description="The part of the key after 'custom.'.")


@router.put("/custom/{slug}", response_model=NoteTypeSchema)
def save_practice_note_type(
    spec: PracticeNoteTypeSpec,
    slug: str = _SLUG,
    user: User = Depends(require_baa_acceptance),
    repo: PracticeNoteTypeRepository = Depends(get_practice_note_type_repository),
    registry: NoteTypeRegistry = Depends(get_registry),
) -> NoteTypeSchema:
    """Save a new version of one of the practice's own note types.

    The key is ``custom.<slug>``. Each save writes the next version and
    leaves earlier versions in place, so notes already written against them
    still render; saving a retired type makes it available again. A type
    with a base stores only its base and patch, which must fit the base.
    """
    key = practice_key(slug)
    if spec.base == key:
        raise _patch_refused(PatchError([(("base",), "a note type cannot be based on itself")]))
    _check_base(spec, registry)
    stored = repo.add_version(
        key,
        spec.model_dump(mode="json"),
        created_by=user.id,
        created_at=utc_now(),
    )
    logger.info("Saved practice note type %s version %d", stored.key, stored.version)
    return NoteTypeSchema.from_def(stored_to_definition(stored, registry.base_for))


class ResolvedSpecResponse(BaseModel):
    spec: PracticeNoteTypeSpec


@router.post(
    "/resolve",
    response_model=ResolvedSpecResponse,
    dependencies=[Depends(require_baa_acceptance)],
)
def resolve_note_type_spec(
    spec: PracticeNoteTypeSpec,
    registry: NoteTypeRegistry = Depends(get_registry),
) -> ResolvedSpecResponse:
    """A type with a base, as the full spec it resolves to. Nothing is saved.

    For detaching a type from its base: saving the result gives a type that
    drafts exactly as the based one does now, and no longer follows the base.
    A full spec comes back unchanged.
    """
    _check_base(spec, registry)
    if spec.base is None or spec.patch is None:
        return ResolvedSpecResponse(spec=spec)
    base = registry.base_for(spec.base)
    if base is None or base.source_spec is None:
        raise NotFoundError(f"Note type {spec.base!r} not found")
    resolved = resolve_spec(base.source_spec, spec.patch, base.required_fields)
    return ResolvedSpecResponse(
        spec=resolved.model_copy(update={"label": spec.label, "description": spec.description})
    )


def _check_base(spec: PracticeNoteTypeSpec, registry: NoteTypeRegistry) -> None:
    try:
        check_against_base(spec, registry.base_for)
    except PatchError as exc:
        raise _patch_refused(exc) from exc


def _patch_refused(error: PatchError) -> RequestValidationError:
    """A patch that does not fit its base, located the way a schema refusal is.

    Each problem's location is under ``patch`` (or is ``base``), so the
    editor puts the message where it puts any other validation message.
    """
    return RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("body", *(where if where[:1] == ("base",) else ("patch", *where))),
                "msg": message,
                "input": None,
            }
            for where, message in error.problems
        ]
    )


@router.delete(
    "/custom/{slug}",
    response_model=NoteTypeSchema,
    dependencies=[Depends(require_baa_acceptance)],
)
def retire_practice_note_type(
    slug: str = _SLUG,
    repo: PracticeNoteTypeRepository = Depends(get_practice_note_type_repository),
    registry: NoteTypeRegistry = Depends(get_registry),
) -> NoteTypeSchema:
    """Retire one of the practice's own note types.

    New notes can no longer use it; notes already written with it keep
    rendering. Saving it again makes it available.
    """
    stored = repo.retire(practice_key(slug), utc_now())
    if stored is None:
        raise NotFoundError(f"Note type {practice_key(slug)!r} not found")
    logger.info("Retired practice note type %s", stored.key)
    return NoteTypeSchema.from_def(stored_to_definition(stored, registry.base_for))


PREVIEW_KEY = practice_key("preview")
"""Key an unsaved definition drafts under; never stored."""

PREVIEW_TRANSCRIPT_MAX_CHARS = 200_000
"""A long visit is well under this; it bounds what one preview can send."""


class NoteDraftPreviewRequest(BaseModel):
    """A note type to try, and the transcript to draft from.

    Name a type that exists (``key``, with ``version`` for an older practice
    version) or send one that has not been saved (``spec``) — exactly one.
    """

    key: str | None = None
    version: int | None = Field(default=None, ge=1)
    spec: PracticeNoteTypeSpec | None = None
    transcript: TranscriptModel
    inputs: dict[str, str] | None = None

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if (self.key is None) == (self.spec is None):
            raise ValueError("send exactly one of key or spec")
        if self.spec is not None and self.version is not None:
            raise ValueError("version applies only to a saved key")
        content = self.transcript.content.strip()
        if not content:
            raise ValueError("transcript is empty")
        if len(content) > PREVIEW_TRANSCRIPT_MAX_CHARS:
            raise ValueError(f"transcript is longer than {PREVIEW_TRANSCRIPT_MAX_CHARS} characters")
        return self


class NoteDraftPreviewResponse(BaseModel):
    """The draft, shaped like a generated note's content. Nothing is saved."""

    key: str
    version: int | None
    sections: dict[str, dict[str, Any]]


def _preview_patient() -> Patient:
    """Stand-in for the client a preview has none of.

    Practice types never read the patient, and the built-in prompts read
    only ``diagnosis``, which stays unset so nothing is suggested.
    """
    now = utc_now()
    return Patient(id="preview", first_name="", last_name="", created_at=now, updated_at=now)


@router.post("/preview", response_model=NoteDraftPreviewResponse)
def preview_note_draft(
    body: NoteDraftPreviewRequest,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    registry: NoteTypeRegistry = Depends(get_registry),
    authorizer: NoteTypeAuthorizer = Depends(get_note_type_authorizer),
    generator: NoteGenerationService = Depends(get_note_generation_service),
    audit: AuditService = Depends(get_audit_service),
) -> NoteDraftPreviewResponse:
    """Draft a note of one type from a transcript and return it unsaved.

    For trying a type before relying on it: no note, session or patient is
    written. The transcript may be a real visit, so the call is audited — by
    the type's key, never the transcript or the draft.
    """
    definition = _resolve_preview_definition(body, registry, authorizer, user)
    if definition.restricted:
        raise BadRequestError(
            f"Note type {definition.key!r} is written by hand, not generated",
            {"note_type": definition.key},
        )
    try:
        inputs = validate_note_inputs(definition, body.inputs)
    except ValueError as exc:
        raise BadRequestError(
            str(exc), {"note_type": definition.key}, code="INVALID_NOTE_INPUTS"
        ) from exc

    audit.log(
        AuditAction.NOTE_TYPE_DRAFT_PREVIEWED,
        user,
        request,
        resource_type=ResourceType.NOTE_TYPE,
        resource_id=definition.key,
        changes={
            "source": "unsaved" if body.spec is not None else "saved",
            "version": definition.version,
        },
    )

    transcript = Transcript(format=body.transcript.format.value, content=body.transcript.content)
    # Release the pooled connection before the multi-second model call, the
    # same seam the standalone-note worker and SOAP import use.
    release_db_connection()
    try:
        generated = generator.generate_note(
            definition.key,
            transcript,
            _preview_patient(),
            utc_now(),
            inputs=inputs,
            definition=definition,
        )
    except TransientNoteGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Drafting is temporarily unavailable. Try again in a moment.",
        ) from exc
    except (ValueError, KeyError) as exc:
        logger.warning("Note draft preview failed for %s", definition.key)
        raise UnprocessableEntityError(
            "This note type could not produce a draft from that transcript.",
            {"note_type": definition.key},
        ) from exc

    return NoteDraftPreviewResponse(
        key=definition.key,
        version=None if body.spec is not None else definition.version,
        sections=generated.content,
    )


def _resolve_preview_definition(
    body: NoteDraftPreviewRequest,
    registry: NoteTypeRegistry,
    authorizer: NoteTypeAuthorizer,
    user: User,
) -> NoteTypeDefinition:
    if body.spec is not None:
        _check_base(body.spec, registry)
        return definition_for(PREVIEW_KEY, 0, body.spec, registry.base_for)
    key = body.key or ""
    try:
        definition = registry.get(key, body.version)
    except KeyError as exc:
        raise NotFoundError(f"Note type {key!r} not found") from exc
    if not authorizer.is_allowed(user, key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Note type {key!r} not allowed for this subscription",
        )
    return definition
