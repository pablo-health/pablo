# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Scheduling API routes — thin HTTP handlers for appointment and availability CRUD."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Collection, Sequence
from datetime import UTC, datetime, tzinfo
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status

from ..api_errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    UnprocessableEntityError,
)
from ..auth.service import (
    TenantContext,
    get_tenant_context,
    require_active_subscription,
    require_baa_acceptance,
)
from ..calendar_providers.capabilities import CalendarCapability, CalendarWriteTarget
from ..calendar_providers.consent_copy import capability_promise
from ..calendar_providers.event_titles import (
    ATTESTATION_STATEMENTS,
    CURRENT_ATTESTATION_VERSION,
    EventTitleStyle,
)
from ..calendar_providers.oauth_state import OAuthStateError
from ..db import release_db_connection
from ..models import (
    AuditAction,
    ScheduleSessionRequest,
    SessionResponse,
    User,
)
from ..models.audit import ResourceType
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
    GoogleCalendarConsentOption,
    GoogleCalendarConsentOptionsResponse,
    GoogleCalendarStatusResponse,
    ParseAvailabilityRulesRequest,
    ParseAvailabilityRulesResponse,
    ProposedAvailabilityRule,
    SetEventTitlingRequest,
    SetEventTitlingResponse,
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
    UserRepository,
    get_user_repository,
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
    AppointmentConflictError,
    AppointmentNotFoundError,
    InvalidAppointmentError,
    InvalidRecurrenceError,
    RuleViolationError,
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
from ..services.google_calendar_service import (
    DEFAULT_EVENT_TITLING,
    DEFAULT_WRITE_TARGET,
    GoogleCalendarService,
    RetitleOutcome,
    google_consent_surface,
)
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


def _apply_appointment_type(
    data: dict[str, object],
    *,
    user_id: str,
    type_repo: AppointmentTypeRepository,
) -> None:
    """Let a chosen appointment type speak for the appointment's label.

    When the caller names a type, that type is authoritative: ``session_type``
    is overwritten with its name so the id and the label cannot drift apart.
    Nothing else is inferred — the caller still sends its own duration, because
    a clinician may legitimately book a longer-than-usual session of a type.

    Mutates ``data`` in place. Raises ``NotFoundError`` for a type that is not
    this clinician's, which also stops one clinician booking against another's
    type inside a shared practice.
    """
    appointment_type_id = data.get("appointment_type_id")
    if not appointment_type_id:
        return
    appointment_type = type_repo.get(str(appointment_type_id), user_id)
    if appointment_type is None:
        raise NotFoundError(f"Appointment type not found: {appointment_type_id}")
    data["session_type"] = appointment_type.name


def get_availability_engine(
    rule_repo: AvailabilityRuleRepository = Depends(get_availability_rule_repository),
    appt_repo: AppointmentRepository = Depends(get_appointment_repository),
) -> AvailabilityEngine:
    """Get availability engine with injected repositories."""
    return AvailabilityEngine(rule_repo, appt_repo)


def get_scheduling_service(
    repo: AppointmentRepository = Depends(get_appointment_repository),
    engine: AvailabilityEngine = Depends(get_availability_engine),
) -> SchedulingService:
    """Get scheduling service with injected repository and availability engine."""
    return SchedulingService(repo, engine)


def _owner_timezone(user_repo: UserRepository, user_id: str) -> tzinfo:
    """Resolve the rule owner's IANA timezone preference.

    Falls back to UTC on an unresolvable preference (never seen by
    ``ZoneInfo``, e.g. left over from a client bug) rather than failing the
    request — a bad stored string shouldn't block every booking read/write
    until someone fixes it by hand. Logs the user id only; the raw
    preference string is caller-controlled input and doesn't belong in logs.
    """
    raw = user_repo.get_preferences(user_id).timezone
    try:
        return ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Invalid timezone preference for user %s; falling back to UTC", user_id)
        return UTC


def get_owner_timezone(
    ctx: TenantContext = Depends(get_tenant_context),
    user_repo: UserRepository = Depends(get_user_repository),
) -> tzinfo:
    """The rule owner's preferred timezone — the frame availability rules
    (working hours, blocked days, per-day caps) are evaluated in."""
    return _owner_timezone(user_repo, ctx.user_id)


def _now(tz: tzinfo) -> datetime:
    """Current time in the given timezone — a seam tests monkeypatch to pin
    the reference date natural-language date resolution runs against."""
    return datetime.now(tz)


def get_google_calendar_service(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> GoogleCalendarService:
    """Get Google Calendar service with injected dependencies."""
    return GoogleCalendarService.from_surface(
        google_consent_surface(get_settings()),
        token_repo=_gcal_token_repo_factory(),
        appointment_repo=_appt_repo_factory(),
        patient_repo=_patient_repo_factory(),
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


def _to_response(
    appt: Appointment,
    *,
    patient_name: str | None = None,
    warnings: list[str] | None = None,
) -> AppointmentResponse:
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
        note_type=appt.note_type,
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
        service_code=appt.service_code,
        modifiers=appt.modifiers,
        unit_count=appt.unit_count,
        place_of_service=appt.place_of_service,
        diagnosis_codes=appt.diagnosis_codes,
        created_at=appt.created_at,
        updated_at=appt.updated_at,
        warnings=warnings or [],
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
    type_repo: AppointmentTypeRepository = Depends(get_appointment_type_repository),
    tz: tzinfo = Depends(get_owner_timezone),
) -> AppointmentResponse:
    """Create a new appointment."""
    data = request.model_dump()
    _apply_appointment_type(data, user_id=user.id, type_repo=type_repo)
    try:
        appt = service.create_appointment(
            user.id,
            data=data,
            tz=tz,
        )
    except InvalidAppointmentError as e:
        raise BadRequestError(str(e)) from e
    except AppointmentConflictError as e:
        raise ConflictError(str(e)) from e
    except RuleViolationError as e:
        raise UnprocessableEntityError(str(e), {"violations": e.violations}) from e
    # Captured before any further service calls (Google sync below issues its
    # own update_appointment for linking, which resets rule_warnings).
    warnings = service.rule_warnings
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
        warnings=warnings,
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
    tz: tzinfo = Depends(get_owner_timezone),
) -> AppointmentListResponse:
    """List appointments in a date range.

    ``start``/``end`` are parsed and validated as ISO 8601 at the request
    layer, so a malformed value is rejected with a 422 instead of reaching
    the service and surfacing as a 500. A value sent without an offset is
    read as wall-clock in the owner's timezone, matching how availability
    rules are evaluated.
    """
    appointments = service.list_appointments(user.id, start.isoformat(), end.isoformat(), tz=tz)
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
    tz: tzinfo = Depends(get_owner_timezone),
) -> AppointmentResponse:
    """Update an appointment."""
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    try:
        appt = service.update_appointment(appointment_id, user.id, tz=tz, **updates)
    except AppointmentNotFoundError as e:
        raise NotFoundError(str(e)) from e
    except InvalidAppointmentError as e:
        raise BadRequestError(str(e)) from e
    except AppointmentConflictError as e:
        raise ConflictError(str(e)) from e
    except RuleViolationError as e:
        raise UnprocessableEntityError(str(e), {"violations": e.violations}) from e
    # Captured before any further service calls (Google sync below issues its
    # own update_appointment for linking, which resets rule_warnings).
    warnings = service.rule_warnings
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
        warnings=warnings,
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
    key for the session, overriding the appointment's own note type.
    When omitted, the session uses the note type chosen when the
    appointment was booked (SOAP if none was set).
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

    # 4. Authorize the effective note type — an explicit override, or else
    #    the one seeded on the appointment at booking time.
    requested_note_type = (body.note_type if body else None) or appt.note_type
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
    tz: tzinfo = Depends(get_owner_timezone),
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
            tz=tz,
        )
    except (InvalidAppointmentError, InvalidRecurrenceError) as e:
        raise BadRequestError(str(e)) from e
    except AppointmentConflictError as e:
        raise ConflictError(str(e)) from e
    except RuleViolationError as e:
        raise UnprocessableEntityError(str(e), {"violations": e.violations}) from e
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
    tz: tzinfo = Depends(get_owner_timezone),
) -> FreeSlotsResponse:
    """Get available time slots for a given date."""
    result = engine.get_free_slots(ctx.user_id, date, duration, tz=tz)
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
    tz: tzinfo = Depends(get_owner_timezone),
) -> CheckConflictsResponse:
    """Check scheduling conflicts for a proposed time."""
    result = engine.check_conflicts(ctx.user_id, request.start_at, request.end_at, tz=tz)
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
    user_repo: UserRepository = Depends(get_user_repository),
) -> ParseAvailabilityRulesResponse:
    """Parse a natural-language sentence into proposed availability rules.

    Two-stage propose-then-confirm: this never creates a rule. The caller
    reviews (and may edit) each proposal, then confirms it through the
    existing create-rule endpoint -- no new write path exists here.
    """
    get_availability_parse_limiter().check(ctx.user_id)

    # The reference date a "next Friday"-style sentence resolves against is
    # the clinician's own calendar day, read before the connection below is
    # released -- same owner-timezone frame as rule evaluation.
    tz = _owner_timezone(user_repo, ctx.user_id)
    reference_date = _now(tz).date()

    # Release the request-scoped DB connection before the LLM call, same
    # seam as the note-import route (sessions.py) -- otherwise the pooled
    # connection (and its open transaction) sits idle across the round trip.
    release_db_connection()

    result = parse_service.parse(request.text, reference_date=reference_date)

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
        refusal_reason=result.refusal_reason,
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
        duration_minutes=appointment_type.duration_minutes,
        audience=appointment_type.audience,
        min_notice_hours=appointment_type.min_notice_hours,
        earliest_offer_business_days=appointment_type.earliest_offer_business_days,
        horizon=appointment_type.horizon,
        horizon_unit=appointment_type.horizon_unit,
        self_bookable=appointment_type.self_bookable,
        offerable=appointment_type.offerable,
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
    """Create a new appointment type.

    Unspecified scheduling fields take the request model's defaults, which
    describe a standard session, so a caller that only sends a name gets a
    usable type rather than one that can never be offered.
    """
    now = utc_now()
    appointment_type = AppointmentType(
        id=str(uuid.uuid4()),
        user_id=ctx.user_id,
        name=request.name,
        default_fee_cents=request.default_fee_cents,
        duration_minutes=request.duration_minutes,
        audience=request.audience,
        min_notice_hours=request.min_notice_hours,
        earliest_offer_business_days=request.earliest_offer_business_days,
        horizon=request.horizon,
        horizon_unit=request.horizon_unit,
        self_bookable=request.self_bookable,
        offerable=request.offerable,
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
    """Update an existing appointment type.

    Only fields the caller actually sent are touched. That distinction matters
    for ``min_notice_hours``, where ``null`` is a real value meaning "defer to
    the practice default" — an omitted field leaves it alone, an explicit null
    clears it. ``exclude_unset`` is what separates the two, so do not simplify
    this to an ``is not None`` check per field.
    """
    appointment_type = type_repo.get(appointment_type_id, ctx.user_id)
    if not appointment_type:
        raise NotFoundError(f"Appointment type not found: {appointment_type_id}")

    for name, value in request.model_dump(exclude_unset=True).items():
        setattr(appointment_type, name, value)

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


def _parse_write_target(value: str) -> CalendarWriteTarget:
    """Turn the query parameter into the seam's write target, or reject it."""
    try:
        return CalendarWriteTarget(value)
    except ValueError as exc:
        raise BadRequestError("Invalid write_target") from exc


def _connect_capabilities(*, busy: bool) -> set[CalendarCapability]:
    """What connecting asks Google for.

    Reading event content is not here and must not be: it is asked for when
    an import is run, so a therapist who never imports never grants it.
    """
    capabilities = {CalendarCapability.PUSH}
    if busy:
        capabilities.add(CalendarCapability.BUSY)
    return capabilities


def _parse_titling(value: str) -> EventTitleStyle:
    """Read a requested naming style, or refuse it.

    Deliberately not the parser that falls back to the floor: a stored
    value gone bad should degrade quietly, but a caller asking for
    something that isn't a style has made a mistake worth hearing about.
    """
    try:
        return EventTitleStyle(value)
    except ValueError as exc:
        raise BadRequestError("Invalid event_titling") from exc


def _narrows(previous: EventTitleStyle, style: EventTitleStyle) -> bool:
    """Whether the new choice says less about a patient than the old one."""
    rungs = {EventTitleStyle.GENERIC: 0, EventTitleStyle.INITIALS: 1, EventTitleStyle.FULL: 2}
    return rungs[style] < rungs[previous]


@router.put(
    "/api/google-calendar/event-titling",
    response_model=SetEventTitlingResponse,
)
def set_google_calendar_event_titling(
    http_request: Request,
    request: SetEventTitlingRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    service: GoogleCalendarService = Depends(get_google_calendar_service),
    audit: AuditService = Depends(get_audit_service),
) -> SetEventTitlingResponse:
    """Choose how sessions read on the connected calendar.

    Writing a patient's name onto a calendar Pablo holds no agreement for
    is the therapist's disclosure to make, so the top rung requires them
    to say the account is covered and records that they did. Narrowing the
    choice rewrites what is already sitting in the calendar — otherwise
    the control would change the setting and leave the names behind.
    """
    style = _parse_titling(request.style)
    if style is EventTitleStyle.FULL and not request.attested:
        raise BadRequestError("Full names require confirming the account is covered")

    status_info = service.get_sync_status(ctx.user_id)
    if not status_info.get("connected"):
        raise NotFoundError("Google Calendar not connected")
    previous = _parse_titling(status_info.get("event_titling") or EventTitleStyle.GENERIC.value)

    attested_account = str(status_info.get("calendar_id") or "")
    if not service.set_event_titling(ctx.user_id, style, attested_account=attested_account):
        raise NotFoundError("Google Calendar not connected")

    if style is EventTitleStyle.FULL:
        # Evidence, not a preference: who said it, when, and which account
        # it covered. It outlives the connection deliberately — a later
        # disconnect does not unsay it.
        audit.log(
            AuditAction.CALENDAR_NAME_DISCLOSURE_ATTESTED,
            user,
            http_request,
            resource_type=ResourceType.APPOINTMENT,
            resource_id=attested_account or "unknown",
            changes={
                "calendar_account": attested_account,
                "event_titling": style.value,
                "previous_event_titling": previous.value,
                # Both the version and the wording it stands for, so the
                # row says what was agreed to without needing the table it
                # came from — and so a later copy change cannot rewrite
                # what a past attestation appears to have said.
                "attestation_statement_version": CURRENT_ATTESTATION_VERSION,
                "attestation_statement": ATTESTATION_STATEMENTS[CURRENT_ATTESTATION_VERSION],
            },
        )

    outcome = (
        service.retitle_future_events(ctx.user_id)
        if _narrows(previous, style)
        else RetitleOutcome(0, 0, 0)
    )
    return SetEventTitlingResponse(
        style=style.value,
        events_retitled=outcome.retitled,
        events_not_retitled=outcome.failed + outcome.skipped,
    )


@router.get(
    "/api/google-calendar/consent-options",
    response_model=GoogleCalendarConsentOptionsResponse,
)
def google_calendar_consent_options(
    _ctx: TenantContext = Depends(get_tenant_context),
    service: GoogleCalendarService = Depends(get_google_calendar_service),
) -> GoogleCalendarConsentOptionsResponse:
    """The choices connecting offers, each with the promise it can honestly make.

    Every promise is generated from the provider's own declaration of how
    the underlying grant is limited, so a choice the grant does not narrow
    can never be presented as one it does.
    """
    return GoogleCalendarConsentOptionsResponse(
        write_targets=[
            GoogleCalendarConsentOption(
                id=target.value,
                promise=capability_promise(
                    service.display_name,
                    service.capability_declarations(write_target=target)[CalendarCapability.PUSH],
                ),
            )
            for target in CalendarWriteTarget
        ],
        busy=GoogleCalendarConsentOption(
            id=CalendarCapability.BUSY.value,
            promise=capability_promise(
                service.display_name,
                service.capability_declarations()[CalendarCapability.BUSY],
            ),
        ),
        default_write_target=DEFAULT_WRITE_TARGET.value,
        busy_default=True,
    )


@router.get(
    "/api/google-calendar/authorize",
    response_model=GoogleCalendarAuthResponse,
)
def google_calendar_authorize(
    redirect_uri: str = Query(..., description="OAuth redirect URI"),
    write_target: str = Query(
        DEFAULT_WRITE_TARGET.value,
        description="Which calendar Pablo writes sessions to",
    ),
    busy: bool = Query(True, description="Also ask when the therapist is booked"),
    ctx: TenantContext = Depends(get_tenant_context),
    service: GoogleCalendarService = Depends(get_google_calendar_service),
) -> GoogleCalendarAuthResponse:
    """Get the Google OAuth URL for exactly the selected permissions."""
    if not _is_valid_gcal_redirect_uri(redirect_uri):
        raise BadRequestError("Invalid redirect_uri")
    auth_url = service.get_auth_url(
        ctx.user_id,
        redirect_uri,
        capabilities=_connect_capabilities(busy=busy),
        write_target=_parse_write_target(write_target),
    )
    return GoogleCalendarAuthResponse(auth_url=auth_url)


def _parse_incremental_capability(value: str) -> CalendarCapability:
    """Turn the query parameter into the single capability being granted, or reject it."""
    try:
        return CalendarCapability(value)
    except ValueError as exc:
        raise BadRequestError("Unsupported capability") from exc


@router.get("/api/google-calendar/callback")
def google_calendar_callback(
    code: str = Query(..., description="OAuth authorization code"),
    redirect_uri: str = Query(..., description="OAuth redirect URI"),
    state: str = Query("", description="The state minted when authorization started"),
    write_target: str = Query(
        DEFAULT_WRITE_TARGET.value,
        description="The write target the authorization URL was built with",
    ),
    busy: bool = Query(True, description="Whether free/busy was part of that request"),
    event_titling: str = Query(
        DEFAULT_EVENT_TITLING.value,
        description="How sessions should read on the calendar, for a connect",
    ),
    capability: str | None = Query(
        None,
        description=(
            "A single capability being granted incrementally (currently only "
            "'import'), on top of an existing connection — rather than the "
            "connect-time set write_target/busy describe"
        ),
    ),
    ctx: TenantContext = Depends(get_tenant_context),
    service: GoogleCalendarService = Depends(get_google_calendar_service),
) -> dict[str, str]:
    """Handle Google OAuth callback — exchange code for tokens.

    Requires the state minted at authorization and bound to the caller, so
    a code can only be exchanged by the person whose authorization request
    produced it. Declared with an empty default rather than as a required
    parameter so a missing one is answered the same way as a bad one.

    Two shapes land here. A connect (no ``capability``) carries the whole
    selection, which decides both the grant and which calendar the
    connection is bound to. An incremental grant (``capability`` set, e.g.
    from the import wizard's "Look at my week") carries only the one
    capability being added — the write target is read back from the
    existing connection rather than defaulted, so an incremental grant can
    never silently rebind PUSH to a different calendar mid-flow.
    """
    if not _is_valid_gcal_redirect_uri(redirect_uri):
        raise BadRequestError("Invalid redirect_uri")
    if not state:
        raise BadRequestError("Missing state")

    if capability is not None:
        capabilities: Collection[CalendarCapability] = [_parse_incremental_capability(capability)]
        existing = service.get_sync_status(ctx.user_id)
        target = _parse_write_target(existing.get("write_target") or DEFAULT_WRITE_TARGET.value)
        # Read back for the same reason as the write target: adding a
        # capability must not quietly re-decide what the therapist's
        # events say about their clients.
        titling = _parse_titling(existing.get("event_titling") or DEFAULT_EVENT_TITLING.value)
    else:
        capabilities = _connect_capabilities(busy=busy)
        target = _parse_write_target(write_target)
        titling = _parse_titling(event_titling)

    try:
        service.handle_callback(
            ctx.user_id,
            code,
            redirect_uri,
            state=state,
            capabilities=capabilities,
            write_target=target,
            event_titling=titling,
        )
    except OAuthStateError as e:
        logger.warning("Google Calendar OAuth callback rejected an unusable state")
        raise BadRequestError("Invalid state") from e
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
