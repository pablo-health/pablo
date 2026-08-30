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

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..repositories.booking_link import BookingLinkRepository
    from ..repositories.google_calendar_token import GoogleCalendarTokenRepository
    from ..repositories.patient import PatientRepository
    from ..repositories.user import UserRepository
    from ..scheduling_engine.models.appointment import Appointment
    from ..scheduling_engine.models.conflict import TimeSlot
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
    ConfirmPublicBookingRequest,
    CreatePublicBookingRequest,
    PublicBookingConfirmation,
    PublicBookingLinkResponse,
)
from ..models.enums import PracticeEdition
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
from ..scheduling_engine.exceptions import AppointmentConflictError, InvalidAppointmentError
from ..scheduling_engine.models.appointment import AppointmentStatus
from ..scheduling_engine.services.availability import AvailabilityEngine
from ..scheduling_engine.services.scheduling import SchedulingService
from ..services import AuditService, get_audit_service
from ..services.email_sender import EmailSender, OutboundEmail, get_email_sender
from ..services.google_calendar_service import (
    GoogleCalendarService,
    google_consent_surface,
)
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

# How long a hold from a require-confirmation link keeps its slot before the
# expiry sweep releases it. A constant, not a setting — see
# docs/design/public-booking.md.
HOLD_TTL_MINUTES = 15

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
    if owner.status == "disabled" or link.practice_is_active is False:
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


def get_public_appointment_repository(
    appt_repo: AppointmentRepository = Depends(get_appointment_repository),
) -> AppointmentRepository:
    """The raw repository, for the one lookup the service layer doesn't wrap.

    A thin passthrough so this — like every other public dependency — is a
    single override point for tests, rather than reaching for the module-
    level ``get_appointment_repository`` directly.
    """
    return appt_repo


def get_public_gcal_service(
    token_repo: GoogleCalendarTokenRepository = Depends(get_google_calendar_token_repository),
    appt_repo: AppointmentRepository = Depends(get_appointment_repository),
) -> GoogleCalendarService:
    return GoogleCalendarService.from_surface(
        google_consent_surface(get_settings()),
        token_repo=token_repo,
        appointment_repo=appt_repo,
    )


def sweep_expired_holds(
    http_request: Request,
    ctx: PublicBookingContext = Depends(get_public_booking_context),
    scheduling: SchedulingService = Depends(get_public_scheduling_service),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> None:
    """Release lapsed holds' slots the next time anyone looks at this link.

    A route dependency, not a cron: there is no reliable moment to sweep a
    hold except when somebody next touches the link it belongs to, and that
    is exactly when its slot matters. Expiring a request without a
    confirmation token is ``expire_pending_appointments``'s own contract
    (some other surface's request, not a hold) — its patient is a real
    chart and must never be swept.
    """
    for appt in scheduling.expire_pending_appointments(ctx.link.user_id):
        if appt.confirmation_token_hash is None:
            continue
        patient = patient_repo.get(appt.patient_id, ctx.link.user_id)
        if patient is not None and patient.status == "pending":
            patient_repo.delete(patient.id, ctx.link.user_id)
            audit.log_patient_action(
                AuditAction.PATIENT_DELETED,
                ctx.owner,
                http_request,
                patient,
                changes={"source": "public_booking_hold_expired"},
                actor_type=ACTOR_TYPE_ANONYMOUS,
            )


_BOOKING_CLOSED = (
    "This practice isn't accepting online bookings right now. Please contact them directly."
)
_CONFIRMATION_INVALID = "This confirmation link is not valid."
_SLOT_TAKEN = "That time was taken while you were confirming. Please pick another slot."


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


@router.get(
    "/api/public/booking-links/{slug}/slots",
    response_model=FreeSlotsResponse,
    dependencies=[Depends(sweep_expired_holds)],
)
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


def _is_personal_edition(link: BookingLink) -> bool:
    """Whether this link's practice has declared itself non-clinical.

    ``None`` — a single-schema deployment, or any value that isn't the
    declared 'personal' string — means therapist semantics. The safe
    default is the clinical one.
    """
    return link.practice_edition == PracticeEdition.PERSONAL.value


def _patient_for_instant_booking(
    request: CreatePublicBookingRequest,
    ctx: PublicBookingContext,
    patient_repo: PatientRepository,
    http_request: Request,
    audit: AuditService,
) -> Patient:
    """Resolve the patient record for a link that books without a hold.

    An unverified email must never attach a booking to an existing
    chart: a fresh email gets a real, active record; a matched email
    gets a quarantined placeholder of its own, and the confirmed
    appointment books against that placeholder rather than the chart
    it matched. A personal-edition practice, which has declared it
    holds no clinical charts, is the one place a match may attach —
    byte-for-byte the original reuse-by-email behavior.
    """
    existing = patient_repo.find_by_email(str(request.email), ctx.link.user_id)
    if existing is not None and _is_personal_edition(ctx.link):
        return existing

    now = utc_now()
    if existing is None:
        patient = patient_repo.create(
            Patient(
                id=str(uuid.uuid4()),
                first_name=request.first_name.strip(),
                last_name=request.last_name.strip(),
                email=str(request.email),
                origin="public_booking",
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

    patient = patient_repo.create(
        Patient(
            id=str(uuid.uuid4()),
            first_name=request.first_name.strip(),
            last_name=request.last_name.strip(),
            email=str(request.email),
            status="pending",
            origin="public_booking",
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
        changes={
            "source": "public_booking",
            "status": "pending",
            "reason": "unverified_email_matched_chart",
        },
        actor_type=ACTOR_TYPE_ANONYMOUS,
    )
    return patient


def _confirmation_email(
    ctx: PublicBookingContext, to: str, slot: TimeSlot, token: str
) -> OutboundEmail:
    confirm_url = f"{get_settings().app_url}/book/{ctx.link.slug}/confirm?token={token}"
    return OutboundEmail(
        to=to,
        subject=f"Confirm your {ctx.link.title} with {ctx.link.host_name}",
        kind="booking_confirmation",
        text=(
            f"{ctx.link.host_name} is holding {ctx.link.title} for you on "
            f"{slot.start[:10]} at {slot.start[11:16]}, {ctx.link.duration_minutes} minutes.\n\n"
            f"Confirm this booking: {confirm_url}\n\n"
            f"This hold expires in {HOLD_TTL_MINUTES} minutes."
        ),
    )


def _place_hold(
    request: CreatePublicBookingRequest,
    ctx: PublicBookingContext,
    slot: TimeSlot,
    note_lines: list[str],
    patient_repo: PatientRepository,
    scheduling: SchedulingService,
    sender: EmailSender,
    http_request: Request,
    audit: AuditService,
) -> PublicBookingConfirmation:
    """Place a slot-holding appointment and mail a confirmation link.

    Always creates a fresh, quarantined patient record — never reused by
    email, unlike the instant path. Verified attach happens on confirm;
    this record is a placeholder until then.
    """
    now = utc_now()
    patient = patient_repo.create(
        Patient(
            id=str(uuid.uuid4()),
            first_name=request.first_name.strip(),
            last_name=request.last_name.strip(),
            email=str(request.email),
            status="pending",
            origin="public_booking",
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
        changes={"source": "public_booking", "status": "pending"},
        actor_type=ACTOR_TYPE_ANONYMOUS,
    )

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()

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
                "status": "pending",
                "pending_expires_at": now + timedelta(minutes=HOLD_TTL_MINUTES),
                "confirmation_token_hash": token_hash,
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
        changes={"source": "public_booking", "status": "pending"},
        actor_type=ACTOR_TYPE_ANONYMOUS,
    )

    message = _confirmation_email(ctx, str(request.email), slot, token)
    try:
        sender.send(message)
    except Exception as e:
        logger.warning("booking confirmation send failed: kind=%s", message.kind)
        scheduling.cancel_appointment(appt.id, ctx.link.user_id)
        patient_repo.delete(patient.id, ctx.link.user_id)
        raise ForbiddenError(_BOOKING_CLOSED) from e

    return PublicBookingConfirmation(
        host_name=ctx.link.host_name,
        title=ctx.link.title,
        start_at=slot.start,
        end_at=slot.end,
        duration_minutes=ctx.link.duration_minutes,
        status="pending_confirmation",
    )


@router.post(
    "/api/public/booking-links/{slug}/bookings",
    response_model=PublicBookingConfirmation,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_public_booking_write_rate_limit), Depends(sweep_expired_holds)],
)
def create_public_booking(
    request: CreatePublicBookingRequest,
    http_request: Request,
    ctx: PublicBookingContext = Depends(get_public_booking_context),
    engine: AvailabilityEngine = Depends(get_public_availability_engine),
    scheduling: SchedulingService = Depends(get_public_scheduling_service),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    gcal_service: GoogleCalendarService = Depends(get_public_gcal_service),
    sender: EmailSender = Depends(get_email_sender),
    audit: AuditService = Depends(get_audit_service),
) -> PublicBookingConfirmation:
    """Book a free slot.

    A link that requires email confirmation gets a slot-holding
    appointment and a confirmation email instead of an instant booking —
    see ``_place_hold``. The requested start must exactly match a
    currently-free slot — the client is never trusted about availability.
    """
    _require_owner_may_accept_bookings(ctx.owner)

    if ctx.link.require_email_confirmation and not sender.can_deliver:
        logger.warning("booking refused, cannot deliver confirmation: slug=%s", ctx.link.slug)
        raise ForbiddenError(_BOOKING_CLOSED)

    date_str = request.start_at[:10]
    _parse_booking_date(date_str)

    slots = engine.get_free_slots(ctx.link.user_id, date_str, ctx.link.duration_minutes)
    slot = next((s for s in slots.slots if s.start == request.start_at), None)
    if slot is None:
        raise ConflictError("That time is no longer available. Please pick another slot.")

    note_lines = [f"Booked via booking link /{ctx.link.slug}."]
    if request.note:
        note_lines.append(f"Note from client: {request.note.strip()}")

    if ctx.link.require_email_confirmation:
        return _place_hold(
            request, ctx, slot, note_lines, patient_repo, scheduling, sender, http_request, audit
        )

    patient = _patient_for_instant_booking(request, ctx, patient_repo, http_request, audit)

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
        status="confirmed",
    )


def _slot_start_iso(appt: Appointment) -> str:
    """Render an appointment's start the way the availability engine does.

    Free slots come back as UTC ``...Z`` strings truncated to the second;
    comparing against those has to render the stored instant the same way
    rather than relying on ``isoformat`` matching by accident.
    """
    return appt.start_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _confirmation_from_appointment(
    ctx: PublicBookingContext, appt: Appointment
) -> PublicBookingConfirmation:
    return PublicBookingConfirmation(
        host_name=ctx.link.host_name,
        title=ctx.link.title,
        start_at=_slot_start_iso(appt),
        end_at=appt.end_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        duration_minutes=ctx.link.duration_minutes,
        status="confirmed",
    )


def _release_the_loser(
    appt: Appointment,
    ctx: PublicBookingContext,
    scheduling: SchedulingService,
    patient_repo: PatientRepository,
    http_request: Request,
    audit: AuditService,
) -> None:
    """The slot went to someone else while this hold sat pending or lapsed.

    Cancels this hold and soft-deletes its placeholder explicitly rather
    than relying on the request rolling back — the winner's write already
    committed, so this cleanup has to be its own, separately-committed
    step for the two paths to agree on a single non-cancelled appointment.
    """
    scheduling.cancel_appointment(appt.id, ctx.link.user_id)
    placeholder = patient_repo.get(appt.patient_id, ctx.link.user_id)
    if placeholder is not None and placeholder.status == "pending":
        patient_repo.delete(placeholder.id, ctx.link.user_id)
        audit.log_patient_action(
            AuditAction.PATIENT_DELETED,
            ctx.owner,
            http_request,
            placeholder,
            changes={"source": "public_booking_hold_lost_race"},
            actor_type=ACTOR_TYPE_ANONYMOUS,
        )
    audit.log_appointment_action(
        AuditAction.APPOINTMENT_UPDATED,
        ctx.owner,
        http_request,
        appt.id,
        patient_id=appt.patient_id,
        changes={"source": "public_booking", "status": "cancelled_slot_taken"},
        actor_type=ACTOR_TYPE_ANONYMOUS,
    )


def _promote_pending_patient(
    appt: Appointment,
    ctx: PublicBookingContext,
    patient_repo: PatientRepository,
    scheduling: SchedulingService,
    http_request: Request,
    audit: AuditService,
) -> Appointment:
    """Turn a confirmed hold's quarantined placeholder into a real chart.

    A verified email that matches an existing chart re-points the
    appointment at it and drops the placeholder — the existing chart's own
    fields are never touched. Otherwise the placeholder itself graduates
    to an active patient, keeping its ``public_booking`` origin as
    provenance rather than quarantine. Returns the appointment, re-pointed
    if it was re-pointed — callers must use the returned value rather than
    their own copy, since the object identity backing it isn't guaranteed
    to be shared with the write this makes.
    """
    placeholder = patient_repo.get(appt.patient_id, ctx.link.user_id)
    if placeholder is None or placeholder.status != "pending":
        return appt  # already promoted (e.g. the idempotent second confirm)

    existing = patient_repo.find_by_email(str(placeholder.email), ctx.link.user_id)
    if existing is not None:
        appt = scheduling.update_appointment(appt.id, ctx.link.user_id, patient_id=existing.id)
        patient_repo.delete(placeholder.id, ctx.link.user_id)
        audit.log_appointment_action(
            AuditAction.APPOINTMENT_UPDATED,
            ctx.owner,
            http_request,
            appt.id,
            patient_id=existing.id,
            changes={"source": "public_booking", "attached": "verified_email"},
            actor_type=ACTOR_TYPE_ANONYMOUS,
        )
        audit.log_patient_action(
            AuditAction.PATIENT_DELETED,
            ctx.owner,
            http_request,
            placeholder,
            changes={"source": "public_booking_placeholder_merged"},
            actor_type=ACTOR_TYPE_ANONYMOUS,
        )
        return appt

    placeholder.status = "active"
    patient_repo.update(placeholder)
    audit.log_patient_action(
        AuditAction.PATIENT_UPDATED,
        ctx.owner,
        http_request,
        placeholder,
        changes={"source": "public_booking", "status": "active"},
        actor_type=ACTOR_TYPE_ANONYMOUS,
    )
    return appt


@router.post(
    "/api/public/booking-links/{slug}/confirm",
    response_model=PublicBookingConfirmation,
    dependencies=[Depends(sweep_expired_holds)],
)
def confirm_public_booking(
    request: ConfirmPublicBookingRequest,
    http_request: Request,
    ctx: PublicBookingContext = Depends(get_public_booking_context),
    engine: AvailabilityEngine = Depends(get_public_availability_engine),
    scheduling: SchedulingService = Depends(get_public_scheduling_service),
    appt_repo: AppointmentRepository = Depends(get_public_appointment_repository),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    gcal_service: GoogleCalendarService = Depends(get_public_gcal_service),
    audit: AuditService = Depends(get_audit_service),
) -> PublicBookingConfirmation:
    """Finish a hold: confirm it, or revive it if it already lapsed.

    POST, not GET — a GET link is exactly what mail scanners and link
    previewers fetch, and either would consume a one-time token before the
    booker ever clicks it. One message covers a token that never existed,
    belongs to another link, or was killed by a clinician cancelling the
    hold in-app: none of those should be distinguishable from each other.
    """
    _require_owner_may_accept_bookings(ctx.owner)

    token_hash = hashlib.sha256(request.token.encode()).hexdigest()
    appt = appt_repo.get_by_confirmation_token_hash(ctx.link.user_id, token_hash)
    if appt is None:
        raise NotFoundError(_CONFIRMATION_INVALID)

    if appt.status == AppointmentStatus.CONFIRMED:
        # Idempotent: a second click or a double form submit does nothing.
        return _confirmation_from_appointment(ctx, appt)

    if appt.status == AppointmentStatus.PENDING:
        try:
            appt = scheduling.confirm_appointment(appt.id, ctx.link.user_id)
        except AppointmentConflictError:
            _release_the_loser(appt, ctx, scheduling, patient_repo, http_request, audit)
            raise ConflictError(_SLOT_TAKEN) from None
    elif appt.status == AppointmentStatus.CANCELLED:
        # Found by its hash, which only a lapsed hold still carries — a
        # clinician-cancelled hold has its hash cleared and would never
        # have matched the lookup above.
        slots = engine.get_free_slots(
            ctx.link.user_id, appt.start_at.date().isoformat(), ctx.link.duration_minutes
        )
        still_free = any(s.start == _slot_start_iso(appt) for s in slots.slots)
        restored = patient_repo.restore(appt.patient_id, ctx.link.user_id) if still_free else None
        if not still_free or restored is None:
            raise ConflictError(_SLOT_TAKEN)
        try:
            appt = scheduling.finalize_lapsed_hold(appt.id, ctx.link.user_id)
        except AppointmentConflictError:
            raise ConflictError(_SLOT_TAKEN) from None
    else:
        raise NotFoundError(_CONFIRMATION_INVALID)

    appt = _promote_pending_patient(appt, ctx, patient_repo, scheduling, http_request, audit)
    appt = _sync_appointment_to_google(scheduling, gcal_service, ctx.owner, appt)

    audit.log_appointment_action(
        AuditAction.APPOINTMENT_UPDATED,
        ctx.owner,
        http_request,
        appt.id,
        patient_id=appt.patient_id,
        changes={"source": "public_booking", "status": "confirmed"},
        actor_type=ACTOR_TYPE_ANONYMOUS,
    )

    return _confirmation_from_appointment(ctx, appt)
