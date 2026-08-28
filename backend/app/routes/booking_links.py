# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Owner-facing management of public booking links.

CRUD over ``platform.booking_links`` (docs/design/public-booking.md).
Booking links carry no PHI — slug, display copy, duration — so these
routes are classified non-PHI in the audit guardrail. The public,
unauthenticated surface lives in ``public_booking.py``.
"""

from __future__ import annotations

import uuid

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
from ..repositories import get_booking_link_repository
from ..repositories.booking_link import BookingLinkRepository, SlugTakenError
from ..utcnow import utc_now

router = APIRouter(tags=["booking-links"], dependencies=[Depends(require_active_subscription)])


def get_link_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> BookingLinkRepository:
    """Booking link repository, behind the standard tenant-context gate.

    The table itself is platform-scoped; the dependency exists so these
    routes authenticate and resolve the caller the same way every other
    authed route does.
    """
    return get_booking_link_repository()


@router.post(
    "/api/booking-links",
    response_model=BookingLinkResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_booking_link(
    request: CreateBookingLinkRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    repo: BookingLinkRepository = Depends(get_link_repository),
) -> BookingLinkResponse:
    """Create a booking link owned by the caller."""
    if not is_valid_slug(request.slug):
        raise BadRequestError("This slug is reserved. Please choose another.")
    now = utc_now()
    link = BookingLink(
        id=str(uuid.uuid4()),
        slug=request.slug,
        user_id=ctx.user_id,
        practice_id=ctx.practice_id,
        host_name=request.host_name.strip(),
        title=request.title.strip(),
        description=request.description,
        duration_minutes=request.duration_minutes,
        session_type=request.session_type,
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
    return BookingLinkResponse.from_link(link)


@router.get("/api/booking-links", response_model=BookingLinkListResponse)
def list_booking_links(
    ctx: TenantContext = Depends(get_tenant_context),
    repo: BookingLinkRepository = Depends(get_link_repository),
) -> BookingLinkListResponse:
    """List the caller's booking links, active and inactive."""
    links = repo.list_by_user(ctx.user_id)
    return BookingLinkListResponse(
        data=[BookingLinkResponse.from_link(link) for link in links],
        total=len(links),
    )


@router.patch("/api/booking-links/{link_id}", response_model=BookingLinkResponse)
def update_booking_link(
    link_id: str,
    request: UpdateBookingLinkRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    repo: BookingLinkRepository = Depends(get_link_repository),
) -> BookingLinkResponse:
    """Update display copy, duration, or active state. The slug is immutable."""
    link = repo.get(link_id, ctx.user_id)
    if link is None:
        raise NotFoundError("Booking link not found")
    if request.host_name is not None:
        link.host_name = request.host_name.strip()
    if request.title is not None:
        link.title = request.title.strip()
    if request.description is not None:
        link.description = request.description
    if request.duration_minutes is not None:
        link.duration_minutes = request.duration_minutes
    if request.is_active is not None:
        link.is_active = request.is_active
    return BookingLinkResponse.from_link(repo.update(link))


@router.delete("/api/booking-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_booking_link(
    link_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    repo: BookingLinkRepository = Depends(get_link_repository),
) -> None:
    """Tombstone a booking link. Its public URL 404s immediately and the slug stays claimed."""
    if not repo.delete(link_id, ctx.user_id):
        raise NotFoundError("Booking link not found")
