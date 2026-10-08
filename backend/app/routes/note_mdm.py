# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The medical decision making review beside a prescriber's note.

The clinician's three choices are stored as the note's inputs; the level and
codes are computed on every read, so they follow the note's latest edits and
the confirmed psychotherapy minutes.
"""

from fastapi import APIRouter, Depends, Request

from ..api_errors import BadRequestError, ConflictError, NotFoundError
from ..auth.service import require_baa_acceptance
from ..models import Note, User
from ..models.audit import AuditAction
from ..models.mdm_review import MdmChoicesRequest, MdmReviewResponse
from ..notes import NoteTypeDefinition, NoteTypeRegistry
from ..notes.mdm_review import (
    CHOICE_INPUTS,
    NEW_PATIENT_INPUT,
    MdmReview,
    confirmed_minutes,
    offers_review,
    review,
    with_visit_details,
)
from ..services import AuditService, NoteService, get_audit_service
from ..services.note_service import NoteNotFoundError
from .notes import get_note_service, get_registry

router = APIRouter(prefix="/api/notes", tags=["notes"])


def _note_with_review(
    note_id: str, user: User, note_service: NoteService, registry: NoteTypeRegistry
) -> tuple[Note, NoteTypeDefinition]:
    try:
        note = note_service.get_note(note_id, user.id)
    except NoteNotFoundError as exc:
        raise NotFoundError("Note not found", {"note_id": note_id}) from exc
    try:
        definition = registry.get(note.note_type, note.note_type_version)
    except KeyError:
        definition = None
    if definition is None or not offers_review(definition):
        raise NotFoundError("This note has no medical decision making", {"note_id": note_id})
    return note, definition


def _review(note: Note, definition: NoteTypeDefinition) -> MdmReview:
    return review(
        definition,
        note.note_inputs or {},
        note.content_edited or note.content,
        confirmed_minutes(note.psychotherapy_window),
    )


def _response(note: Note, definition: NoteTypeDefinition) -> MdmReviewResponse:
    return MdmReviewResponse.from_review(_review(note, definition))


@router.get("/{note_id}/mdm")
def get_mdm_review(
    note_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    note_service: NoteService = Depends(get_note_service),
    registry: NoteTypeRegistry = Depends(get_registry),
    audit: AuditService = Depends(get_audit_service),
) -> MdmReviewResponse:
    """The clinician's MDM choices, the drafted evidence, and the codes they give."""
    note, definition = _note_with_review(note_id, user, note_service, registry)
    audit.log_note_action(
        action=AuditAction.SESSION_VIEWED,
        user=user,
        request=request,
        note_id=note.id,
        patient_id=note.patient_id,
        session_id=note.session_id,
    )
    return _response(note, definition)


@router.put("/{note_id}/mdm")
def put_mdm_choices(
    note_id: str,
    body: MdmChoicesRequest,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    note_service: NoteService = Depends(get_note_service),
    registry: NoteTypeRegistry = Depends(get_registry),
    audit: AuditService = Depends(get_audit_service),
) -> MdmReviewResponse:
    """Record the clinician's MDM choices. Nothing is drafted again."""
    note, definition = _note_with_review(note_id, user, note_service, registry)
    chosen = {
        CHOICE_INPUTS["problems"]: body.problems,
        CHOICE_INPUTS["data"]: body.data,
        CHOICE_INPUTS["risk"]: body.risk,
        NEW_PATIENT_INPUT: body.new_patient,
    }
    options = {i.key: i.options for i in definition.inputs}
    for key, value in chosen.items():
        if value and value not in options.get(key, ()):
            raise BadRequestError(f"{value!r} is not an option for {key!r}", {"note_id": note_id})
    # Only the review's own inputs change; the rest stay exactly as they were.
    kept = {k: v for k, v in (note.note_inputs or {}).items() if k not in chosen}
    kept.update({k: v for k, v in chosen.items() if v})
    note = note_service.update_note_inputs(note.id, kept, user.id)
    audit.log_note_action(
        action=AuditAction.SESSION_UPDATED,
        user=user,
        request=request,
        note_id=note.id,
        patient_id=note.patient_id,
        session_id=note.session_id,
        changes={"changed_fields": ["note_inputs"]},
    )
    return _response(note, definition)


@router.post("/{note_id}/mdm/apply")
def apply_mdm_codes(
    note_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    note_service: NoteService = Depends(get_note_service),
    registry: NoteTypeRegistry = Depends(get_registry),
    audit: AuditService = Depends(get_audit_service),
) -> MdmReviewResponse:
    """Put the computed codes in the note's visit details, as the clinician's edit."""
    note, definition = _note_with_review(note_id, user, note_service, registry)
    applied = _review(note, definition).visit_details_with_codes
    if applied is None:
        raise ConflictError("The note already states these codes", {"note_id": note_id})
    content = with_visit_details(note.content_edited or note.content, applied)
    note = note_service.update_note_edits(note.id, content, user.id)
    audit.log_note_action(
        action=AuditAction.SESSION_UPDATED,
        user=user,
        request=request,
        note_id=note.id,
        patient_id=note.patient_id,
        session_id=note.session_id,
        changes={"changed_fields": ["content_edited"]},
    )
    return _response(note, definition)
