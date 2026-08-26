# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Scheduling API routes — thin HTTP handlers for appointment and availability CRUD."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status

from ..api_errors import BadRequestError, ConflictError, NotFoundError
from ..auth.service import (
    TenantContext,
    get_tenant_context,
    require_active_subscription,
    require_baa_acceptance,
)
from ..db import release_db_connection
from ..models import (
    AuditAction,
    ScheduleSessionRequest,
    SessionResponse,
    User,
)
from ..models.enums import SessionSource, SessionType, VideoPlatform
from ..models.scheduling import (
    AppointmentListResponse,
    AppointmentResponse,
    AppointmentTypeListResponse,
    AppointmentTypeResponse,
    AvailabilityRuleListResponse,
    AvailabilityRuleResponse,
    CheckConflictsRequest,
    CheckConflictsResponse,
    ConflictResponse,
    CreateAppointmentRequest,
    CreateAppointmentTypeRequest,
    CreateAvailabilityRuleRequest,
    CreateRecurringAppointmentRequest,
    EditSeriesRequest,
    FreeSlotsResponse,
    GoogleCalendarAuthResponse,
    GoogleCalendarStatusResponse,
    ParseAvailabilityRulesRequest,
    ParseAvailabilityRulesResponse,
    ProposedAvailabilityRule,
    StartSessionFromAppointmentRequest,
    TimeSlotResponse,
    UpdateAppointmentRequest,
    UpdateAppointmentTypeRequest,
    UpdateAvailabilityRuleRequest,
)
from ..notes import NoteTypeAuthorizer, get_note_type_authorizer
from ..rate_limit import get_availability_parse_limiter
from ..repositories import (
    NotesRepository,
    PatientRepository,
    TherapySessionRepository,
)
from ..repositories import (
    get_appointment_repository as _appt_repo_factory,
)
from ..repositories import (
    get_appointment_type_repository as _appt_type_repo_factory,
)
from ..repositories import (
    get_availability_rule_repository as _rule_repo_factory,
)
from ..repositories import (
    get_google_calendar_token_repository as _gcal_token_repo_factory,
)
from ..repositories import (
    get_notes_repository as _notes_repo_factory,
)
from ..repositories import (
    get_patient_repository as _patient_repo_factory,
)
from ..repositories import (
    get_session_repository as _session_repo_factory,
)
from ..scheduling_engine.exceptions import (
    AppointmentNotFoundError,
    InvalidAppointmentError,
    InvalidRecurrenceError,
)
from ..scheduling_engine.models.appointment_type import AppointmentType
from ..scheduling_engine.models.availability import AvailabilityRule, EnforcementLevel, RuleType
from ..scheduling_engine.services.availability import AvailabilityEngine
from ..scheduling_engine.services.scheduling import SchedulingService
from ..services import (
    AuditService,
    NoteService,
    PatientNotFoundError,
    RegistryNoteGenerationService,
    SessionService,
    get_audit_service,
)
from ..services.availability_parse_service import AvailabilityRuleParseService
from ..services.google_calendar_service import GoogleCalendarService
from ..settings import get_settings
from ..utcnow import utc_now

# Native app schemes allowed for Google Calendar OAuth redirect
_ALLOWED_GCAL_SCHEMES = {"pablohealth", "therapyrecorder"}


def _is_valid_gcal_redirect_uri(redirect_uri: str) -> bool:
    """Validate redirect_uri against allowed origins and native app schemes."""
    try:
        parsed = urlparse(redirect_uri)
    except Exception:
        return False

    # Allow native app schemes
    if parsed.scheme in _ALLOWED_GCAL_SCHEMES:
        return True

    # Allow localhost for development
    if parsed.scheme == "http" and parsed.hostname == "localhost":
        return True

    # Allow CORS origins (the known frontend URLs)
    settings = get_settings()
    allowed_origins = {o.strip().rstrip("/") for o in settings.cors_origins.split(",") if o.strip()}
    origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return origin in allowed_origins


if TYPE_CHECKING:
    from ..scheduling_engine.models.appointment import Appointment
    from ..scheduling_engine.repositories.appointment import AppointmentRepository
    from ..scheduling_engine.repositories.appointment_type import AppointmentTypeRepository
    from ..scheduling_engine.repositories.availability_rule import AvailabilityRuleRepository

logger = logging.getLogger(__name__)

router = APIRouter(tags=["scheduling"], dependencies=[Depends(require_active_subscription)])


def get_appointment_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> AppointmentRepository:
    """Get appointment repository scoped to the tenant's database."""
    return _appt_repo_factory()


def get_availability_rule_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> AvailabilityRuleRepository:
    """Get availability rule repository scoped to the tenant's database."""
    return _rule_repo_factory()


def get_patient_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientRepository:
    """Get patient repository scoped to the tenant's database.

    Used to resolve patient display names onto appointment responses.
    """
    return _patient_repo_factory()


def get_appointment_type_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> AppointmentTypeRepository:
    """Get appointment type repository scoped to the tenant's database."""
    return _appt_type_repo_factory()


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


def get_google_calendar_service(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> GoogleCalendarService:
    """Get Google Calendar service with injected dependencies."""
    token_repo = _gcal_token_repo_factory()
    appt_repo = _appt_repo_factory()
    settings = get_settings()
    return GoogleCalendarService(
        token_repo=token_repo,
        appointment_repo=appt_repo,
        client_id=settings.google_calendar_client_id,
        client_secret=settings.google_calendar_client_secret.get_secret_value(),
    )


def _sync_appointment_to_google(
    service: SchedulingService,
    gcal_service: GoogleCalendarService,
    user: User,
    appt: Appointment,
) -> Appointment:
    """Best-effort push of an appointment create/update to Google Calendar.

    A Google failure never fails the appointment write — it's recorded as
    google_sync_status='error' and swallowed. A user who isn't connected
    gets no event and no status change: absence of sync isn't an error.
    """
    try:
        event_id = gcal_service.push_appointment(user.id, appt)
    except Exception:
        logger.exception("Failed to push appointment to Google Calendar")
        return service.update_appointment(appt.id, user.id, google_sync_status="error")
    if event_id is None:
        return appt
    return service.update_appointment(
        appt.id, user.id, google_event_id=event_id, google_sync_status="synced"
    )


def _push_cancellation_to_google(
    gcal_service: GoogleCalendarService,
    user: User,
    appt: Appointment,
) -> None:
    """Best-effort delete of the linked Google Calendar event, if any."""
    if not appt.google_event_id:
        return
    try:
        gcal_service.delete_event(user.id, appt.google_event_id)
    except Exception:
        logger.exception("Failed to delete Google Calendar event")


def _patient_name_map(
    patient_repo: PatientRepository,
    user_id: str,
    appointments: Sequence[Appointment],
) -> dict[str, str]:
    """Display names for the appointments' patients, in one repository call.

    Responses carry the name so clients can label events directly. The
    calendar used to join appointments against its own patient list, whose
    first page is all it fetches — in a practice with more patients than the
    page size, every event whose patient sorted beyond that page rendered
    with a fallback label (or none at all for imported appointments, which
    have no title).
    """
    ids = list({a.patient_id for a in appointments})
    if not ids:
        return {}
    patients = patient_repo.get_multiple(ids, user_id)
    return {pid: f"{p.first_name} {p.last_name}" for pid, p in patients.items()}


def _to_response(appt: Appointment, *, patient_name: str | None = None) -> AppointmentResponse:
    return AppointmentResponse(
        id=appt.id,
        user_id=appt.user_id,
        patient_id=appt.patient_id,
        title=appt.title,
        patient_name=patient_name,
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
        ical_uid=appt.ical_uid,
        ical_source=appt.ical_source,
        ical_sync_status=appt.ical_sync_status,
        ehr_appointment_url=appt.ehr_appointment_url,
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
    http_request: Request,
    _ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    service: SchedulingService = Depends(get_scheduling_service),
    audit: AuditService = Depends(get_audit_service),
    gcal_service: GoogleCalendarService = Depends(get_google_calendar_service),
    patient_repo: PatientRepository = Depends(get_patient_repository),
) -> AppointmentResponse:
    """Create a new appointment."""
    try:
        appt = service.create_appointment(
            user.id,
            data=request.model_dump(),
        )
    except InvalidAppointmentError as e:
        raise BadRequestError(str(e)) from e
    audit.log_appointment_action(
        AuditAction.APPOINTMENT_CREATED,
        user,
        http_request,
        appt.id,
        patient_id=appt.patient_id,
    )
    appt = _sync_appointment_to_google(service, gcal_service, user, appt)
    return _to_response(
        appt,
        patient_name=_patient_name_map(patient_repo, user.id, [appt]).get(appt.patient_id),
    )


@router.get("/api/appointments", response_model=AppointmentListResponse)
def list_appointments(
    http_request: Request,
    start: datetime = Query(..., description="Range start (ISO 8601)"),
    end: datetime = Query(..., description="Range end (ISO 8601)"),
    _ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    service: SchedulingService = Depends(get_scheduling_service),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> AppointmentListResponse:
    """List appointments in a date range.

    ``start``/``end`` are parsed and validated as ISO 8601 at the request
    layer, so a malformed value is rejected with a 422 instead of reaching
    the service and surfacing as a 500.
    """
    appointments = service.list_appointments(user.id, start.isoformat(), end.isoformat())
    names = _patient_name_map(patient_repo, user.id, appointments)
    # The payload carries each patient's display name, which makes reading
    # this list a per-record identifier read rather than bare calendar
    # metadata — audit one appointment_viewed per row, mirroring the
    # patients list. The read-coalescing gate collapses repeats of the same
    # appointment within the window, so calendar refetches don't flood the
    # log.
    for a in appointments:
        audit.log_appointment_action(
            AuditAction.APPOINTMENT_VIEWED,
            user,
            http_request,
            a.id,
            patient_id=a.patient_id,
        )
    return AppointmentListResponse(
        data=[_to_response(a, patient_name=names.get(a.patient_id)) for a in appointments],
        total=len(appointments),
    )


@router.get("/api/appointments/{appointment_id}", response_model=AppointmentResponse)
def get_appointment(
    appointment_id: str,
    http_request: Request,
    _ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    service: SchedulingService = Depends(get_scheduling_service),
    audit: AuditService = Depends(get_audit_service),
    patient_repo: PatientRepository = Depends(get_patient_repository),
) -> AppointmentResponse:
    """Get a single appointment."""
    try:
        appt = service.get_appointment(appointment_id, user.id)
    except AppointmentNotFoundError as e:
        raise NotFoundError(str(e)) from e
    audit.log_appointment_action(
        AuditAction.APPOINTMENT_VIEWED,
        user,
        http_request,
        appt.id,
        patient_id=appt.patient_id,
    )
    return _to_response(
        appt,
        patient_name=_patient_name_map(patient_repo, user.id, [appt]).get(appt.patient_id),
    )


@router.patch("/api/appointments/{appointment_id}", response_model=AppointmentResponse)
def update_appointment(
    appointment_id: str,
    request: UpdateAppointmentRequest,
    http_request: Request,
    _ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    service: SchedulingService = Depends(get_scheduling_service),
    audit: AuditService = Depends(get_audit_service),
    gcal_service: GoogleCalendarService = Depends(get_google_calendar_service),
    patient_repo: PatientRepository = Depends(get_patient_repository),
) -> AppointmentResponse:
    """Update an appointment."""
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    try:
        appt = service.update_appointment(appointment_id, user.id, **updates)
    except AppointmentNotFoundError as e:
        raise NotFoundError(str(e)) from e
    except InvalidAppointmentError as e:
        raise BadRequestError(str(e)) from e
    audit.log_appointment_action(
        AuditAction.APPOINTMENT_UPDATED,
        user,
        http_request,
        appt.id,
        patient_id=appt.patient_id,
        changes={"changed_fields": sorted(updates.keys())},
    )
    appt = _sync_appointment_to_google(service, gcal_service, user, appt)
    return _to_response(
        appt,
        patient_name=_patient_name_map(patient_repo, user.id, [appt]).get(appt.patient_id),
    )


@router.delete(
    "/api/appointments/{appointment_id}",
    response_model=AppointmentResponse,
)
def cancel_appointment(
    appointment_id: str,
    http_request: Request,
    _ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    service: SchedulingService = Depends(get_scheduling_service),
    audit: AuditService = Depends(get_audit_service),
    gcal_service: GoogleCalendarService = Depends(get_google_calendar_service),
    patient_repo: PatientRepository = Depends(get_patient_repository),
) -> AppointmentResponse:
    """Cancel an appointment (soft delete — sets status to cancelled)."""
    try:
        appt = service.cancel_appointment(appointment_id, user.id)
    except AppointmentNotFoundError as e:
        raise NotFoundError(str(e)) from e
    audit.log_appointment_action(
        AuditAction.APPOINTMENT_CANCELLED,
        user,
        http_request,
        appt.id,
        patient_id=appt.patient_id,
    )
    _push_cancellation_to_google(gcal_service, user, appt)
    return _to_response(
        appt,
        patient_name=_patient_name_map(patient_repo, user.id, [appt]).get(appt.patient_id),
    )


# --- Appointment → session link ---


def _get_session_service(
    _ctx: TenantContext = Depends(get_tenant_context),
    session_repo: TherapySessionRepository = Depends(_session_repo_factory),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    notes_repo: NotesRepository = Depends(_notes_repo_factory),
) -> SessionService:
    """Get session service for appointment→session linking.

    Depends on get_tenant_context to ensure the practice schema is set
    before any queries run (required for multi-tenant Postgres).
    """
    return SessionService(
        session_repo,
        patient_repo,
        RegistryNoteGenerationService(),
        NoteService(notes_repo),
    )


@router.post(
    "/api/appointments/{appointment_id}/start-session",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def start_session_from_appointment(
    appointment_id: str,
    http_request: Request,
    body: StartSessionFromAppointmentRequest | None = Body(default=None),
    user: User = Depends(require_baa_acceptance),
    service: SchedulingService = Depends(get_scheduling_service),
    session_service: SessionService = Depends(_get_session_service),
    audit: AuditService = Depends(get_audit_service),
    authorizer: NoteTypeAuthorizer = Depends(get_note_type_authorizer),
) -> SessionResponse:
    """Create a therapy session linked to a calendar appointment.

    Used by the companion app when the therapist clicks 'Start Session'
    on a calendar appointment. Copies appointment data into a new
    therapy session and sets appointment.session_id to link them.

    Optional body field ``note_type`` selects the note-type registry
    key for the session. When omitted, the session falls back to the
    appointment's default (currently SOAP).
    """
    # 1. Fetch appointment
    try:
        appt = service.get_appointment(appointment_id, user.id)
    except AppointmentNotFoundError as e:
        raise NotFoundError(str(e)) from e

    # 2. Already has a session? → 409
    if appt.session_id:
        raise ConflictError(
            "Session already started for this appointment",
            {"session_id": appt.session_id},
        )

    # 3. Unmatched patient? → 400
    if not appt.patient_id:
        raise BadRequestError("Appointment has no linked patient. Resolve the client match first.")

    # 4. Authorize requested note type. Only check when caller explicitly
    #    requested one — falling back to the default is always allowed.
    requested_note_type = body.note_type if body else None
    if requested_note_type is not None and not authorizer.is_allowed(user, requested_note_type):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Note type {requested_note_type!r} not allowed for this subscription",
        )

    # 5. Create session from appointment data
    request = ScheduleSessionRequest(
        patient_id=appt.patient_id,
        scheduled_at=appt.start_at,
        duration_minutes=appt.duration_minutes,
        video_link=appt.video_link,
        video_platform=VideoPlatform(appt.video_platform) if appt.video_platform else None,
        session_type=(
            SessionType(appt.session_type) if appt.session_type else SessionType.INDIVIDUAL
        ),
        source=SessionSource.COMPANION,
        notes=appt.notes,
        note_type=requested_note_type,
    )

    try:
        session, patient = session_service.schedule_session(user.id, request)
    except PatientNotFoundError as e:
        raise NotFoundError("Patient not found for this appointment.") from e

    # 6. Link appointment → session
    service.update_appointment(appointment_id, user.id, session_id=session.id)

    # 7. Audit
    audit.log_session_action(AuditAction.SESSION_CREATED, user, http_request, session, patient)

    return SessionResponse.from_session(session, patient.display_name)


# --- Recurring appointment endpoints ---


@router.post(
    "/api/appointments/recurring",
    response_model=AppointmentListResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_recurring_appointment(
    request: CreateRecurringAppointmentRequest,
    http_request: Request,
    _ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    service: SchedulingService = Depends(get_scheduling_service),
    audit: AuditService = Depends(get_audit_service),
    patient_repo: PatientRepository = Depends(get_patient_repository),
) -> AppointmentListResponse:
    """Create a recurring appointment series."""
    try:
        appointments = service.create_recurring(
            user.id,
            data=request.model_dump(exclude={"frequency", "timezone", "end_date", "count"}),
            recurrence={
                "frequency": request.frequency,
                "timezone": request.timezone,
                "end_date": request.end_date,
                "count": request.count,
            },
        )
    except (InvalidAppointmentError, InvalidRecurrenceError) as e:
        raise BadRequestError(str(e)) from e
    first_appt_id = appointments[0].id if appointments else "series"
    audit.log_appointment_action(
        AuditAction.APPOINTMENT_SERIES_CREATED,
        user,
        http_request,
        first_appt_id,
        patient_id=appointments[0].patient_id if appointments else None,
        changes={"occurrence_count": len(appointments), "frequency": request.frequency},
    )
    names = _patient_name_map(patient_repo, user.id, appointments)
    return AppointmentListResponse(
        data=[_to_response(a, patient_name=names.get(a.patient_id)) for a in appointments],
        total=len(appointments),
    )


@router.post(
    "/api/appointments/{appointment_id}/edit-series",
    response_model=AppointmentListResponse,
)
def edit_series(
    appointment_id: str,
    request: EditSeriesRequest,
    http_request: Request,
    _ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    service: SchedulingService = Depends(get_scheduling_service),
    audit: AuditService = Depends(get_audit_service),
    patient_repo: PatientRepository = Depends(get_patient_repository),
) -> AppointmentListResponse:
    """Edit all future occurrences in a recurring series."""
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    try:
        appointments = service.edit_future_occurrences(appointment_id, user.id, **updates)
    except AppointmentNotFoundError as e:
        raise NotFoundError(str(e)) from e
    except InvalidAppointmentError as e:
        raise BadRequestError(str(e)) from e
    audit.log_appointment_action(
        AuditAction.APPOINTMENT_SERIES_UPDATED,
        user,
        http_request,
        appointment_id,
        changes={
            "changed_fields": sorted(updates.keys()),
            "occurrence_count": len(appointments),
        },
    )
    names = _patient_name_map(patient_repo, user.id, appointments)
    return AppointmentListResponse(
        data=[_to_response(a, patient_name=names.get(a.patient_id)) for a in appointments],
        total=len(appointments),
    )


@router.delete(
    "/api/appointments/{appointment_id}/cancel-series",
    response_model=AppointmentListResponse,
)
def cancel_series(
    appointment_id: str,
    http_request: Request,
    _ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    service: SchedulingService = Depends(get_scheduling_service),
    audit: AuditService = Depends(get_audit_service),
    patient_repo: PatientRepository = Depends(get_patient_repository),
) -> AppointmentListResponse:
    """Cancel all future occurrences in a recurring series."""
    try:
        appointments = service.cancel_future_occurrences(appointment_id, user.id)
    except AppointmentNotFoundError as e:
        raise NotFoundError(str(e)) from e
    except InvalidAppointmentError as e:
        raise BadRequestError(str(e)) from e
    audit.log_appointment_action(
        AuditAction.APPOINTMENT_SERIES_CANCELLED,
        user,
        http_request,
        appointment_id,
        changes={"occurrence_count": len(appointments)},
    )
    names = _patient_name_map(patient_repo, user.id, appointments)
    return AppointmentListResponse(
        data=[_to_response(a, patient_name=names.get(a.patient_id)) for a in appointments],
        total=len(appointments),
    )


# --- Availability endpoints ---


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
    duration: int | None = Query(
        None,
        description="Slot duration in minutes (defaults to the user's session default)",
        ge=1,
        le=480,
    ),
    ctx: TenantContext = Depends(get_tenant_context),
    engine: AvailabilityEngine = Depends(get_availability_engine),
) -> FreeSlotsResponse:
    """Get available time slots for a given date."""
    result = engine.get_free_slots(ctx.user_id, date, duration)
    return FreeSlotsResponse(
        date=date,
        duration_minutes=result.duration_minutes,
        slots=[TimeSlotResponse(start=s.start, end=s.end) for s in result.slots],
        total=len(result.slots),
        configured=result.configured,
    )


@router.post("/api/availability/check", response_model=CheckConflictsResponse)
def check_conflicts(
    request: CheckConflictsRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    engine: AvailabilityEngine = Depends(get_availability_engine),
) -> CheckConflictsResponse:
    """Check scheduling conflicts for a proposed time."""
    result = engine.check_conflicts(ctx.user_id, request.start_at, request.end_at)
    conflict_responses = [
        ConflictResponse(
            rule_type=c.rule.rule_type,
            enforcement=c.enforcement,
            message=c.message,
        )
        for c in result.conflicts
    ]
    has_hard = any(c.enforcement == EnforcementLevel.HARD for c in result.conflicts)
    return CheckConflictsResponse(
        conflicts=conflict_responses,
        has_hard_conflicts=has_hard,
        configured=result.configured,
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
        raise BadRequestError(f"Invalid rule_type: {request.rule_type}") from e

    try:
        EnforcementLevel(request.enforcement)
    except ValueError as e:
        raise BadRequestError(f"Invalid enforcement: {request.enforcement}") from e

    now = utc_now()
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
        raise NotFoundError(f"Rule not found: {rule_id}")

    if request.rule_type is not None:
        try:
            RuleType(request.rule_type)
        except ValueError as e:
            raise BadRequestError(f"Invalid rule_type: {request.rule_type}") from e
        rule.rule_type = request.rule_type

    if request.enforcement is not None:
        try:
            EnforcementLevel(request.enforcement)
        except ValueError as e:
            raise BadRequestError(f"Invalid enforcement: {request.enforcement}") from e
        rule.enforcement = request.enforcement

    if request.params is not None:
        rule.params = request.params

    rule.updated_at = utc_now()
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
        raise NotFoundError(f"Rule not found: {rule_id}")


def get_availability_rule_parse_service() -> AvailabilityRuleParseService:
    """Get the natural-language availability-rule parse service instance."""
    return AvailabilityRuleParseService()


@router.post("/api/availability/rules/parse", response_model=ParseAvailabilityRulesResponse)
def parse_availability_rules(
    request: ParseAvailabilityRulesRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    rule_repo: AvailabilityRuleRepository = Depends(get_availability_rule_repository),
    parse_service: AvailabilityRuleParseService = Depends(get_availability_rule_parse_service),
) -> ParseAvailabilityRulesResponse:
    """Parse a natural-language sentence into proposed availability rules.

    Two-stage propose-then-confirm: this never creates a rule. The caller
    reviews (and may edit) each proposal, then confirms it through the
    existing create-rule endpoint -- no new write path exists here.
    """
    get_availability_parse_limiter().check(ctx.user_id)

    # Release the request-scoped DB connection before the LLM call, same
    # seam as the note-import route (sessions.py) -- otherwise the pooled
    # connection (and its open transaction) sits idle across the round trip.
    release_db_connection()

    result = parse_service.parse(request.text)

    proposals = [
        ProposedAvailabilityRule(
            rule_type=p.rule_type,
            enforcement=p.enforcement,
            params=p.params,
            human_summary=p.human_summary,
        )
        for p in result.proposals
    ]

    existing_conflicting_rules: list[AvailabilityRuleResponse] = []
    if result.exclusive and proposals:
        proposed_days = {
            p.params["day_of_week"] for p in result.proposals if p.rule_type == "working_hours"
        }
        existing_conflicting_rules = [
            _rule_to_response(r)
            for r in rule_repo.list_by_user(ctx.user_id)
            if r.rule_type == "working_hours" and r.params.get("day_of_week") not in proposed_days
        ]

    return ParseAvailabilityRulesResponse(
        proposals=proposals,
        could_not_parse=result.could_not_parse,
        exclusive=result.exclusive,
        existing_conflicting_rules=existing_conflicting_rules,
    )


# --- Appointment type endpoints ---


def _appointment_type_to_response(appointment_type: AppointmentType) -> AppointmentTypeResponse:
    return AppointmentTypeResponse(
        id=appointment_type.id,
        user_id=appointment_type.user_id,
        name=appointment_type.name,
        default_fee_cents=appointment_type.default_fee_cents,
        created_at=appointment_type.created_at,
        updated_at=appointment_type.updated_at,
    )


@router.get("/api/appointment-types", response_model=AppointmentTypeListResponse)
def list_appointment_types(
    ctx: TenantContext = Depends(get_tenant_context),
    type_repo: AppointmentTypeRepository = Depends(get_appointment_type_repository),
) -> AppointmentTypeListResponse:
    """List all appointment types for the current user."""
    types = type_repo.list_by_user(ctx.user_id)
    return AppointmentTypeListResponse(
        data=[_appointment_type_to_response(t) for t in types],
        total=len(types),
    )


@router.post(
    "/api/appointment-types",
    response_model=AppointmentTypeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_appointment_type(
    request: CreateAppointmentTypeRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    type_repo: AppointmentTypeRepository = Depends(get_appointment_type_repository),
) -> AppointmentTypeResponse:
    """Create a new appointment type with an optional default fee."""
    now = utc_now()
    appointment_type = AppointmentType(
        id=str(uuid.uuid4()),
        user_id=ctx.user_id,
        name=request.name,
        default_fee_cents=request.default_fee_cents,
        created_at=now,
        updated_at=now,
    )
    created = type_repo.create(appointment_type)
    return _appointment_type_to_response(created)


@router.patch(
    "/api/appointment-types/{appointment_type_id}",
    response_model=AppointmentTypeResponse,
)
def update_appointment_type(
    appointment_type_id: str,
    request: UpdateAppointmentTypeRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    type_repo: AppointmentTypeRepository = Depends(get_appointment_type_repository),
) -> AppointmentTypeResponse:
    """Update an existing appointment type."""
    appointment_type = type_repo.get(appointment_type_id, ctx.user_id)
    if not appointment_type:
        raise NotFoundError(f"Appointment type not found: {appointment_type_id}")

    if request.name is not None:
        appointment_type.name = request.name
    if request.default_fee_cents is not None:
        appointment_type.default_fee_cents = request.default_fee_cents

    appointment_type.updated_at = utc_now()
    updated = type_repo.update(appointment_type)
    return _appointment_type_to_response(updated)


@router.delete(
    "/api/appointment-types/{appointment_type_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_appointment_type(
    appointment_type_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    type_repo: AppointmentTypeRepository = Depends(get_appointment_type_repository),
) -> None:
    """Delete an appointment type."""
    deleted = type_repo.delete(appointment_type_id, ctx.user_id)
    if not deleted:
        raise NotFoundError(f"Appointment type not found: {appointment_type_id}")


# --- Google Calendar endpoints ---


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
    if not _is_valid_gcal_redirect_uri(redirect_uri):
        raise BadRequestError("Invalid redirect_uri")
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
    if not _is_valid_gcal_redirect_uri(redirect_uri):
        raise BadRequestError("Invalid redirect_uri")
    try:
        service.handle_callback(ctx.user_id, code, redirect_uri)
    except Exception as e:
        logger.exception("Google Calendar OAuth callback failed")
        raise BadRequestError("OAuth callback failed") from e
    return {"status": "connected"}


@router.delete("/api/google-calendar/disconnect")
def google_calendar_disconnect(
    ctx: TenantContext = Depends(get_tenant_context),
    service: GoogleCalendarService = Depends(get_google_calendar_service),
) -> dict[str, str]:
    """Disconnect Google Calendar and remove stored tokens."""
    deleted = service.disconnect(ctx.user_id)
    if not deleted:
        raise NotFoundError("Google Calendar not connected")
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
