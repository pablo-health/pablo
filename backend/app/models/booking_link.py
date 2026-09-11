# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Booking link domain model and API schemas.

A booking link is a clinician-created public slug through which a client
can book an appointment directly (docs/design/public-booking.md). The
record itself carries no PHI — slug, owner, display copy, and the
appointment type it books. Length comes from the type; the link does not
carry a second answer to that question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, EmailStr, Field

from .coverage import IntakeCoverage  # noqa: TC001 — Pydantic resolves the field type at runtime

if TYPE_CHECKING:
    from ..scheduling_engine.models.appointment_type import AppointmentType
    from ..scheduling_engine.services.booking_link_gate import LinkBookability

SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{2,63}$"
_SLUG_RE = re.compile(SLUG_PATTERN)

# Slugs that would shadow app routes or invite impersonation.
RESERVED_SLUGS: frozenset[str] = frozenset(
    {
        "admin",
        "api",
        "app",
        "assets",
        "auth",
        "book",
        "health",
        "login",
        "logout",
        "onboarding",
        "pablo",
        "settings",
        "signup",
        "static",
        "support",
        "www",
    }
)


def is_valid_slug(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug)) and slug not in RESERVED_SLUGS


@dataclass
class BookingLink:
    """A public booking link owned by a clinician."""

    id: str
    slug: str
    user_id: str
    practice_id: str | None
    host_name: str
    title: str
    description: str | None
    # The appointment type this link books. ``appointment_types`` is a
    # per-practice table and this record is platform-scoped, so this is the
    # type's id held as a value, validated against the owner's own types
    # when the link is written and resolved again after the practice is
    # known on the public path. A type that has since been deleted makes
    # the link non-bookable rather than an error.
    appointment_type_id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # Whether a booking through this link must confirm by email before it
    # holds a real slot. Born true, database-enforced, no API surface — see
    # BookingLinkRow.require_email_confirmation.
    require_email_confirmation: bool = True
    # The owning practice's schema name. Populated only by
    # ``get_by_slug`` (the public resolution path, which must select a
    # tenant schema before any other query); owner-facing reads leave it
    # None.
    practice_schema: str | None = None
    # The owning practice's declared edition ('therapist' / 'personal').
    # Populated only by ``get_by_slug``, alongside practice_schema; None
    # in a single-schema deployment (no practice row) or when unset.
    practice_edition: str | None = None
    # Whether the owning practice is active. Populated only by
    # ``get_by_slug``, alongside practice_schema; None when there is no
    # practice row (single-tenant deployment), in which case the public
    # surface has no practice-level reason to refuse the link.
    practice_is_active: bool | None = None
    # Set when the link is tombstoned. A tombstoned link never reaches a
    # response; its slug stays claimed via the UNIQUE(slug) constraint.
    deleted_at: datetime | None = None


class CreateBookingLinkRequest(BaseModel):
    slug: str = Field(..., pattern=SLUG_PATTERN, max_length=64)
    host_name: str = Field(..., min_length=1, max_length=255)
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    #: Required. A link with no type would be a link nothing can gate, so
    #: there is no untyped shape to fall back to.
    appointment_type_id: str = Field(..., min_length=1, max_length=64)


class UpdateBookingLinkRequest(BaseModel):
    host_name: str | None = Field(None, min_length=1, max_length=255)
    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    appointment_type_id: str | None = Field(None, min_length=1, max_length=64)
    is_active: bool | None = None


class BookingLinkResponse(BaseModel):
    """The owner's view of a link, with whether it can actually take a booking.

    ``bookable`` and ``not_bookable_reason`` are computed, not stored: they
    answer the question at read time against the type and the practice
    policy as they stand. The public surface never sees either — a booker
    gets an identical "not found" for every reason a link is closed.
    """

    id: str
    slug: str
    host_name: str
    title: str
    description: str | None
    appointment_type_id: str
    #: Display fields resolved from the type; ``None`` when the type is gone.
    appointment_type_name: str | None
    duration_minutes: int | None
    bookable: bool
    not_bookable_reason: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_link(
        cls,
        link: BookingLink,
        *,
        appointment_type: AppointmentType | None,
        bookability: LinkBookability,
    ) -> BookingLinkResponse:
        return cls(
            id=link.id,
            slug=link.slug,
            host_name=link.host_name,
            title=link.title,
            description=link.description,
            appointment_type_id=link.appointment_type_id,
            appointment_type_name=appointment_type.name if appointment_type else None,
            duration_minutes=appointment_type.duration_minutes if appointment_type else None,
            bookable=bookability.bookable,
            not_bookable_reason=bookability.reason,
            is_active=link.is_active,
            created_at=link.created_at,
            updated_at=link.updated_at,
        )


class BookingLinkListResponse(BaseModel):
    data: list[BookingLinkResponse]
    total: int


class PublicBookingLinkResponse(BaseModel):
    """The public display card — everything a visitor may see about a link."""

    slug: str
    host_name: str
    title: str
    description: str | None
    duration_minutes: int
    # Cloudflare Turnstile site key, present only when CAPTCHA_PROVIDER is
    # configured. A site key is public by definition — it is rendered into
    # the page — so exposing it on the card is not a disclosure.
    captcha_site_key: str | None = None


class CreatePublicBookingRequest(BaseModel):
    start_at: str = Field(..., description="Slot start exactly as returned by the slots endpoint")
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    note: str | None = Field(None, max_length=1000)
    # Insurance as read off the card, optional. Goes straight onto the new
    # chart's coverage on file; omitted, nothing is created.
    insurance: IntakeCoverage | None = None


class ConfirmPublicBookingRequest(BaseModel):
    token: str = Field(..., min_length=1)


class PublicBookingConfirmation(BaseModel):
    """What the booker gets back — deliberately minimal, no internal ids.

    Both the instant path and the hold-for-confirmation path return this
    same shape; only ``status`` tells them apart.
    """

    host_name: str
    title: str
    start_at: str
    end_at: str
    duration_minutes: int
    status: Literal["confirmed", "pending_confirmation"]
    # A capability link the booker can use to view or cancel this booking
    # later — not an id, just the same confirmation token folded into a
    # URL. ``None`` for an instant booking, which never gets a token.
    manage_url: str | None = None


class PublicManagedBooking(BaseModel):
    """What a booker sees when they redeem their manage link.

    Same "no internal ids" posture as the rest of the public surface —
    link-derived display fields plus the appointment's own status, and
    nothing that identifies the patient record behind it.
    """

    title: str
    host_name: str
    start_at: str
    end_at: str
    duration_minutes: int
    status: str


class PublicBookingCancellation(BaseModel):
    cancelled: bool = True
