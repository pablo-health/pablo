# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Owner-facing management of public booking links.

CRUD over ``platform.booking_links`` (docs/design/public-booking.md).
Booking links carry no PHI — slug, display copy, the appointment type they
book — so these routes are classified non-PHI in the audit guardrail. The
public, unauthenticated surface lives in ``public_booking.py``.

Every response says whether the link can actually take a booking and, when
it cannot, why — the type's switches and the practice policy decide that,
and a therapist who sends a dead link deserves to learn it here rather than
from a client who gave up.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, status

from ..api_errors import BadRequestError, ConflictError, NotFoundError
from ..auth.service import (
    TenantContext,
    get_tenant_context,
    require_active_subscription,
)
from ..models.booking_link import (
    BookingLink,
    BookingLinkListResponse,
    BookingLinkResponse,
    CreateBookingLinkRequest,
    UpdateBookingLinkRequest,
    is_valid_slug,
)
from ..repositories import get_appointment_type_repository as _appt_type_repo_factory
from ..repositories import get_booking_link_repository
from ..repositories.booking_link import BookingLinkRepository, SlugTakenError
from ..scheduling_engine.services.booking_link_gate import assess_link
from ..scheduling_engine.services.scheduling_policy import current_policy_or_defaults
from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..scheduling_engine.models.appointment_type import AppointmentType
    from ..scheduling_engine.repositories.appointment_type import AppointmentTypeRepository

router = APIRouter(tags=["booking-links"], dependencies=[Depends(require_active_subscription)])

_TYPE_NOT_YOURS = "Pick one of your own appointment types for this link."


def get_link_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> BookingLinkRepository:
    """Booking link repository, behind the standard tenant-context gate.

    The table itself is platform-scoped; the dependency exists so these
    routes authenticate and resolve the caller the same way every other
    authed route does.
    """
    return get_booking_link_repository()


def get_link_type_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> AppointmentTypeRepository:
    """The caller's appointment types, scoped to their practice."""
    return _appt_type_repo_factory()


def get_link_scheduling_policy(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> dict[str, object]:
    """The caller's practice scheduling policy, defaults when unset."""
    return current_policy_or_defaults()


def _response(
    link: BookingLink,
    appointment_type: AppointmentType | None,
    policy: dict[str, object],
) -> BookingLinkResponse:
    return BookingLinkResponse.from_link(
        link,
        appointment_type=appointment_type,
        bookability=assess_link(appointment_type, policy),
    )


def _require_own_type(
    appointment_type_id: str, ctx: TenantContext, type_repo: AppointmentTypeRepository
) -> AppointmentType:
    """The named type, provided it belongs to the caller.

    A type that is not the caller's — or does not exist — is one answer: the
    write is refused. Booking links are platform rows, so this is the only
    place the reference is checked against a real type; get it wrong here
    and the link points at nothing.
    """
    appointment_type = type_repo.get(appointment_type_id, ctx.user_id)
    if appointment_type is None:
        raise BadRequestError(_TYPE_NOT_YOURS)
    return appointment_type


@router.post(
    "/api/booking-links",
    response_model=BookingLinkResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_booking_link(
    request: CreateBookingLinkRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    repo: BookingLinkRepository = Depends(get_link_repository),
    type_repo: AppointmentTypeRepository = Depends(get_link_type_repository),
    policy: dict[str, object] = Depends(get_link_scheduling_policy),
) -> BookingLinkResponse:
    """Create a booking link owned by the caller, booking one of their types."""
    if not is_valid_slug(request.slug):
        raise BadRequestError("This slug is reserved. Please choose another.")
    appointment_type = _require_own_type(request.appointment_type_id, ctx, type_repo)
    now = utc_now()
    link = BookingLink(
        id=str(uuid.uuid4()),
        slug=request.slug,
        user_id=ctx.user_id,
        practice_id=ctx.practice_id,
        host_name=request.host_name.strip(),
        title=request.title.strip(),
        description=request.description,
        appointment_type_id=appointment_type.id,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    try:
        link = repo.create(link)
    except SlugTakenError as e:
        raise ConflictError(
            "This slug is taken. Slugs stay reserved after a link is deleted, so pick another."
        ) from e
    return _response(link, appointment_type, policy)


@router.get("/api/booking-links", response_model=BookingLinkListResponse)
def list_booking_links(
    ctx: TenantContext = Depends(get_tenant_context),
    repo: BookingLinkRepository = Depends(get_link_repository),
    type_repo: AppointmentTypeRepository = Depends(get_link_type_repository),
    policy: dict[str, object] = Depends(get_link_scheduling_policy),
) -> BookingLinkListResponse:
    """List the caller's booking links, active and inactive."""
    links = repo.list_by_user(ctx.user_id)
    types = {t.id: t for t in type_repo.list_by_user(ctx.user_id)}
    return BookingLinkListResponse(
        data=[_response(link, types.get(link.appointment_type_id), policy) for link in links],
        total=len(links),
    )


@router.patch("/api/booking-links/{link_id}", response_model=BookingLinkResponse)
def update_booking_link(
    link_id: str,
    request: UpdateBookingLinkRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    repo: BookingLinkRepository = Depends(get_link_repository),
    type_repo: AppointmentTypeRepository = Depends(get_link_type_repository),
    policy: dict[str, object] = Depends(get_link_scheduling_policy),
) -> BookingLinkResponse:
    """Update display copy, the appointment type, or active state. The slug is immutable."""
    link = repo.get(link_id, ctx.user_id)
    if link is None:
        raise NotFoundError("Booking link not found")
    if request.host_name is not None:
        link.host_name = request.host_name.strip()
    if request.title is not None:
        link.title = request.title.strip()
    if request.description is not None:
        link.description = request.description
    if request.appointment_type_id is not None:
        link.appointment_type_id = _require_own_type(request.appointment_type_id, ctx, type_repo).id
    if request.is_active is not None:
        link.is_active = request.is_active
    updated = repo.update(link)
    return _response(updated, type_repo.get(updated.appointment_type_id, ctx.user_id), policy)


@router.delete("/api/booking-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_booking_link(
    link_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    repo: BookingLinkRepository = Depends(get_link_repository),
) -> None:
    """Tombstone a booking link. Its public URL 404s immediately and the slug stays claimed."""
    if not repo.delete(link_id, ctx.user_id):
        raise NotFoundError("Booking link not found")
