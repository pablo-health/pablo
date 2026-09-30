# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Sessions another service puts on a calendar Pablo follows.

The calendar shows the open ones as read-only blocks; the banner counts the
questions; the review answers them. See ``app.services.outside_sessions`` for
what becomes a question and what an answer does.

Event titles often carry a client's name. They go to the clinician who owns
the calendar and nowhere else — the audit trail records counts and sources.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request

from ..api_errors import BadRequestError, NotFoundError
from ..auth.service import (
    TenantContext,
    get_tenant_context,
    require_active_subscription,
    require_baa_acceptance,
)
from ..models import AuditAction, User
from ..models.audit import ResourceType
from ..models.outside_sessions import (
    AnsweredAppointment,
    FollowMainCalendarRequest,
    FollowMainCalendarResponse,
    OutsideAnswer,
    OutsideAnswerRequest,
    OutsideAnswerResponse,
    OutsideQuestionResponse,
    OutsideQuestionsResponse,
    OutsideSessionResponse,
    OutsideSessionsResponse,
)
from ..models.patient import Patient
from ..repositories import (
    PatientRepository,
)
from ..repositories import (
    get_external_calendar_event_repository as _events_repo_factory,
)
from ..repositories.external_calendar_event import (  # noqa: TC001 — resolved at runtime
    ExternalCalendarEventRepository,
)
from ..repositories.patient_source_mapping import (  # noqa: TC001 — resolved at runtime
    PatientSourceMappingRepository,
)
from ..scheduling_engine.repositories.appointment import (  # noqa: TC001 — resolved at runtime
    AppointmentRepository,
)
from ..services import AuditService, get_audit_service
from ..services.google_calendar_service import (  # noqa: TC001 — resolved at runtime
    GoogleCalendarService,
)
from ..services.outside_sessions import OutsideSessions
from ..utcnow import utc_now
from .calendar_import import get_patient_source_mapping_repository, series_match
from .patients import get_patient_repository
from .scheduling import get_appointment_repository, get_google_calendar_service

router = APIRouter(tags=["outside-sessions"], dependencies=[Depends(require_active_subscription)])

#: What a client added from a followed session is recorded as coming from.
PATIENT_ORIGIN = "calendar_follow"
#: A chart's name when the event had no title to take one from.
UNNAMED_CLIENT = "New client"
_NAME_MAX = 255


def get_external_calendar_events(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> ExternalCalendarEventRepository:
    """Followed calendar events, scoped to the tenant's database."""
    return _events_repo_factory()


def get_outside_sessions(
    events: ExternalCalendarEventRepository = Depends(get_external_calendar_events),
    appointments: AppointmentRepository = Depends(get_appointment_repository),
    patients: PatientRepository = Depends(get_patient_repository),
    mappings: PatientSourceMappingRepository = Depends(get_patient_source_mapping_repository),
) -> OutsideSessions:
    return OutsideSessions(events, appointments, patients, mappings)


@router.get("/api/calendar/outside-sessions", response_model=OutsideSessionsResponse)
def list_outside_sessions(
    http_request: Request,
    start: datetime = Query(..., description="Start of the window, inclusive"),
    end: datetime = Query(..., description="End of the window, exclusive"),
    user: User = Depends(require_baa_acceptance),
    outside: OutsideSessions = Depends(get_outside_sessions),
    audit: AuditService = Depends(get_audit_service),
) -> OutsideSessionsResponse:
    """Sessions nobody has said who they are yet, for the calendar to show."""
    if end <= start:
        raise BadRequestError("end must be after start")
    open_sessions = outside.open_sessions(user.id, start, end)
    audit.log(
        AuditAction.APPOINTMENT_LISTED,
        user,
        http_request,
        resource_type=ResourceType.APPOINTMENT,
        resource_id="outside-sessions",
        changes={"outside_sessions": len(open_sessions)},
    )
    return OutsideSessionsResponse(
        events=[
            OutsideSessionResponse(
                id=row.id,
                source=row.source,
                source_identifier=identifier,
                title=row.title,
                start_at=row.start_at,
                end_at=row.end_at,
            )
            for row, identifier in open_sessions
        ]
    )


@router.get("/api/calendar/outside-sessions/questions", response_model=OutsideQuestionsResponse)
def outside_session_questions(
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    outside: OutsideSessions = Depends(get_outside_sessions),
    audit: AuditService = Depends(get_audit_service),
) -> OutsideQuestionsResponse:
    """One "who is this?" per client, with who it might be."""
    ctx = outside.context(user.id)
    questions = outside.questions(user.id)
    audit.log(
        AuditAction.APPOINTMENT_LISTED,
        user,
        http_request,
        resource_type=ResourceType.APPOINTMENT,
        resource_id="outside-session-questions",
        changes={"questions": len(questions)},
    )
    return OutsideQuestionsResponse(
        count=len(questions),
        questions=[
            OutsideQuestionResponse(
                key=f"{q.source}|{q.source_identifier}",
                source=q.source,
                source_identifier=q.source_identifier,
                title=q.title,
                recurring=q.recurring,
                sessions=len(q.rows),
                next_start_at=q.next_start_at,
                match=series_match(q.match, ctx),
            )
            for q in questions
        ],
    )


def _checked_client(
    item: OutsideAnswer, patient_repo: PatientRepository, user_id: str
) -> Patient | None:
    if item.patient_id is None:
        return None
    patient = patient_repo.get(item.patient_id, user_id)
    if patient is None:
        raise NotFoundError("One of those clients could not be found")
    return patient


@router.post("/api/calendar/outside-sessions/answer", response_model=OutsideAnswerResponse)
def answer_outside_sessions(
    request: OutsideAnswerRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    outside: OutsideSessions = Depends(get_outside_sessions),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> OutsideAnswerResponse:
    """Say who each question is: a client, a new client, or not a client.

    Every answer is remembered, so the next event for the same client is
    booked without asking and a not-a-client is never asked about again. An
    answer for events already answered elsewhere does nothing.
    """
    # Every named client is checked before anything is written.
    existing = [_checked_client(item, patient_repo, user.id) for item in request.answers]
    ctx = outside.context(user.id)
    answered = 0
    booked: list[AnsweredAppointment] = []

    for item, client in zip(request.answers, existing, strict=True):
        if not outside.open_rows(user.id, item.source, item.source_identifier):
            continue
        answered += 1
        if item.not_a_client:
            rows = outside.answer(
                user.id, item.source, item.source_identifier, patient_id=None, ctx=ctx
            )
            audit.log(
                AuditAction.CLIENT_RESOLVED,
                user,
                http_request,
                resource_type=ResourceType.APPOINTMENT,
                resource_id="outside-sessions",
                changes={"source": item.source, "answer": "not_a_client", "sessions": len(rows)},
            )
            continue
        patient = client or _new_client(item, patient_repo, user, http_request, audit)
        rows = outside.answer(
            user.id, item.source, item.source_identifier, patient_id=patient.id, ctx=ctx
        )
        audit.log(
            AuditAction.CLIENT_RESOLVED,
            user,
            http_request,
            resource_type=ResourceType.PATIENT,
            resource_id=patient.id,
            changes={"source": item.source, "sessions": len(rows)},
        )
        for row in rows:
            if row.appointment_id is None:
                continue
            audit.log_appointment_action(
                AuditAction.APPOINTMENT_CREATED,
                user,
                http_request,
                row.appointment_id,
                patient_id=patient.id,
                changes={"source": item.source},
            )
            booked.append(
                AnsweredAppointment(outside_session_id=row.id, appointment_id=row.appointment_id)
            )

    return OutsideAnswerResponse(
        answered=answered, appointments_created=len(booked), appointments=booked
    )


def _new_client(
    item: OutsideAnswer,
    patient_repo: PatientRepository,
    user: User,
    http_request: Request,
    audit: AuditService,
) -> Patient:
    """A chart named as the calendar names them; the clinician can correct it.

    Nothing tries to split the name into first and last — a guess there is a
    wrong name on a chart.
    """
    now = utc_now()
    patient = patient_repo.create(
        Patient(
            id=str(uuid.uuid4()),
            first_name=(item.new_client_name or "").strip()[:_NAME_MAX] or UNNAMED_CLIENT,
            last_name="",
            created_at=now,
            updated_at=now,
            origin=PATIENT_ORIGIN,
        ),
        user.id,
    )
    audit.log_patient_action(AuditAction.PATIENT_CREATED, user, http_request, patient)
    return patient


@router.put("/api/google-calendar/follow-main-calendar", response_model=FollowMainCalendarResponse)
def set_follow_main_calendar(
    request: FollowMainCalendarRequest,
    user: User = Depends(require_baa_acceptance),
    service: GoogleCalendarService = Depends(get_google_calendar_service),
) -> FollowMainCalendarResponse:
    """Turn bringing in sessions from the main calendar on or off.

    Following reads events, so it needs the grant the "Look at my week" step
    asks for; turning it on without that grant is refused rather than stored
    as a choice that does nothing.
    """
    status_info = service.get_sync_status(user.id)
    if not status_info.get("connected"):
        raise NotFoundError("Google Calendar not connected")
    if request.enabled and not status_info.get("import_granted"):
        raise BadRequestError("Following your calendar needs access to read its events")
    service.set_follow_main_calendar(user.id, follow=request.enabled)
    return FollowMainCalendarResponse(follow_main_calendar=request.enabled)
