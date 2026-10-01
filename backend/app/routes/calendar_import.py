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

import logging
import uuid
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    MAX_HORIZON_DAYS,
    ImportProposal,
    ProposedSeries,
)
from ..calendar_providers.source_identity import (
    GOOGLE_CALENDAR_SOURCE,
    answer_scope,
    calendar_source_identifier,
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
    NAME_ONLY,
    MatchContext,
    MatchResult,
    PatientHint,
    match_patient,
    normalize,
    remember_match,
    remember_not_a_client,
)
from ..patients.seen_by import SeenBy
from ..repositories import (
    PatientRepository,
    UserRepository,
    get_user_repository,
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
    configured_timezone,
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
PATIENT_ORIGIN = "calendar_import"
#: The source a confirmed series is remembered under.
MATCH_SOURCE = GOOGLE_CALENDAR_SOURCE
#: Refusing to chart or book a client of the practice the caller doesn't see.
#: The review already said who does; this is for a request that skipped it.
#: Raised for one answer or for a batch of series, so it names no count.
SEEN_BY_SOMEONE_ELSE = (
    "Already a client of the practice. Ask their clinician or your practice owner for access."
)
#: Refusing to confirm when the calendar the series are on can't be named:
#: what is remembered is keyed by it, and reading it needs the grant.
CALENDAR_NOT_READABLE = "Reading the calendar needs access that isn't granted"


def get_patient_source_mapping_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientSourceMappingRepository:
    """Remembered series, scoped to the tenant's database."""
    return _mapping_repo_factory()


def _source_identifier(series: ProposedSeries) -> str:
    """How a confirmed series is remembered — the same way following does.

    See ``calendar_source_identifier``: the provider's series id, or a digest
    of the series' shape (title, weekday and local start time).
    """
    return calendar_source_identifier(
        series.series_id, series.summary, series.weekday, series.local_start_time
    )


def series_match(result: MatchResult, ctx: MatchContext, seen_by: SeenBy) -> SeriesMatchResponse:
    """What the clinician is shown about who a series is.

    A certain match to a chart they cannot see is shown as the clinicians who
    do see it, and nothing else: not the chart's name, not its birthday. A
    possible match they cannot see is left out, because naming it would say
    that a colleague sees someone by that name when nobody is sure it is this
    person.
    """
    if result.patient_id and not result.visible:
        candidate = ctx.candidate(result.patient_id)
        return SeriesMatchResponse(seen_by=seen_by.names(candidate) if candidate else [])

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

    if result.patient_id and result.evidence in NAME_ONLY:
        # A name alone is never shown as settled: the chart is offered,
        # preselected, beside "New client", so the therapist can say no.
        return SeriesMatchResponse(
            possible=choices([result.patient_id]), suggested_patient_id=result.patient_id
        )
    if result.patient_id:
        return SeriesMatchResponse(patient=choices([result.patient_id])[0])
    return SeriesMatchResponse(possible=choices(result.visible_possible_ids))


def _series_hint(summary: str, identifier: str | None, scope: str | None) -> PatientHint:
    """What a series says about its client. ``scope`` is the main calendar's, when known."""
    return PatientHint(
        full_name=summary, source=MATCH_SOURCE, source_identifier=identifier, scope=scope
    )


def _seen_by_someone_else(item: ConfirmImportSeries, ctx: MatchContext, scope: str) -> bool:
    """Whether confirming this series would book or chart a colleague's client."""
    if item.patient_id:
        named = ctx.candidate(item.patient_id)
        return named is not None and not named.visible
    result = match_patient(_series_hint(item.display_name, item.source_identifier, scope), ctx)
    return result.patient_id is not None and not result.visible


def _main_calendar_scope(service: GoogleCalendarService, user_id: str) -> str | None:
    """Whose answers a scan of the main calendar consults: that calendar's.

    Asked of the provider only when no read has learned the id already.
    None when the calendar can't be read, which a scan that just read it
    never sees; a confirm refuses, since what it remembers is keyed by it.
    """
    main = service.known_main_calendar_id(user_id) or service.main_calendar_id(user_id)
    return answer_scope(MATCH_SOURCE, main, user_id)


def _required_main_calendar_scope(service: GoogleCalendarService, user_id: str) -> str:
    scope = _main_calendar_scope(service, user_id)
    if scope is None:
        raise BadRequestError(CALENDAR_NOT_READABLE)
    return scope


def _to_response(
    proposal: ImportProposal, ctx: MatchContext, seen_by: SeenBy, scope: str | None
) -> ImportProposalResponse:
    series_out: list[ProposedSeriesResponse] = []
    for series in proposal.series:
        identifier = _source_identifier(series)
        result = match_patient(_series_hint(series.summary, identifier, scope), ctx)
        if result.evidence == "not_a_client":
            # The therapist already said this series is not a client.
            continue
        match = series_match(result, ctx, seen_by)
        series_out.append(
            ProposedSeriesResponse(
                candidate_key=series.candidate_key,
                summary=series.summary,
                source_identifier=identifier,
                match=match,
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
                # A colleague's client is never imported from here.
                preselected=series.preselected and match.seen_by is None,
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
    user_repo: UserRepository = Depends(get_user_repository),
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

    The calendar is read in the clinician's own zone when they have set one,
    and in the browser's (``timezone``) only when they have not. A series
    without a provider id is remembered by its weekday and start time, so
    the import and the sessions Pablo later follows must read it in the same
    zone or they remember the same client twice.
    """
    if not _is_valid_gcal_redirect_uri(redirect_uri):
        raise BadRequestError("Invalid redirect_uri")

    try:
        proposal = service.scan_for_practice_import(
            ctx.user_id,
            lookback_days=lookback_days,
            horizon_days=horizon_days,
            timezone=configured_timezone(user_repo, ctx.user_id) or timezone,
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

    # What is remembered about this calendar is keyed by its id, which the
    # scan learns after the read has proved the grant.
    scope = _main_calendar_scope(service, ctx.user_id)
    main_calendar_id = scope.removeprefix("calendar:") if scope else None
    response = _to_response(
        proposal,
        MatchContext.for_practice(
            ctx.user_id, patient_repo, mappings, main_calendar_id=main_calendar_id
        ),
        SeenBy(user_repo),
        scope,
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
            "series_seen_by_others": sum(1 for s in response.series if s.match.seen_by is not None),
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


def _already_in_slot(
    scheduling: SchedulingService,
    user_id: str,
    patient_id: str,
    start: datetime,
    timezone: str,
    now: datetime,
) -> bool:
    """Whether this patient already has this series booked.

    That is an appointment ahead in the same slot — same weekday and start
    time in the series' zone — that is either part of a recurring series or
    falls on the first date this one would book. Anything else the patient
    has ahead, such as a one-off intake, is not this series.
    """
    try:
        zone: tzinfo = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = UTC
    first = start.astimezone(zone)
    slot = (first.weekday(), first.hour, first.minute)
    for appt in scheduling.list_patient_appointments(user_id, patient_id):
        at = appt.start_at if appt.start_at.tzinfo else appt.start_at.replace(tzinfo=UTC)
        if at <= now or appt.status == AppointmentStatus.CANCELLED:
            continue
        local = at.astimezone(zone)
        if (local.weekday(), local.hour, local.minute) != slot:
            continue
        if appt.recurring_appointment_id or local.date() == first.date():
            return True
    return False


@router.post("/confirm", response_model=ConfirmImportResponse)
def confirm_calendar_import(
    http_request: Request,
    request: ConfirmImportRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    mappings: PatientSourceMappingRepository = Depends(get_patient_source_mapping_repository),
    scheduling: SchedulingService = Depends(get_scheduling_service),
    user_repo: UserRepository = Depends(get_user_repository),
    audit: AuditService = Depends(get_audit_service),
    owner_tz: tzinfo = Depends(get_owner_timezone),
    service: GoogleCalendarService = Depends(get_google_calendar_service),
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

    A series an existing patient already has booked in the same slot is not
    booked again — that is the same series imported twice — and is reported
    in ``already_scheduled``.

    ``not_clients`` names series the therapist marked as not a client. They
    are remembered as such, so later scans leave them out.

    A series that is a client of the practice the therapist does not see is
    refused, whether it names that chart or asks for a new one: a new one
    would be a second chart for the same person. The scan already said so.

    Series are booked in the clinician's own zone when they have set one.
    """
    now = utc_now()
    zone = configured_timezone(user_repo, ctx.user_id)
    # Every answer given here is the main calendar's, under its id.
    scope = _required_main_calendar_scope(service, ctx.user_id)
    match_ctx = MatchContext.for_practice(
        ctx.user_id, patient_repo, mappings, main_calendar_id=scope.removeprefix("calendar:")
    )

    # Everything is checked before anything is written.
    # Compared the way they are remembered, so case or spacing can't hide a clash.
    confirming = {
        normalize(item.source_identifier) for item in request.series if item.source_identifier
    }
    if confirming & {normalize(identifier) for identifier in request.not_clients}:
        raise BadRequestError("A series can't be both a client and not a client")
    checked: list[tuple[ConfirmImportSeries, RecurrenceFrequency, Patient | None]] = []
    for requested in request.series:
        item = requested.model_copy(update={"timezone": zone}) if zone else requested
        frequency = _validate(item, now)
        if _seen_by_someone_else(item, match_ctx, scope):
            raise BadRequestError(SEEN_BY_SOMEONE_ELSE)
        existing = None
        if item.patient_id:
            existing = patient_repo.get(item.patient_id, ctx.user_id)
            if existing is None:
                raise BadRequestError("One of those clients could not be found")
        checked.append((item, frequency, existing))

    for identifier in request.not_clients:
        remember_not_a_client(MATCH_SOURCE, identifier, match_ctx, scope=scope)

    confirmed: list[ConfirmedSeriesResponse] = []
    skipped: list[str] = []
    already_scheduled: list[str] = []
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
            remember_match(MATCH_SOURCE, item.source_identifier, patient.id, match_ctx, scope=scope)

        if existing is not None and _already_in_slot(
            scheduling, ctx.user_id, patient.id, start, item.timezone, now
        ):
            already_scheduled.append(item.candidate_key)
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
        already_scheduled=already_scheduled,
    )
