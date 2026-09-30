# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Read a connected calendar once and set a practice up from what it says.

Two steps, deliberately separate. A scan proposes and returns; nothing it
read is written down, so a therapist who doesn't like the proposal can
walk away and leave no trace of it. A confirmation takes back the subset
they agreed to and creates those patients and appointments.

A series can be a client the practice already has. The scan says which
patient each series is when that is certain, or which it might be, and the
confirmation either names an existing patient or asks for a new one. Every
confirmed series is remembered against its patient, so a second import of
the same calendar lands on the same charts instead of making new ones. A
series the therapist marks as not a client is remembered too, and later
scans leave it out.

Event titles carry client names. They travel to the person who owns them
and nowhere else: not to a log line, not to an error message, not to a
metric label, and not to any table other than the chart a therapist
confirms. A series is remembered by the provider's series id, or by a
digest of its title, weekday and start time, never by the title itself.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta, tzinfo

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
    ProposedSeries,
)
from ..models import AuditAction, User
from ..models.audit import ResourceType
from ..models.patient import Patient
from ..models.scheduling import (
    BusyWindowResponse,
    BusyWindowsNotGrantedResponse,
    BusyWindowsResponse,
    ConfirmedSeriesResponse,
    ConfirmImportRequest,
    ConfirmImportResponse,
    ConfirmImportSeries,
    ImportConsentRequiredResponse,
    ImportPatientChoice,
    ImportProposalResponse,
    ProposedSeriesResponse,
    SeriesMatchResponse,
)
from ..patients.matching import (
    MatchContext,
    MatchResult,
    PatientHint,
    match_patient,
    normalize,
    remember_match,
    remember_not_a_client,
)
from ..repositories import (
    PatientRepository,
)
from ..repositories import (
    get_patient_source_mapping_repository as _mapping_repo_factory,
)
from ..repositories.patient_source_mapping import (  # noqa: TC001 — FastAPI resolves at runtime
    PatientSourceMappingRepository,
)
from ..scheduling_engine.exceptions import (
    AppointmentConflictError,
    InvalidAppointmentError,
    InvalidRecurrenceError,
    RuleViolationError,
)
from ..scheduling_engine.models.appointment import AppointmentStatus, RecurrenceFrequency
from ..scheduling_engine.services.scheduling import (  # noqa: TC001 — resolved at runtime
    SchedulingService,
)
from ..services import AuditService, get_audit_service
from ..services.google_calendar_service import (
    CalendarBusyNotAuthorizedError,
    CalendarImportNotAuthorizedError,
    GoogleCalendarService,
)
from ..utcnow import utc_now
from .patients import get_patient_repository
from .scheduling import (
    _is_valid_gcal_redirect_uri,
    get_google_calendar_service,
    get_owner_timezone,
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
#: The source a confirmed series is remembered under.
MATCH_SOURCE = "google_calendar"


def get_patient_source_mapping_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientSourceMappingRepository:
    """Remembered series, scoped to the tenant's database."""
    return _mapping_repo_factory()


def _source_identifier(series: ProposedSeries) -> str:
    """How a confirmed series is remembered.

    The provider's series id when the scan saw one. Otherwise a digest of the
    series' shape — normalised title, weekday and local start time, the same
    things the scan groups a hand-entered series by. The title alone is not
    enough: two "Therapy Session" series on Monday and Thursday are two
    clients. The digest is stable across scans and keeps the title out of
    the table.
    """
    if series.series_id:
        return f"series:{series.series_id}"
    shape = f"{normalize(series.summary)}|{series.weekday}|{series.local_start_time}"
    return f"shape:{hashlib.sha256(shape.encode()).hexdigest()[:32]}"


def _series_match(result: MatchResult, ctx: MatchContext) -> SeriesMatchResponse:
    def choices(patient_ids: list[str]) -> list[ImportPatientChoice]:
        # Two charts can share a name, so a date of birth, when the chart has
        # one, is what lets the therapist tell them apart.
        return [
            ImportPatientChoice(
                patient_id=c.id,
                display_name=c.display_name,
                date_of_birth=c.date_of_birth,
            )
            for c in (ctx.candidate(pid) for pid in patient_ids)
            if c is not None
        ]

    if result.patient_id:
        return SeriesMatchResponse(patient=choices([result.patient_id])[0])
    return SeriesMatchResponse(possible=choices(result.possible_ids))


def _to_response(proposal: ImportProposal, ctx: MatchContext) -> ImportProposalResponse:
    series_out: list[ProposedSeriesResponse] = []
    for series in proposal.series:
        identifier = _source_identifier(series)
        hint = PatientHint(
            full_name=series.summary, source=MATCH_SOURCE, source_identifier=identifier
        )
        result = match_patient(hint, ctx)
        if result.evidence == "not_a_client":
            # The therapist already said this series is not a client.
            continue
        series_out.append(
            ProposedSeriesResponse(
                candidate_key=series.candidate_key,
                summary=series.summary,
                source_identifier=identifier,
                match=_series_match(result, ctx),
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
        )
    return ImportProposalResponse(
        series=series_out,
        left_alone=proposal.left_alone,
        events_read=proposal.events_read,
        partial=proposal.partial,
        lookback_days=proposal.lookback_days,
        horizon_days=proposal.horizon_days,
        timezone=proposal.timezone,
    )


@router.get(
    "/busy",
    response_model=BusyWindowsResponse | BusyWindowsNotGrantedResponse,
)
def get_calendar_busy_windows(
    start: datetime = Query(..., description="Start of the window, inclusive"),
    end: datetime = Query(..., description="End of the window, exclusive"),
    ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),  # noqa: ARG001 — dependency gates access
    service: GoogleCalendarService = Depends(get_google_calendar_service),
) -> BusyWindowsResponse | BusyWindowsNotGrantedResponse:
    """Busy/free blocks over a window, before anything has been scanned.

    Renders the "anonymous shapes" week grid a scan later sorts. BUSY is
    opt-in at connect and never asked for again, so a connection that
    declined it gets a typed "not granted" answer, not a 500 — the caller
    falls back to building the grid from a scan response instead.
    """
    if end <= start:
        raise BadRequestError("end must be after start")
    try:
        windows = service.list_busy_windows(ctx.user_id, start, end)
    except CalendarBusyNotAuthorizedError:
        return BusyWindowsNotGrantedResponse()
    return BusyWindowsResponse(
        windows=[BusyWindowResponse(start=window.start, end=window.end) for window in windows]
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
    patient_repo: PatientRepository = Depends(get_patient_repository),
    mappings: PatientSourceMappingRepository = Depends(get_patient_source_mapping_repository),
    audit: AuditService = Depends(get_audit_service),
) -> ImportProposalResponse | ImportConsentRequiredResponse:
    """Propose the practice the connected calendar describes.

    Reading event content is its own permission, asked for here rather than
    at connect, so a therapist who never imports never grants it. A
    connection that doesn't hold it gets the consent URL back — that is the
    expected first answer, not a failure.

    Nothing is written. The proposal is returned and forgotten. Each series
    carries which existing patient it is, or might be, so the therapist can
    say before anything is created.
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

    response = _to_response(
        proposal, MatchContext.for_clinician(ctx.user_id, patient_repo, mappings)
    )

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
            "series_proposed": len(response.series),
            "series_not_clients": len(proposal.series) - len(response.series),
            "left_alone": proposal.left_alone,
            "partial": proposal.partial,
            "lookback_days": proposal.lookback_days,
            "horizon_days": proposal.horizon_days,
            "series_matched": sum(1 for s in response.series if s.match.patient),
        },
    )
    return response


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


def _has_appointments_ahead(
    scheduling: SchedulingService, user_id: str, patient_id: str, now: datetime
) -> bool:
    return any(
        appt.start_at > now and appt.status != AppointmentStatus.CANCELLED
        for appt in scheduling.list_patient_appointments(user_id, patient_id)
    )


@router.post("/confirm", response_model=ConfirmImportResponse)
def confirm_calendar_import(
    http_request: Request,
    request: ConfirmImportRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    mappings: PatientSourceMappingRepository = Depends(get_patient_source_mapping_repository),
    scheduling: SchedulingService = Depends(get_scheduling_service),
    audit: AuditService = Depends(get_audit_service),
    owner_tz: tzinfo = Depends(get_owner_timezone),
) -> ConfirmImportResponse:
    """Create recurring appointments for the confirmed series, and any new patients.

    Only what is in this request is created. A series the therapist left
    unchecked leaves no trace, because the proposal it came from was never
    stored in the first place.

    A series that names an existing patient is scheduled on that chart. One
    that doesn't gets a new patient, and the calendar's wording becomes the
    patient's initial name as-is. Nothing tries to split it into a first and
    last name — a guess there is a wrong name on a chart, and the therapist
    can correct it in seconds. Either way the series is remembered against
    its patient, so the next scan shows it as that client.

    An existing patient who already has appointments ahead is not scheduled
    again: that is the same series imported twice.

    ``not_clients`` names series the therapist marked as not a client. They
    are remembered as such, so later scans leave them out.
    """
    now = utc_now()

    # Everything is checked before anything is written.
    # Compared the way they are remembered, so case or spacing can't hide a clash.
    confirming = {
        normalize(item.source_identifier) for item in request.series if item.source_identifier
    }
    if confirming & {normalize(identifier) for identifier in request.not_clients}:
        raise BadRequestError("A series can't be both a client and not a client")
    checked: list[tuple[ConfirmImportSeries, RecurrenceFrequency, Patient | None]] = []
    for item in request.series:
        frequency = _validate(item, now)
        existing = None
        if item.patient_id:
            existing = patient_repo.get(item.patient_id, ctx.user_id)
            if existing is None:
                raise BadRequestError("One of those clients could not be found")
        checked.append((item, frequency, existing))

    match_ctx = MatchContext.for_clinician(ctx.user_id, patient_repo, mappings)
    for identifier in request.not_clients:
        remember_not_a_client(MATCH_SOURCE, identifier, match_ctx)

    confirmed: list[ConfirmedSeriesResponse] = []
    skipped: list[str] = []
    patients_created = 0
    appointments_created = 0

    for item, frequency, existing in checked:
        start = item.start_at if item.start_at.tzinfo else item.start_at.replace(tzinfo=UTC)

        if existing is None:
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
        else:
            patient = existing
            audit.log(
                AuditAction.CLIENT_RESOLVED,
                user,
                http_request,
                resource_type=ResourceType.PATIENT,
                resource_id=patient.id,
                changes={"source": MATCH_SOURCE},
            )

        if item.source_identifier:
            remember_match(MATCH_SOURCE, item.source_identifier, patient.id, match_ctx)

        if existing is not None and _has_appointments_ahead(
            scheduling, ctx.user_id, patient.id, now
        ):
            confirmed.append(
                ConfirmedSeriesResponse(
                    candidate_key=item.candidate_key, patient_id=patient.id, appointments_created=0
                )
            )
            continue

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
                # Working hours and the other rules read in the owner's own
                # zone, as a booking made anywhere else does. Left at the
                # UTC default, a 2pm Eastern session checked as 18:00 and was
                # refused as outside 9-5.
                tz=owner_tz,
            )
        except (
            AppointmentConflictError,
            InvalidAppointmentError,
            InvalidRecurrenceError,
            # A series outside the practice's hours or other booking rules is
            # skipped like a clash, not a 500 that loses the whole import.
            RuleViolationError,
        ):
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
