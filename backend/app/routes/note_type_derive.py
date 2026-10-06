# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Derive a note type from sample notes or a description.

``POST /api/note-types/derive`` proposes a definition and returns it
unsaved, with the evidence that it fits: which passages of each sample
found no field, what was changed so no sample text stays in the
definition, and, against a named reference, what the proposal lacks.
Saving is the ordinary ``PUT /api/note-types/custom/{slug}``.

Samples are a client's record. They are read from the request, used for
this call and dropped; the audit row records counts, never their text.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..api_errors import BadRequestError, NotFoundError, UnprocessableEntityError
from ..auth.route_access import subscription_exempt
from ..auth.service import get_current_user, require_baa_acceptance
from ..db import release_db_connection
from ..models.audit import AuditAction, ResourceType
from ..notes.practice_types import PracticeNoteTypeSpec  # noqa: TC001 — runtime Pydantic field
from ..notes.references import (
    NoteTypeReference,
    get_registered_note_type_reference,
    reference_from_definition,
    registered_note_type_references,
)
from ..services.ai_features import AIFeature
from ..services.audit_service import AuditService, get_audit_service
from ..services.hedged_structured_llm_gateway import generation_gateway
from ..services.http_structured_llm_gateway import HttpStructuredLLMGateway
from ..services.note_import_service import (
    MAX_IMPORT_DOC_BYTES,
    DocumentTextExtractionError,
    NoteImportService,
    UnsupportedDocumentTypeError,
    extract_document_text,
)
from ..services.note_type_derive_service import (
    MAX_DESCRIPTION_CHARS,
    MAX_SAMPLE_CHARS,
    MAX_SAMPLES,
    DeriveFailedError,
    NoteTypeDeriveService,
)
from ..services.structured_llm_gateway import StructuredOutputTruncatedError
from ..settings import get_settings
from .note_types import get_registry

if TYPE_CHECKING:
    from ..models import User
    from ..notes import NoteTypeRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/note-types", tags=["note-types"])


class CoverageSchema(BaseModel):
    sample: int = Field(description="Index of the sample: pasted samples first, then files.")
    passages: int = Field(description="How many passages the sample was split into.")
    unplaced: list[str] = Field(
        description="Passages of the sample that no proposed field took, verbatim."
    )
    checked: bool = Field(
        description="False when the sample could not be checked; unplaced is then empty."
    )
    excluded: int = Field(
        default=0,
        description=(
            "Lines set aside as not note content: blocks the sample marks as not part "
            "of the note (such as how codes were chosen), signature lines, and facts "
            "that only identify the client or clinician. Never counted as unplaced."
        ),
    )


class GuardFindingSchema(BaseModel):
    path: str = Field(description="Where in the proposal, e.g. 'sections[1].fields[0].ai_hint'.")
    outcome: str = Field(
        description=(
            "'rewritten' when general wording replaced text taken from a sample; "
            "'neutralized' when it still repeated one and was replaced with plain wording."
        )
    )


class SuggestionSchema(BaseModel):
    label: str
    description: str = ""


class ReferenceSchema(BaseModel):
    key: str
    label: str


class DeriveNoteTypeResponse(BaseModel):
    """A proposed note type and the checks run on it. Nothing is saved."""

    spec: PracticeNoteTypeSpec
    coverage: list[CoverageSchema] = Field(
        description="One entry per sample, in the order they were sent."
    )
    guard: list[GuardFindingSchema] = Field(
        description="Parts of the proposal changed because they repeated sample text."
    )
    reference: ReferenceSchema | None = None
    suggestions: list[SuggestionSchema] = Field(
        default_factory=list,
        description="Elements of the reference the proposal has no section or field for.",
    )


class ReferenceListResponse(BaseModel):
    references: list[ReferenceSchema]


def get_note_type_derive_service() -> NoteTypeDeriveService:
    """Under the end-to-end stack both calls go to its stand-in.

    The setting refuses to load outside development.
    """
    base_url = get_settings().note_generation_base_url
    if base_url:
        stand_in = HttpStructuredLLMGateway(base_url)
        return NoteTypeDeriveService(
            NoteImportService(llm_gateway=generation_gateway(AIFeature.NOTE_IMPORT, stand_in)),
            llm_gateway=generation_gateway(AIFeature.NOTE_TYPE_DERIVE, stand_in),
        )
    return NoteTypeDeriveService(NoteImportService())


@router.get("/derive/references", response_model=ReferenceListResponse)
def list_derive_references(
    _user: User = Depends(get_current_user),
    _: None = Depends(subscription_exempt),
) -> ReferenceListResponse:
    """References registered with this deployment.

    Any note type's key also serves as a reference; these are the ones that
    are not note types.
    """
    return ReferenceListResponse(
        references=[
            ReferenceSchema(key=r.key, label=r.label) for r in registered_note_type_references()
        ]
    )


def _resolve_reference(key: str | None, registry: NoteTypeRegistry) -> NoteTypeReference | None:
    if not key:
        return None
    registered = get_registered_note_type_reference(key)
    if registered is not None:
        return registered
    try:
        return reference_from_definition(registry.get(key))
    except KeyError as exc:
        raise NotFoundError(f"Reference {key!r} not found") from exc


async def _sample_texts(pasted: list[str], files: list[UploadFile]) -> list[str]:
    """Each sample's text, pasted ones first; PDF and Word read like an import."""
    texts = [text.strip() for text in pasted if text.strip()]
    for upload in files:
        data = await upload.read()
        if len(data) > MAX_IMPORT_DOC_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File too large. Max {MAX_IMPORT_DOC_BYTES // (1024 * 1024)} MB.",
            )
        try:
            texts.append(
                extract_document_text(
                    data, content_type=upload.content_type, filename=upload.filename
                )
            )
        except UnsupportedDocumentTypeError as exc:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
            ) from exc
        except DocumentTextExtractionError as exc:
            raise UnprocessableEntityError(str(exc)) from exc
    if len(texts) > MAX_SAMPLES:
        raise BadRequestError(f"Send at most {MAX_SAMPLES} sample notes.")
    if any(len(text) > MAX_SAMPLE_CHARS for text in texts):
        raise BadRequestError(
            f"A sample note is longer than {MAX_SAMPLE_CHARS} characters; send one note per sample."
        )
    return texts


@router.post("/derive", response_model=DeriveNoteTypeResponse)
async def derive_note_type(
    request: Request,
    samples: Annotated[list[str] | None, Form()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
    description: Annotated[str | None, Form(max_length=MAX_DESCRIPTION_CHARS)] = None,
    reference: Annotated[str | None, Form()] = None,
    user: User = Depends(require_baa_acceptance),
    registry: NoteTypeRegistry = Depends(get_registry),
    deriver: NoteTypeDeriveService = Depends(get_note_type_derive_service),
    audit: AuditService = Depends(get_audit_service),
) -> DeriveNoteTypeResponse:
    """Propose a note type from up to three sample notes and/or a description.

    Multipart form: ``samples`` (pasted note text, repeatable), ``files``
    (PDF, Word or text, repeatable), ``description`` (how the notes are laid
    out, in plain words) and ``reference`` (a note type or reference key to
    compare against). Send at least one sample or a description.
    """
    texts = await _sample_texts(samples or [], files or [])
    if not texts and not (description and description.strip()):
        raise BadRequestError("Send a sample note or a description.")
    chosen = _resolve_reference(reference, registry)

    audit.log(
        AuditAction.NOTE_TYPE_DERIVED,
        user,
        request,
        resource_type=ResourceType.NOTE_TYPE,
        resource_id="derive",
        changes={
            "pasted_samples": len([s for s in samples or [] if s.strip()]),
            "file_samples": len(files or []),
            "description": bool(description and description.strip()),
            "reference": chosen.key if chosen else None,
        },
    )

    # The model calls take tens of seconds; hold no pooled connection through
    # them, the same seam the preview and the note import use.
    release_db_connection()
    try:
        derived = await run_in_threadpool(deriver.derive, texts, description, chosen)
    except (DeriveFailedError, StructuredOutputTruncatedError) as exc:
        raise UnprocessableEntityError(
            "A note type could not be proposed from these samples. Try again, or add a description."
        ) from exc
    except (ValueError, RuntimeError) as exc:
        logger.warning("Note type derive failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Proposing a note type is temporarily unavailable. Try again in a moment.",
        ) from exc

    return DeriveNoteTypeResponse(
        spec=derived.spec,
        coverage=[
            CoverageSchema(
                sample=c.sample,
                passages=c.passages,
                unplaced=c.unplaced,
                checked=c.checked,
                excluded=c.excluded,
            )
            for c in derived.coverage
        ],
        guard=[GuardFindingSchema(path=g.path, outcome=g.outcome) for g in derived.guard],
        reference=ReferenceSchema(key=chosen.key, label=chosen.label) if chosen else None,
        suggestions=[
            SuggestionSchema(label=s.label, description=s.description) for s in derived.suggestions
        ],
    )
