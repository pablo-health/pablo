# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Read a connected calendar once and set a practice up from what it says.

Two steps, deliberately separate. A scan proposes and returns; nothing it
read is written down, so a therapist who doesn't like the proposal can
walk away and leave no trace of it. A confirmation takes back the subset
they agreed to and creates those patients and appointments.

Event titles carry client names. They travel to the person who owns them
and nowhere else: not to a log line, not to an error message, not to a
metric label, and not to any table.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request

from ..api_errors import BadRequestError
from ..auth.service import (
    TenantContext,
    get_tenant_context,
    require_active_subscription,
    require_baa_acceptance,
)
from ..calendar_providers.capabilities import CalendarCapability
from ..calendar_providers.practice_import import (
    DEFAULT_HORIZON_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    ImportProposal,
)
from ..models import AuditAction, User
from ..models.audit import ResourceType
from ..models.patient import Patient
from ..models.scheduling import (
    ConfirmedSeriesResponse,
    ConfirmImportRequest,
    ConfirmImportResponse,
    ConfirmImportSeries,
    ImportConsentRequiredResponse,
    ImportProposalResponse,
    ProposedSeriesResponse,
)
from ..repositories import PatientRepository  # noqa: TC001 — FastAPI resolves at runtime
from ..scheduling_engine.exceptions import (
    AppointmentConflictError,
    InvalidAppointmentError,
    InvalidRecurrenceError,
)
from ..scheduling_engine.models.appointment import RecurrenceFrequency
from ..scheduling_engine.services.scheduling import (  # noqa: TC001 — resolved at runtime
    SchedulingService,
)
from ..services import AuditService, get_audit_service
from ..services.google_calendar_service import (
    CalendarImportNotAuthorizedError,
    GoogleCalendarService,
)
from ..utcnow import utc_now
from .patients import get_patient_repository
from .scheduling import (
    _is_valid_gcal_redirect_uri,
    get_google_calendar_service,
    get_scheduling_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/calendar/import",
    tags=["calendar-import"],
    dependencies=[Depends(require_active_subscription)],
)

MAX_LOOKBACK_DAYS = 400
MAX_HORIZON_DAYS = 400
PATIENT_ORIGIN = "calendar_import"


def _to_response(proposal: ImportProposal) -> ImportProposalResponse:
    return ImportProposalResponse(
        series=[
            ProposedSeriesResponse(
                candidate_key=series.candidate_key,
                summary=series.summary,
                weekday=series.weekday,
                local_start_time=series.local_start_time,
                duration_minutes=series.duration_minutes,
                cadence=series.cadence.value,
                occurrences_in_window=series.occurrences_in_window,
                occurrences_ahead=series.occurrences_ahead,
                first_future_start=series.first_future_start,
                last_seen=series.last_seen,
                recurrence_rule=series.recurrence_rule,
                status=series.status.value,
                confidence=series.confidence,
                preselected=series.preselected,
            )
            for series in proposal.series
        ],
        left_alone=proposal.left_alone,
        events_read=proposal.events_read,
        partial=proposal.partial,
        lookback_days=proposal.lookback_days,
        horizon_days=proposal.horizon_days,
        timezone=proposal.timezone,
    )


@router.post(
    "/scan",
    response_model=ImportProposalResponse | ImportConsentRequiredResponse,
)
def scan_calendar_for_practice(
    http_request: Request,
    redirect_uri: str = Query(..., description="Where to return after granting event access"),
    lookback_days: int = Query(DEFAULT_LOOKBACK_DAYS, ge=7, le=MAX_LOOKBACK_DAYS),
    horizon_days: int = Query(DEFAULT_HORIZON_DAYS, ge=7, le=MAX_HORIZON_DAYS),
    timezone: str = Query("UTC", max_length=64, description="Zone the calendar reads in"),
    ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    service: GoogleCalendarService = Depends(get_google_calendar_service),
    audit: AuditService = Depends(get_audit_service),
) -> ImportProposalResponse | ImportConsentRequiredResponse:
    """Propose the practice the connected calendar describes.

    Reading event content is its own permission, asked for here rather than
    at connect, so a therapist who never imports never grants it. A
    connection that doesn't hold it gets the consent URL back — that is the
    expected first answer, not a failure.

    Nothing is written. The proposal is returned and forgotten.
    """
    if not _is_valid_gcal_redirect_uri(redirect_uri):
        raise BadRequestError("Invalid redirect_uri")

    try:
        proposal = service.scan_for_practice_import(
            ctx.user_id,
            lookback_days=lookback_days,
            horizon_days=horizon_days,
            timezone=timezone,
        )
    except CalendarImportNotAuthorizedError:
        auth_url = service.get_auth_url(
            ctx.user_id,
            redirect_uri,
            capabilities=[CalendarCapability.IMPORT],
        )
        return ImportConsentRequiredResponse(auth_url=auth_url)
    except ValueError as exc:
        raise BadRequestError("Could not read the calendar with those settings") from exc

    # The proposal itself carries client names; the audit record carries
    # what was read and how much, which is the disclosure worth recording.
    audit.log(
        AuditAction.ICAL_CALENDAR_SYNCED,
        user,
        http_request,
        resource_type=ResourceType.APPOINTMENT,
        resource_id="calendar-import-scan",
        changes={
            "events_read": proposal.events_read,
            "series_proposed": len(proposal.series),
            "left_alone": proposal.left_alone,
            "partial": proposal.partial,
            "lookback_days": proposal.lookback_days,
            "horizon_days": proposal.horizon_days,
        },
    )
    return _to_response(proposal)


def _validate(item: ConfirmImportSeries, now: datetime) -> RecurrenceFrequency:
    """Check one confirmation before anything is written for it."""
    try:
        frequency = RecurrenceFrequency(item.cadence)
    except ValueError as exc:
        raise BadRequestError("Unsupported cadence") from exc

    start = item.start_at if item.start_at.tzinfo else item.start_at.replace(tzinfo=UTC)
    if start <= now:
        # The past supplies the pattern; only what is still ahead becomes a
        # record. Importing a session that already happened would invent a
        # clinical history nobody kept.
        raise BadRequestError("A series can only be imported from an occurrence still to come")
    return frequency


@router.post("/confirm", response_model=ConfirmImportResponse)
def confirm_calendar_import(
    http_request: Request,
    request: ConfirmImportRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    scheduling: SchedulingService = Depends(get_scheduling_service),
    audit: AuditService = Depends(get_audit_service),
) -> ConfirmImportResponse:
    """Create patients and recurring appointments for the confirmed series.

    Only what is in this request is created. A series the therapist left
    unchecked leaves no trace, because the proposal it came from was never
    stored in the first place.

    The calendar's wording becomes the patient's initial name as-is. Nothing
    tries to split it into a first and last name — a guess there is a wrong
    name on a chart, and the therapist can correct it in seconds.
    """
    now = utc_now()

    confirmed: list[ConfirmedSeriesResponse] = []
    skipped: list[str] = []
    patients_created = 0
    appointments_created = 0

    for item in request.series:
        frequency = _validate(item, now)
        start = item.start_at if item.start_at.tzinfo else item.start_at.replace(tzinfo=UTC)

        patient = patient_repo.create(
            Patient(
                id=str(uuid.uuid4()),
                first_name=item.display_name,
                last_name="",
                created_at=now,
                updated_at=now,
                origin=PATIENT_ORIGIN,
            ),
            ctx.user_id,
        )
        patients_created += 1
        audit.log_patient_action(AuditAction.PATIENT_CREATED, user, http_request, patient)

        try:
            appointments = scheduling.create_recurring(
                ctx.user_id,
                data={
                    "patient_id": patient.id,
                    "title": item.display_name,
                    "start_at": start.isoformat(),
                    "end_at": (start + timedelta(minutes=item.duration_minutes)).isoformat(),
                    "duration_minutes": item.duration_minutes,
                },
                recurrence={
                    "frequency": frequency.value,
                    "timezone": item.timezone,
                    "count": item.occurrences,
                },
            )
        except (AppointmentConflictError, InvalidAppointmentError, InvalidRecurrenceError):
            # The chart stands even when its schedule doesn't: the therapist
            # books around whatever collided. Keys only — never the title.
            logger.warning("Could not create a recurring series during a calendar import")
            skipped.append(item.candidate_key)
            continue

        appointments_created += len(appointments)
        confirmed.append(
            ConfirmedSeriesResponse(
                candidate_key=item.candidate_key,
                patient_id=patient.id,
                appointments_created=len(appointments),
            )
        )

    return ConfirmImportResponse(
        confirmed=confirmed,
        patients_created=patients_created,
        appointments_created=appointments_created,
        skipped=skipped,
    )
