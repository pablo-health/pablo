# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Scheduling API routes — thin HTTP handlers for appointment and availability CRUD."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth.service import TenantContext, get_tenant_context
from ..database import get_tenant_firestore_client
from ..models.scheduling import (
    AppointmentListResponse,
    AppointmentResponse,
    AvailabilityRuleListResponse,
    AvailabilityRuleResponse,
    CheckConflictsRequest,
    CheckConflictsResponse,
    ConflictResponse,
    CreateAppointmentRequest,
    CreateAvailabilityRuleRequest,
    CreateRecurringAppointmentRequest,
    EditSeriesRequest,
    FreeSlotsResponse,
    GoogleCalendarAuthResponse,
    GoogleCalendarStatusResponse,
    TimeSlotResponse,
    UpdateAppointmentRequest,
    UpdateAvailabilityRuleRequest,
)
from ..repositories.appointment import FirestoreAppointmentRepository
from ..repositories.availability_rule import FirestoreAvailabilityRuleRepository
from ..repositories.google_calendar_token import GoogleCalendarTokenRepository
from ..scheduling_engine.exceptions import (
    AppointmentNotFoundError,
    InvalidAppointmentError,
    InvalidRecurrenceError,
)
from ..scheduling_engine.models.availability import AvailabilityRule, EnforcementLevel, RuleType
from ..scheduling_engine.services.availability import AvailabilityEngine
from ..scheduling_engine.services.scheduling import SchedulingService
from ..services.google_calendar_service import GoogleCalendarService
from ..settings import get_settings

if TYPE_CHECKING:
    from ..scheduling_engine.models.appointment import Appointment
    from ..scheduling_engine.repositories.appointment import AppointmentRepository
    from ..scheduling_engine.repositories.availability_rule import AvailabilityRuleRepository

logger = logging.getLogger(__name__)

router = APIRouter(tags=["scheduling"])


def get_appointment_repository(
    ctx: TenantContext = Depends(get_tenant_context),
) -> AppointmentRepository:
    """Get appointment repository scoped to the tenant's database."""
    db = get_tenant_firestore_client(ctx.firestore_db)
    return FirestoreAppointmentRepository(db)


def get_availability_rule_repository(
    ctx: TenantContext = Depends(get_tenant_context),
) -> AvailabilityRuleRepository:
    """Get availability rule repository scoped to the tenant's database."""
    db = get_tenant_firestore_client(ctx.firestore_db)
    return FirestoreAvailabilityRuleRepository(db)


def get_scheduling_service(
    repo: AppointmentRepository = Depends(get_appointment_repository),
) -> SchedulingService:
    """Get scheduling service with injected repository."""
    return SchedulingService(repo)


def get_availability_engine(
    rule_repo: AvailabilityRuleRepository = Depends(get_availability_rule_repository),
    appt_repo: AppointmentRepository = Depends(get_appointment_repository),
) -> AvailabilityEngine:
    """Get availability engine with injected repositories."""
    return AvailabilityEngine(rule_repo, appt_repo)


def _to_response(appt: Appointment) -> AppointmentResponse:
    return AppointmentResponse(
        id=appt.id,
        user_id=appt.user_id,
        patient_id=appt.patient_id,
        title=appt.title,
        start_at=appt.start_at,
        end_at=appt.end_at,
        duration_minutes=appt.duration_minutes,
        status=appt.status,
        session_type=appt.session_type,
        video_link=appt.video_link,
        video_platform=appt.video_platform,
        notes=appt.notes,
        recurrence_rule=appt.recurrence_rule,
        recurring_appointment_id=appt.recurring_appointment_id,
        recurrence_index=appt.recurrence_index,
        is_exception=appt.is_exception,
        google_event_id=appt.google_event_id,
        google_sync_status=appt.google_sync_status,
        session_id=appt.session_id,
        created_at=appt.created_at,
        updated_at=appt.updated_at,
    )


@router.post(
    "/api/appointments",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_appointment(
    request: CreateAppointmentRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    service: SchedulingService = Depends(get_scheduling_service),
) -> AppointmentResponse:
    """Create a new appointment."""
    try:
        appt = service.create_appointment(
            ctx.user_id,
            data=request.model_dump(),
        )
    except InvalidAppointmentError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return _to_response(appt)


@router.get("/api/appointments", response_model=AppointmentListResponse)
def list_appointments(
    start: str = Query(..., description="Range start (ISO 8601)"),
    end: str = Query(..., description="Range end (ISO 8601)"),
    ctx: TenantContext = Depends(get_tenant_context),
    service: SchedulingService = Depends(get_scheduling_service),
) -> AppointmentListResponse:
    """List appointments in a date range."""
    appointments = service.list_appointments(ctx.user_id, start, end)
    return AppointmentListResponse(
        data=[_to_response(a) for a in appointments],
        total=len(appointments),
    )


@router.get("/api/appointments/{appointment_id}", response_model=AppointmentResponse)
def get_appointment(
    appointment_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    service: SchedulingService = Depends(get_scheduling_service),
) -> AppointmentResponse:
    """Get a single appointment."""
    try:
        appt = service.get_appointment(appointment_id, ctx.user_id)
    except AppointmentNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_response(appt)


@router.patch("/api/appointments/{appointment_id}", response_model=AppointmentResponse)
def update_appointment(
    appointment_id: str,
    request: UpdateAppointmentRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    service: SchedulingService = Depends(get_scheduling_service),
) -> AppointmentResponse:
    """Update an appointment."""
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    try:
        appt = service.update_appointment(appointment_id, ctx.user_id, **updates)
    except AppointmentNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except InvalidAppointmentError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return _to_response(appt)


@router.delete(
    "/api/appointments/{appointment_id}",
    response_model=AppointmentResponse,
)
def cancel_appointment(
    appointment_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    service: SchedulingService = Depends(get_scheduling_service),
) -> AppointmentResponse:
    """Cancel an appointment (soft delete — sets status to cancelled)."""
    try:
        appt = service.cancel_appointment(appointment_id, ctx.user_id)
    except AppointmentNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_response(appt)


# --- Recurring appointment endpoints ---


@router.post(
    "/api/appointments/recurring",
    response_model=AppointmentListResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_recurring_appointment(
    request: CreateRecurringAppointmentRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    service: SchedulingService = Depends(get_scheduling_service),
) -> AppointmentListResponse:
    """Create a recurring appointment series."""
    try:
        appointments = service.create_recurring(
            ctx.user_id,
            data=request.model_dump(exclude={"frequency", "timezone", "end_date", "count"}),
            recurrence={
                "frequency": request.frequency,
                "timezone": request.timezone,
                "end_date": request.end_date,
                "count": request.count,
            },
        )
    except (InvalidAppointmentError, InvalidRecurrenceError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return AppointmentListResponse(
        data=[_to_response(a) for a in appointments],
        total=len(appointments),
    )


@router.post(
    "/api/appointments/{appointment_id}/edit-series",
    response_model=AppointmentListResponse,
)
def edit_series(
    appointment_id: str,
    request: EditSeriesRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    service: SchedulingService = Depends(get_scheduling_service),
) -> AppointmentListResponse:
    """Edit all future occurrences in a recurring series."""
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    try:
        appointments = service.edit_future_occurrences(appointment_id, ctx.user_id, **updates)
    except AppointmentNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except InvalidAppointmentError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return AppointmentListResponse(
        data=[_to_response(a) for a in appointments],
        total=len(appointments),
    )


@router.delete(
    "/api/appointments/{appointment_id}/cancel-series",
    response_model=AppointmentListResponse,
)
def cancel_series(
    appointment_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    service: SchedulingService = Depends(get_scheduling_service),
) -> AppointmentListResponse:
    """Cancel all future occurrences in a recurring series."""
    try:
        appointments = service.cancel_future_occurrences(appointment_id, ctx.user_id)
    except AppointmentNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except InvalidAppointmentError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return AppointmentListResponse(
        data=[_to_response(a) for a in appointments],
        total=len(appointments),
    )


# --- Availability endpoints ---


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _rule_to_response(rule: AvailabilityRule) -> AvailabilityRuleResponse:
    return AvailabilityRuleResponse(
        id=rule.id,
        user_id=rule.user_id,
        rule_type=rule.rule_type,
        enforcement=rule.enforcement,
        params=rule.params,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


@router.get("/api/availability/slots", response_model=FreeSlotsResponse)
def get_free_slots(
    date: str = Query(..., description="Date (YYYY-MM-DD)"),
    duration: int = Query(50, description="Slot duration in minutes", ge=1, le=480),
    ctx: TenantContext = Depends(get_tenant_context),
    engine: AvailabilityEngine = Depends(get_availability_engine),
) -> FreeSlotsResponse:
    """Get available time slots for a given date."""
    slots = engine.get_free_slots(ctx.user_id, date, duration)
    return FreeSlotsResponse(
        date=date,
        duration_minutes=duration,
        slots=[TimeSlotResponse(start=s.start, end=s.end) for s in slots],
        total=len(slots),
    )


@router.post("/api/availability/check", response_model=CheckConflictsResponse)
def check_conflicts(
    request: CheckConflictsRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    engine: AvailabilityEngine = Depends(get_availability_engine),
) -> CheckConflictsResponse:
    """Check scheduling conflicts for a proposed time."""
    conflicts = engine.check_conflicts(ctx.user_id, request.start_at, request.end_at)
    conflict_responses = [
        ConflictResponse(
            rule_type=c.rule.rule_type,
            enforcement=c.enforcement,
            message=c.message,
        )
        for c in conflicts
    ]
    has_hard = any(c.enforcement == EnforcementLevel.HARD for c in conflicts)
    return CheckConflictsResponse(
        conflicts=conflict_responses,
        has_hard_conflicts=has_hard,
    )


@router.get("/api/availability/rules", response_model=AvailabilityRuleListResponse)
def list_availability_rules(
    ctx: TenantContext = Depends(get_tenant_context),
    rule_repo: AvailabilityRuleRepository = Depends(get_availability_rule_repository),
) -> AvailabilityRuleListResponse:
    """List all availability rules for the current user."""
    rules = rule_repo.list_by_user(ctx.user_id)
    return AvailabilityRuleListResponse(
        data=[_rule_to_response(r) for r in rules],
        total=len(rules),
    )


@router.post(
    "/api/availability/rules",
    response_model=AvailabilityRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_availability_rule(
    request: CreateAvailabilityRuleRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    rule_repo: AvailabilityRuleRepository = Depends(get_availability_rule_repository),
) -> AvailabilityRuleResponse:
    """Create a new availability rule."""
    try:
        RuleType(request.rule_type)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid rule_type: {request.rule_type}",
        ) from e

    try:
        EnforcementLevel(request.enforcement)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid enforcement: {request.enforcement}",
        ) from e

    now = _now()
    rule = AvailabilityRule(
        id=str(uuid.uuid4()),
        user_id=ctx.user_id,
        rule_type=request.rule_type,
        enforcement=request.enforcement,
        params=request.params,
        created_at=now,
        updated_at=now,
    )
    created = rule_repo.create(rule)
    return _rule_to_response(created)


@router.patch(
    "/api/availability/rules/{rule_id}",
    response_model=AvailabilityRuleResponse,
)
def update_availability_rule(
    rule_id: str,
    request: UpdateAvailabilityRuleRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    rule_repo: AvailabilityRuleRepository = Depends(get_availability_rule_repository),
) -> AvailabilityRuleResponse:
    """Update an existing availability rule."""
    rule = rule_repo.get(rule_id, ctx.user_id)
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule not found: {rule_id}",
        )

    if request.rule_type is not None:
        try:
            RuleType(request.rule_type)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid rule_type: {request.rule_type}",
            ) from e
        rule.rule_type = request.rule_type

    if request.enforcement is not None:
        try:
            EnforcementLevel(request.enforcement)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid enforcement: {request.enforcement}",
            ) from e
        rule.enforcement = request.enforcement

    if request.params is not None:
        rule.params = request.params

    rule.updated_at = _now()
    updated = rule_repo.update(rule)
    return _rule_to_response(updated)


@router.delete(
    "/api/availability/rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_availability_rule(
    rule_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    rule_repo: AvailabilityRuleRepository = Depends(get_availability_rule_repository),
) -> None:
    """Delete an availability rule."""
    deleted = rule_repo.delete(rule_id, ctx.user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule not found: {rule_id}",
        )


# --- Google Calendar endpoints ---


def get_google_calendar_service(
    ctx: TenantContext = Depends(get_tenant_context),
) -> GoogleCalendarService:
    """Get Google Calendar service with injected dependencies."""
    db = get_tenant_firestore_client(ctx.firestore_db)
    token_repo = GoogleCalendarTokenRepository(db)
    appt_repo = FirestoreAppointmentRepository(db)
    settings = get_settings()
    return GoogleCalendarService(
        token_repo=token_repo,
        appointment_repo=appt_repo,
        client_id=settings.google_calendar_client_id,
        client_secret=settings.google_calendar_client_secret.get_secret_value(),
    )


@router.get(
    "/api/google-calendar/authorize",
    response_model=GoogleCalendarAuthResponse,
)
def google_calendar_authorize(
    redirect_uri: str = Query(..., description="OAuth redirect URI"),
    ctx: TenantContext = Depends(get_tenant_context),
    service: GoogleCalendarService = Depends(get_google_calendar_service),
) -> GoogleCalendarAuthResponse:
    """Get Google OAuth authorization URL to connect calendar."""
    auth_url = service.get_auth_url(ctx.user_id, redirect_uri)
    return GoogleCalendarAuthResponse(auth_url=auth_url)


@router.get("/api/google-calendar/callback")
def google_calendar_callback(
    code: str = Query(..., description="OAuth authorization code"),
    redirect_uri: str = Query(..., description="OAuth redirect URI"),
    ctx: TenantContext = Depends(get_tenant_context),
    service: GoogleCalendarService = Depends(get_google_calendar_service),
) -> dict[str, str]:
    """Handle Google OAuth callback — exchange code for tokens."""
    try:
        service.handle_callback(ctx.user_id, code, redirect_uri)
    except Exception as e:
        logger.exception("Google Calendar OAuth callback failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth callback failed: {e}",
        ) from e
    return {"status": "connected"}


@router.delete("/api/google-calendar/disconnect")
def google_calendar_disconnect(
    ctx: TenantContext = Depends(get_tenant_context),
    service: GoogleCalendarService = Depends(get_google_calendar_service),
) -> dict[str, str]:
    """Disconnect Google Calendar and remove stored tokens."""
    deleted = service.disconnect(ctx.user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google Calendar not connected",
        )
    return {"status": "disconnected"}


@router.get(
    "/api/google-calendar/status",
    response_model=GoogleCalendarStatusResponse,
)
def google_calendar_status(
    ctx: TenantContext = Depends(get_tenant_context),
    service: GoogleCalendarService = Depends(get_google_calendar_service),
) -> GoogleCalendarStatusResponse:
    """Check Google Calendar connection status."""
    status_info = service.get_sync_status(ctx.user_id)
    return GoogleCalendarStatusResponse(**status_info)
