# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's "Book a session" link, as the practice manages it (Settings > Website).

* ``GET /api/practice/booking-button`` — any clinician of the practice: where
  the link goes, which surfaces show it, and the address each one shows now.
* ``PUT /api/practice/booking-button`` — the owner or a practice admin, the
  same people who manage the website
  (:func:`app.routes.practice_domains._manageable_practice_id`).

The target is one of the practice's booking links or an outside https address,
never both. A surface can only be turned on with a target chosen. A change is
audited, and forgets this process's website host answers once it has
committed, so the website follows at once; another process notices within the
minute it keeps answers. The portal asks afresh on every load.

The website's link is filled in as its pages are served, so a deployment that
serves websites straight from a storage bucket (``practice_site_live_bucket``)
cannot show it yet; ``website_available`` says so and the card leaves the
website out (PABLO-ub7w).

No PHI: a link id, a public address and two switches.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel, Field

from ..api_errors import UnprocessableEntityError
from ..auth.service import require_active_subscription
from ..db import get_db_session
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..routes._deps import practice_id_or_404
from ..routes.practice_domains import _manageable_practice_id
from ..services import AuditService, get_audit_service
from ..settings import get_settings
from ..sites.hosts import get_site_host_cache
from ..utcnow import utc_now
from .booking_button import (
    BookingButton,
    InvalidOutsideUrlError,
    booking_button_url,
    checked_outside_url,
    get_booking_button,
    owned_booking_link,
    save_booking_button,
)

router = APIRouter(prefix="/api/practice/booking-button", tags=["practice-website"])


class BookingButtonRequest(BaseModel):
    booking_link_id: str | None = Field(None, max_length=64)
    outside_url: str | None = Field(None, max_length=4096)
    on_website: bool = False
    in_portal: bool = False


class BookingButtonResponse(BaseModel):
    booking_link_id: str | None
    outside_url: str | None
    on_website: bool
    in_portal: bool
    #: Where the link on the website goes now; ``None`` when it shows nothing.
    website_url: str | None
    #: Where the link in the portal goes now; ``None`` when it shows nothing.
    portal_url: str | None
    #: Whether this deployment can show the link on a website.
    website_available: bool


def _response(practice_id: str) -> BookingButtonResponse:
    session = get_db_session()
    button = get_booking_button(session, practice_id)
    return BookingButtonResponse(
        booking_link_id=button.booking_link_id,
        outside_url=button.outside_url,
        on_website=button.on_website,
        in_portal=button.in_portal,
        website_url=booking_button_url(session, practice_id, "website"),
        portal_url=booking_button_url(session, practice_id, "portal"),
        website_available=not (get_settings().practice_site_live_bucket or "").strip(),
    )


def _checked(body: BookingButtonRequest, practice_id: str) -> BookingButton:
    if body.booking_link_id and body.outside_url:
        raise UnprocessableEntityError(
            "Choose a booking link or another booking address, not both."
        )
    outside_url = None
    if body.outside_url:
        try:
            outside_url = checked_outside_url(body.outside_url)
        except InvalidOutsideUrlError as error:
            raise UnprocessableEntityError(str(error)) from error
    link_id = body.booking_link_id or None
    if link_id and owned_booking_link(get_db_session(), practice_id, link_id) is None:
        raise UnprocessableEntityError("That booking link isn't one of your practice's.")
    if (body.on_website or body.in_portal) and not (link_id or outside_url):
        raise UnprocessableEntityError("Choose where the link goes.")
    return BookingButton(
        booking_link_id=link_id,
        outside_url=outside_url,
        on_website=body.on_website,
        in_portal=body.in_portal,
    )


@router.get("", response_model=BookingButtonResponse)
def get_practice_booking_button(
    user: User = Depends(require_active_subscription),
) -> BookingButtonResponse:
    """Where the practice's "Book a session" link goes and where it shows."""
    return _response(practice_id_or_404(user))


@router.put("", response_model=BookingButtonResponse)
def set_practice_booking_button(
    body: BookingButtonRequest,
    http_request: Request,
    background: BackgroundTasks,
    user: User = Depends(require_active_subscription),
    audit: AuditService = Depends(get_audit_service),
) -> BookingButtonResponse:
    """Choose where the link goes and which surfaces show it."""
    practice_id = _manageable_practice_id(user)
    button = _checked(body, practice_id)
    save_booking_button(get_db_session(), practice_id, button, by=user.id, now=utc_now())
    audit.log(
        AuditAction.PRACTICE_BOOKING_BUTTON_CHANGED,
        user,
        http_request,
        resource_type=ResourceType.PRACTICE,
        resource_id=practice_id,
        changes={
            "practice_id": practice_id,
            "booking_link_id": button.booking_link_id,
            "outside_url": button.outside_url is not None,
            "on_website": button.on_website,
            "in_portal": button.in_portal,
        },
    )
    background.add_task(get_site_host_cache().clear)
    return _response(practice_id)
