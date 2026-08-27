# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Public booking endpoints — the unauthenticated surface behind booking links.

Mounted only when ``PUBLIC_BOOKING_ENABLED`` is on (see
docs/design/public-booking.md). Every endpoint resolves a slug through
:func:`get_public_booking_context`, which rate-limits by IP, enters the
owning practice's tenant schema, and arms RLS as the link's owner — so
the repositories behave exactly as if the owner were making the calls.

The surface is deliberately narrow: a link's display card, free slots
for one date, and a booking POST. Nothing readable comes back out — no
patient data, no existing appointments, no internal ids.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..repositories.booking_link import BookingLinkRepository
    from ..repositories.google_calendar_token import GoogleCalendarTokenRepository
    from ..repositories.patient import PatientRepository
    from ..repositories.user import UserRepository
    from ..scheduling_engine.repositories.appointment import AppointmentRepository
    from ..scheduling_engine.repositories.availability_rule import AvailabilityRuleRepository

from fastapi import APIRouter, Depends, Query, Request, status

from ..api_errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from ..auth.route_access import AccessLevel, resolve_access_level
from ..auth.route_security import truly_public
from ..db import arm_current_user_id, get_db_session, set_tenant_schema
from ..models import Patient, User
from ..models.audit import ACTOR_TYPE_ANONYMOUS, AuditAction
from ..models.booking_link import (
    BookingLink,
    CreatePublicBookingRequest,
    PublicBookingConfirmation,
    PublicBookingLinkResponse,
)
from ..models.scheduling import FreeSlotsResponse, TimeSlotResponse
from ..rate_limit import (
    require_public_booking_rate_limit,
    require_public_booking_write_rate_limit,
)
from ..repositories import (
    get_appointment_repository,
    get_availability_rule_repository,
    get_booking_link_repository,
    get_google_calendar_token_repository,
    get_patient_repository,
    get_user_repository,
)
from ..scheduling_engine.exceptions import InvalidAppointmentError
from ..scheduling_engine.services.availability import AvailabilityEngine
from ..scheduling_engine.services.scheduling import SchedulingService
from ..services import AuditService, get_audit_service
from ..services.google_calendar_service import GoogleCalendarService
from ..settings import get_settings
from ..utcnow import utc_now
from .scheduling import _sync_appointment_to_google

logger = logging.getLogger(__name__)

# truly_public: anonymous internet traffic is the point of a booking link —
# the surface is narrowed by design (display card, free slots, one POST) and
# every request is IP-rate-limited before the slug even resolves.
router = APIRouter(
    tags=["public-booking"],
    dependencies=[Depends(truly_public), Depends(require_public_booking_rate_limit)],
)

# How far ahead a public booker may look and book.
MAX_ADVANCE_DAYS = 60

_LINK_NOT_FOUND = "This booking link does not exist or is no longer available."


@dataclass
class PublicBookingContext:
    """A resolved booking link with its owner, tenant-armed and ready."""

    link: BookingLink
    owner: User


def get_public_booking_context(
    slug: str,
    link_repo: BookingLinkRepository = Depends(get_booking_link_repository),
    user_repo: UserRepository = Depends(get_user_repository),
) -> PublicBookingContext:
    """Resolve a slug and enter its practice context.

    Missing, inactive, and misconfigured links are an identical 404 —
    the public surface offers no oracle for "exists but off". After
    this dependency runs, the request session is scoped to the owning
    practice's schema with RLS armed as the link's owner.
    """
    link = link_repo.get_by_slug(slug)
    if link is None or not link.is_active:
        raise NotFoundError(_LINK_NOT_FOUND)

    try:
        session = get_db_session()
    except RuntimeError:
        # In-memory mode (unit tests, DB-less dev) has no request session
        # to scope; multi-tenancy and RLS only exist on the Postgres path.
        session = None
    if session is not None:
        if get_settings().multi_tenancy_enabled:
            if link.practice_schema is None:
                raise NotFoundError(_LINK_NOT_FOUND)
            set_tenant_schema(session, link.practice_schema)
        arm_current_user_id(session, link.user_id)

    owner = user_repo.get(link.user_id)
    if owner is None:
        raise NotFoundError(_LINK_NOT_FOUND)
    return PublicBookingContext(link=link, owner=owner)


def get_public_availability_engine(
    rule_repo: AvailabilityRuleRepository = Depends(get_availability_rule_repository),
    appt_repo: AppointmentRepository = Depends(get_appointment_repository),
) -> AvailabilityEngine:
    return AvailabilityEngine(rule_repo, appt_repo)


def get_public_scheduling_service(
    appt_repo: AppointmentRepository = Depends(get_appointment_repository),
) -> SchedulingService:
    return SchedulingService(appt_repo)


def get_public_gcal_service(
    token_repo: GoogleCalendarTokenRepository = Depends(get_google_calendar_token_repository),
    appt_repo: AppointmentRepository = Depends(get_appointment_repository),
) -> GoogleCalendarService:
    settings = get_settings()
    return GoogleCalendarService(
        token_repo=token_repo,
        appointment_repo=appt_repo,
        client_id=settings.google_calendar_client_id,
        client_secret=settings.google_calendar_client_secret.get_secret_value(),
    )


_BOOKING_CLOSED = (
    "This practice isn't accepting online bookings right now. Please contact them directly."
)


def _require_owner_may_accept_bookings(owner: User) -> None:
    """Refuse the booking write when the owner's practice may not write.

    The public surface has no request identity, so the standard
    ``require_active_subscription`` gate — which resolves the *caller* —
    cannot apply here. Resolve the link's owner instead: a practice
    wound down to read-only or no access keeps its card and slots
    readable (matching how read-intent routes behave everywhere else),
    but stops accumulating new charts and appointments through a link
    it can no longer service.

    The refusal names no billing state: to a booker this is
    indistinguishable from any other reason a link stops taking
    bookings.
    """
    settings = get_settings()
    if not settings.is_saas:
        return

    try:
        # SaaS-overlay-only module; the same late import
        # require_active_subscription does.
        from ..routes.subscription import (  # type: ignore[import-not-found]
            _fetch_subscription,
        )
    except ImportError:
        # A build with no subscription registry at all, configured for a
        # hosted edition (a self-host that set PABLO_EDITION, and the OSS
        # test harness). There is no subscription to read, so there is
        # nothing to enforce — and 500-ing every booking would be a worse
        # answer than letting a deployment that does not bill anyone book.
        return

    sub = _fetch_subscription(owner.email, settings)
    if not sub:
        # No subscription record — might be mid-provisioning; let through,
        # matching require_active_subscription.
        return
    if resolve_access_level(sub) is not AccessLevel.FULL:
        raise ForbiddenError(_BOOKING_CLOSED)


def _parse_booking_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as e:
        raise BadRequestError("date must be YYYY-MM-DD") from e
    today = utc_now().date()
    if parsed < today or parsed > today + timedelta(days=MAX_ADVANCE_DAYS):
        raise BadRequestError(f"date must be within the next {MAX_ADVANCE_DAYS} days")
    return parsed


@router.get("/api/public/booking-links/{slug}", response_model=PublicBookingLinkResponse)
def get_public_booking_link(
    ctx: PublicBookingContext = Depends(get_public_booking_context),
) -> PublicBookingLinkResponse:
    """The link's public display card — everything a visitor may see."""
    return PublicBookingLinkResponse(
        slug=ctx.link.slug,
        host_name=ctx.link.host_name,
        title=ctx.link.title,
        description=ctx.link.description,
        duration_minutes=ctx.link.duration_minutes,
    )


@router.get("/api/public/booking-links/{slug}/slots", response_model=FreeSlotsResponse)
def get_public_free_slots(
    date_param: str = Query(..., alias="date", description="Date (YYYY-MM-DD)"),
    ctx: PublicBookingContext = Depends(get_public_booking_context),
    engine: AvailabilityEngine = Depends(get_public_availability_engine),
) -> FreeSlotsResponse:
    """Free slots for one date, at the link's fixed duration.

    Times are the practice's local wall-clock, matching the engine's
    convention (the ``Z`` suffix is cosmetic — see the design doc).
    """
    parsed = _parse_booking_date(date_param)
    result = engine.get_free_slots(ctx.link.user_id, parsed.isoformat(), ctx.link.duration_minutes)
    return FreeSlotsResponse(
        date=parsed.isoformat(),
        duration_minutes=ctx.link.duration_minutes,
        slots=[TimeSlotResponse(start=s.start, end=s.end) for s in result.slots],
        total=len(result.slots),
        configured=result.configured,
    )


def _booking_provenance(ctx: PublicBookingContext) -> dict[str, str]:
    """Where a public booking came from, for the audit row.

    The actor has no identifier of its own, so this and the request's IP are
    the whole answer to "who did this". Ids and the slug only — never the
    booker's name or email, which are PHI the audit trail must not carry.
    """
    return {
        "source": "public_booking",
        "booking_link_id": ctx.link.id,
        "booking_link_slug": ctx.link.slug,
    }


def _find_or_create_patient(
    request: CreatePublicBookingRequest,
    ctx: PublicBookingContext,
    patient_repo: PatientRepository,
    http_request: Request,
    audit: AuditService,
) -> Patient:
    existing = patient_repo.find_by_email(str(request.email), ctx.link.user_id)
    if existing is not None:
        return existing

    now = utc_now()
    patient = patient_repo.create(
        Patient(
            id=str(uuid.uuid4()),
            first_name=request.first_name.strip(),
            last_name=request.last_name.strip(),
            email=str(request.email),
            created_at=now,
            updated_at=now,
        ),
        ctx.link.user_id,
    )
    audit.log_patient_action(
        AuditAction.PATIENT_CREATED,
        ctx.owner,
        http_request,
        patient,
        changes=_booking_provenance(ctx),
        actor_type=ACTOR_TYPE_ANONYMOUS,
    )
    return patient


@router.post(
    "/api/public/booking-links/{slug}/bookings",
    response_model=PublicBookingConfirmation,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_public_booking_write_rate_limit)],
)
def create_public_booking(
    request: CreatePublicBookingRequest,
    http_request: Request,
    ctx: PublicBookingContext = Depends(get_public_booking_context),
    engine: AvailabilityEngine = Depends(get_public_availability_engine),
    scheduling: SchedulingService = Depends(get_public_scheduling_service),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    gcal_service: GoogleCalendarService = Depends(get_public_gcal_service),
    audit: AuditService = Depends(get_audit_service),
) -> PublicBookingConfirmation:
    """Book a free slot: create (or reuse, by email) a patient record and
    a confirmed appointment through the standard repositories.

    The requested start must exactly match a currently-free slot — the
    client is never trusted about availability.
    """
    _require_owner_may_accept_bookings(ctx.owner)

    date_str = request.start_at[:10]
    _parse_booking_date(date_str)

    slots = engine.get_free_slots(ctx.link.user_id, date_str, ctx.link.duration_minutes)
    slot = next((s for s in slots.slots if s.start == request.start_at), None)
    if slot is None:
        raise ConflictError("That time is no longer available. Please pick another slot.")

    patient = _find_or_create_patient(request, ctx, patient_repo, http_request, audit)

    note_lines = [f"Booked via booking link /{ctx.link.slug}."]
    if request.note:
        note_lines.append(f"Note from client: {request.note.strip()}")

    try:
        appt = scheduling.create_appointment(
            ctx.link.user_id,
            data={
                "patient_id": patient.id,
                "title": ctx.link.title,
                "start_at": slot.start,
                "end_at": slot.end,
                "duration_minutes": ctx.link.duration_minutes,
                "session_type": ctx.link.session_type,
                "notes": "\n".join(note_lines),
            },
        )
    except InvalidAppointmentError as e:
        raise BadRequestError(str(e)) from e
    audit.log_appointment_action(
        AuditAction.APPOINTMENT_CREATED,
        ctx.owner,
        http_request,
        appt.id,
        patient_id=patient.id,
        changes=_booking_provenance(ctx),
        actor_type=ACTOR_TYPE_ANONYMOUS,
    )

    # Called for its side effect only — it persists google_event_id on the
    # appointment. The confirmation below is built from the link and the
    # booked slot, so the synced copy it returns has nothing to add here.
    _sync_appointment_to_google(scheduling, gcal_service, ctx.owner, appt)

    return PublicBookingConfirmation(
        host_name=ctx.link.host_name,
        title=ctx.link.title,
        start_at=slot.start,
        end_at=slot.end,
        duration_minutes=ctx.link.duration_minutes,
    )
