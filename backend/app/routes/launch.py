# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Companion launch-intent handoff endpoints.

The web dashboard's "Start Session" button hands off to the desktop
companion through a domain-verified deep link. To keep a stable
PHI-adjacent pointer (the appointment id) out of the URL — which is
visible to the OS, browser history, and any app that claims the
fallback scheme — the handoff is indirected through a single-use
``intent_id``:

1. The authenticated web session POSTs ``/launch/intent {appointment_id}``;
   the backend returns an opaque, 180s, single-use ``intent_id`` bound
   to ``(user_id, appointment_id)``. Only the SHA-256 hash is stored.
2. The companion receives ``https://<host>/launch/<intent_id>`` via a
   Universal Link / App URI Handler and POSTs ``/launch/redeem
   {intent_id}`` with its existing Firebase bearer token. The backend
   atomically consumes the intent, verifies the redeeming token belongs
   to the same user, and returns the appointment's
   ``{appointment_id, patient_name, video_url, session_id}``.

Both endpoints are mounted only when ``ENABLE_LAUNCH_INTENT`` is true;
otherwise the router is not registered and the paths return 404.

See ``docs/design/companion-thin-client.md``.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..models import User
from ..models.audit import AuditAction, ResourceType
from ..repositories import PatientRepository
from ..repositories import get_appointment_repository as _appt_repo_factory
from ..repositories import get_patient_repository as _patient_repo_factory
from ..scheduling_engine.repositories.appointment import AppointmentRepository
from ..services import AuditService, get_audit_service
from ..services.launch_intent_store import (
    LAUNCH_INTENT_TTL_SECONDS,
    create_launch_intent,
    redeem_launch_intent,
)
from ..settings import get_settings

logger = logging.getLogger(__name__)

# No prefix — every decorator carries the full /api/launch/... path
# (matches the sessions.py / scheduling.py convention).
router = APIRouter(tags=["launch"])


def get_appointment_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> AppointmentRepository:
    """Appointment repository scoped to the caller's tenant database."""
    return _appt_repo_factory()


def get_patient_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientRepository:
    """Patient repository scoped to the caller's tenant database."""
    return _patient_repo_factory()


class CreateLaunchIntentRequest(BaseModel):
    appointment_id: str = Field(min_length=1)


class CreateLaunchIntentResponse(BaseModel):
    intent_id: str
    launch_url: str
    expires_in: int


class RedeemLaunchIntentRequest(BaseModel):
    intent_id: str = Field(min_length=1)


class RedeemLaunchIntentResponse(BaseModel):
    appointment_id: str
    patient_name: str | None
    video_url: str | None
    session_id: str | None


@router.post(
    "/api/launch/intent",
    response_model=CreateLaunchIntentResponse,
)
def create_intent(
    request: CreateLaunchIntentRequest,
    user: User = Depends(require_baa_acceptance),
    appt_repo: AppointmentRepository = Depends(get_appointment_repository),
) -> CreateLaunchIntentResponse:
    """Issue a single-use launch intent for one of the caller's appointments.

    The appointment must belong to the caller's tenant — resolved inside
    the tenant-scoped session via ``appointment_repo.get(...)``. No audit
    event is emitted here: no PHI is disclosed yet (the appointment id is
    a pointer the caller already holds). The disclosure happens at redeem.
    """
    appointment = appt_repo.get(request.appointment_id, user_id=user.id)
    if appointment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found.",
        )

    intent_id = create_launch_intent(user_id=user.id, appointment_id=appointment.id)
    settings = get_settings()
    launch_url = f"{settings.app_url}/launch/{intent_id}"
    return CreateLaunchIntentResponse(
        intent_id=intent_id,
        launch_url=launch_url,
        expires_in=LAUNCH_INTENT_TTL_SECONDS,
    )


# Single generic terminal failure for a well-formed redeem request.
# Collapses unknown / expired / already-consumed / wrong-user into one
# 410 so the endpoint is not an existence (or ownership) oracle.
_INTENT_INVALID = HTTPException(
    status_code=status.HTTP_410_GONE,
    detail="Launch intent is no longer valid.",
)


@router.post(
    "/api/launch/redeem",
    response_model=RedeemLaunchIntentResponse,
)
def redeem_intent(
    request: RedeemLaunchIntentRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    appt_repo: AppointmentRepository = Depends(get_appointment_repository),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> RedeemLaunchIntentResponse:
    """Redeem a launch intent and disclose the appointment to the companion.

    Single-use and atomic: the store claim consumes the intent before any
    further check, so a wrong-user redeem still burns the intent (by
    design). Unknown, expired, already-consumed, and wrong-user all return
    the same generic 410 — no oracle.
    """
    redeemed = redeem_launch_intent(request.intent_id)
    if redeemed is None:
        raise _INTENT_INVALID

    # Same-user binding: the redeeming token must belong to the user the
    # intent was issued to. Mismatch is indistinguishable from "not found"
    # (same 410); the intent has already been consumed above.
    if redeemed.user_id != user.id:
        logger.warning("launch_intent_redeem_wrong_user")
        raise _INTENT_INVALID

    appointment = appt_repo.get(redeemed.appointment_id, user_id=user.id)
    if appointment is None:
        # The intent was valid but the appointment is gone (cancelled /
        # purged between issue and redeem). Treat as no-longer-valid.
        raise _INTENT_INVALID

    patient = patient_repo.get(appointment.patient_id, user_id=user.id)
    patient_name = patient.display_name if patient is not None else None

    # Record-level audit: a patient name is disclosed. The patient
    # association rides the ``patient=`` argument (handled by the audit
    # service's PHI guard); ``changes`` carries names-only metadata and
    # never patient_name / video_url / the raw intent_id.
    audit.log(
        AuditAction.LAUNCH_INTENT_REDEEMED,
        user,
        http_request,
        resource_type=ResourceType.APPOINTMENT,
        resource_id=appointment.id,
        patient=patient,
        changes={"changed_fields": ["session_started_from_appointment"]},
    )

    return RedeemLaunchIntentResponse(
        appointment_id=appointment.id,
        patient_name=patient_name,
        video_url=appointment.video_link,
        session_id=appointment.session_id,
    )
