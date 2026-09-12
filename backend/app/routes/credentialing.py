# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The credentialing checklist surface — what to ask, and what came back.

One GET describes the whole sitting: which questions apply to this clinician,
which already have an answer on file, and how far each tier has got. The wizard
renders that rather than holding its own copy of the question set, so the
branching the API enforces and the branching the screen shows cannot disagree.

Progress is reported per tier and there is no overall figure, deliberately. A
clinician who completes Tier 1 and stops has finished — every field in that
tier is one the billing pipeline needs regardless of whether she ever applies
to a panel — and a single percentage would render that as half done.

All routes are user-scoped: a clinician reads and writes only her own record.
The encrypted identifiers are not among them; those go through
``app.credentialing.government_ids``, which audits every read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..api_errors import BadRequestError, NotFoundError
from ..auth.route_access import subscription_exempt
from ..auth.service import get_current_user, get_tenant_context
from ..credentialing import checklist, confirmations, government_ids, status
from ..db import get_db_session
from ..db.models import CREDENTIAL_CONFIRMATION_SOURCES, ClinicianProfileRow
from ..models import User
from ..services.audit_service import AuditService, get_audit_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..db.models import CredentialConfirmationRow

# The credential tables live in the tenant schema with RLS keyed on
# ``app.current_user_id``, so the GUC is armed at router level before any
# handler reaches the record — the same shape supervision.py and compliance.py
# use.
router = APIRouter(
    prefix="/api/credentialing",
    tags=["credentialing"],
    dependencies=[Depends(get_tenant_context)],
)

DbSession = Annotated["Session", Depends(get_db_session)]
UserDep = Annotated[User, Depends(get_current_user)]
AuditDep = Annotated[AuditService, Depends(get_audit_service)]
SubscriptionExemptDep = Annotated[None, Depends(subscription_exempt)]


class ChecklistFieldResponse(BaseModel):
    """One question, with everything the surface needs to render it."""

    key: str
    label: str
    section: str
    tier: str
    kind: str
    required: bool
    applies_to: str
    source: str | None
    help_text: str | None
    choices: list[str]
    #: Whether the record already holds an answer. Tier 0 renders a value with
    #: a confirm control either way; the other tiers use it to show what is
    #: left without implying anything is overdue.
    answered: bool
    #: What the record holds for this field right now, rendered for display.
    #: Tier 0 only — the tier that asks her to agree with a value rather than
    #: to type one. ``None`` means nothing is on file, which the card says out
    #: loud rather than showing an empty box as though it were an answer.
    current_value: str | None = None


class TierProgressResponse(BaseModel):
    tier: str
    answered: int
    required: int
    complete: bool


class ChecklistResponse(BaseModel):
    """The whole sitting."""

    supervised: bool
    prescriber: bool
    #: True once every question the billing pipeline needs has an answer. The
    #: honest finish line for a clinician who may never credential.
    claims_ready: bool
    progress: list[TierProgressResponse]
    fields: list[ChecklistFieldResponse]


class ConfirmationPayload(BaseModel):
    """Her answer to one pre-filled value."""

    source: str = Field(min_length=1, max_length=32)
    confirmed: bool
    presented_value: str | None = Field(default=None, max_length=2000)
    #: Required when ``confirmed`` is false: a value marked wrong is only
    #: useful with the right one beside it.
    correction: str | None = Field(default=None, max_length=2000)


class ConfirmationResponse(BaseModel):
    field_key: str
    source: str
    presented_value: str | None
    confirmed: bool
    correction: str | None
    confirmed_at: str


class ChecklistAnswersPayload(BaseModel):
    """The scalar answers that land on the clinician's identifier row.

    Only the unencrypted ones. SSN, date of birth and tax id are written
    through ``government_ids`` by a route that audits them, and are not
    accepted here.
    """

    supervision_status: str | None = Field(default=None, max_length=16)
    caqh_id: str | None = Field(default=None, max_length=32)
    business_structure: str | None = Field(default=None, max_length=40)
    sole_proprietor: bool | None = None
    type2_npi: str | None = Field(default=None, max_length=20)
    medicare_intent: bool | None = None
    medicaid_intent: bool | None = None


def _field_to_response(
    field: checklist.ChecklistField, *, answered: bool, current_value: str | None
) -> ChecklistFieldResponse:
    return ChecklistFieldResponse(
        key=field.key,
        label=field.label,
        section=field.section.value,
        tier=field.tier.value,
        kind=field.kind.value,
        required=field.required,
        applies_to=field.applies_to.value,
        source=field.source,
        help_text=field.help_text,
        choices=list(field.choices),
        answered=answered,
        current_value=current_value,
    )


def _confirmation_to_response(row: CredentialConfirmationRow) -> ConfirmationResponse:
    return ConfirmationResponse(
        field_key=row.field_key,
        source=row.source,
        presented_value=row.presented_value,
        confirmed=row.confirmed,
        correction=row.correction,
        confirmed_at=row.confirmed_at.isoformat(),
    )


def _who(
    session: Session, user_id: str, *, supervised: bool | None, prescriber: bool | None
) -> tuple[bool, bool]:
    """Which branch of the question set this clinician is on.

    Both are derived from the record and both can be overridden by the caller,
    because the wizard asks the fork before there is anything to derive it
    from: the answer to "are you independently licensed?" has to narrow the
    same page it was given on.
    """
    summary = government_ids.load_summary(session, user_id)
    if supervised is None:
        supervised = summary["supervision_status"] == "supervised"
    if prescriber is None:
        profile = session.get(ClinicianProfileRow, user_id)
        prescriber = bool(profile and profile.dea_number)
    return supervised, prescriber


@router.get("/checklist", response_model=ChecklistResponse)
def get_checklist(
    user: UserDep,
    session: DbSession,
    _exempt: SubscriptionExemptDep,
    supervised: bool | None = None,
    prescriber: bool | None = None,
) -> ChecklistResponse:
    """The question set for this clinician, with what she has already answered."""
    is_supervised, is_prescriber = _who(
        session, user.id, supervised=supervised, prescriber=prescriber
    )
    answered = status.answered_keys(session, user.id)
    values = status.current_values(session, user.id)
    fields = checklist.applicable(
        checklist.CHECKLIST_FIELDS, supervised=is_supervised, prescriber=is_prescriber
    )
    progress = checklist.completion(answered, supervised=is_supervised, prescriber=is_prescriber)
    return ChecklistResponse(
        supervised=is_supervised,
        prescriber=is_prescriber,
        claims_ready=checklist.claims_ready(
            answered, supervised=is_supervised, prescriber=is_prescriber
        ),
        progress=[
            TierProgressResponse(
                tier=p.tier.value,
                answered=p.answered,
                required=p.required,
                complete=p.complete,
            )
            for p in progress
        ],
        fields=[
            _field_to_response(f, answered=f.key in answered, current_value=values.get(f.key))
            for f in fields
        ],
    )


@router.get("/checklist/confirmations", response_model=list[ConfirmationResponse])
def list_confirmations(
    user: UserDep,
    session: DbSession,
    _exempt: SubscriptionExemptDep,
) -> list[ConfirmationResponse]:
    """Every Tier-0 value she has confirmed or corrected."""
    return [_confirmation_to_response(r) for r in confirmations.list_for(session, user.id)]


@router.put("/checklist/confirmations/{field_key}", response_model=ConfirmationResponse)
def record_confirmation(
    field_key: str,
    payload: ConfirmationPayload,
    user: UserDep,
    session: DbSession,
    _exempt: SubscriptionExemptDep,
) -> ConfirmationResponse:
    """Record that she looked at a pre-filled value and said yes or no.

    Idempotent per field: confirming again replaces the earlier answer, because
    the question is "is this right now" rather than a history of what she was
    shown.
    """
    field = next((f for f in checklist.CHECKLIST_FIELDS if f.key == field_key), None)
    if field is None or field.tier is not checklist.Tier.CONFIRM:
        raise NotFoundError(f"No Tier-0 field named {field_key!r}")

    try:
        row = confirmations.record(
            session,
            user.id,
            field_key=field_key,
            source=payload.source,
            confirmed=payload.confirmed,
            presented_value=payload.presented_value,
            correction=payload.correction,
        )
    except (confirmations.CorrectionRequiredError, confirmations.UnknownSourceError) as exc:
        raise BadRequestError(str(exc)) from exc

    session.commit()
    return _confirmation_to_response(row)


@router.patch("/checklist/answers")
def save_checklist_answers(
    payload: ChecklistAnswersPayload,
    request: Request,
    user: UserDep,
    session: DbSession,
    audit: AuditDep,
    _exempt: SubscriptionExemptDep,
) -> dict[str, object]:
    """Save the scalar answers, leaving anything unmentioned alone.

    Partial by design: the checklist is meant to be left and resumed, so a request
    carrying one field must not blank the rest. Writes go through
    ``government_ids`` rather than onto the row, because that row has one
    writer and a second would race it.
    """
    patch = payload.model_dump(exclude_unset=True)
    if not patch:
        raise BadRequestError("No answers in the request.")

    summary = government_ids.update_identifiers(session, user, audit, patch, request=request)
    session.commit()
    return summary


@router.get("/checklist/sources", response_model=list[str])
def list_confirmation_sources(_exempt: SubscriptionExemptDep) -> list[str]:
    """The provenance vocabulary, so the surface never invents a label."""
    return list(CREDENTIAL_CONFIRMATION_SOURCES)
