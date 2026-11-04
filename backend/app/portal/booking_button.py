# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's "Book a session" link on its website and in its portal.

The practice chooses where the link goes — one of its booking links, or an
outside booking address it already uses — and turns it on for its website, its
portal, or both (Settings > Website). Nothing appears until it does.

:func:`booking_button_url` is the one rule every surface asks: the address the
link points at on that surface, or ``None`` for no link. ``None`` when the
surface is off, when nothing is chosen, and when the chosen booking link is not
the practice's, is turned off or is deleted — so turning a link off removes the
button everywhere without anyone revisiting this setting. A booking link goes
to the practice's own host when that serves its booking pages
(:func:`app.portal.booking_hosts.booking_host`), and to the app's address
otherwise.

No PHI: a link's public slug, a public address and two switches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from sqlalchemy import select

from ..db.platform_models import BookingLinkRow, PracticeBookingButtonRow
from ..settings import get_settings
from .booking_hosts import booking_host

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

Surface = Literal["website", "portal"]

MAX_OUTSIDE_URL_LENGTH = 2048
#: Characters that could close or break out of the attribute the address goes in.
_UNSAFE = frozenset("<>\"'`")


@dataclass(frozen=True)
class BookingButton:
    """What the practice has chosen. Off, with nothing chosen, when it has not."""

    booking_link_id: str | None = None
    outside_url: str | None = None
    on_website: bool = False
    in_portal: bool = False


class InvalidOutsideUrlError(ValueError):
    """An outside booking address that cannot be linked to."""


def checked_outside_url(raw: str) -> str:
    """*raw* as an outside booking address: trimmed, and only a plain ``https``
    address with a host, no user name or password, and nothing that could
    break out of an attribute."""
    url = raw.strip()
    if not url or len(url) > MAX_OUTSIDE_URL_LENGTH:
        raise InvalidOutsideUrlError("Enter a booking address.")
    if any(c.isspace() or c in _UNSAFE or not c.isprintable() for c in url):
        raise InvalidOutsideUrlError("Enter a booking address without spaces.")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or "." not in parts.hostname:
        raise InvalidOutsideUrlError("Enter a booking address that starts with https://.")
    if parts.username is not None or parts.password is not None:
        raise InvalidOutsideUrlError("Enter a booking address without a user name.")
    return url


def get_booking_button(session: Session, practice_id: str) -> BookingButton:
    row = session.get(PracticeBookingButtonRow, practice_id)
    if row is None:
        return BookingButton()
    return BookingButton(
        booking_link_id=row.booking_link_id,
        outside_url=row.outside_url,
        on_website=row.on_website,
        in_portal=row.in_portal,
    )


def owned_booking_link(session: Session, practice_id: str, link_id: str) -> BookingLinkRow | None:
    """The practice's booking link *link_id*, if it has one by that id and it is not deleted."""
    return session.execute(
        select(BookingLinkRow).where(
            BookingLinkRow.id == link_id,
            BookingLinkRow.practice_id == practice_id,
            BookingLinkRow.deleted_at.is_(None),
        )
    ).scalar_one_or_none()


def save_booking_button(
    session: Session, practice_id: str, button: BookingButton, *, by: str, now: datetime
) -> None:
    """Store *button* for the practice; the caller has checked its target and commits."""
    row = session.get(PracticeBookingButtonRow, practice_id)
    if row is None:
        row = PracticeBookingButtonRow(practice_id=practice_id)
        session.add(row)
    row.booking_link_id = button.booking_link_id
    row.outside_url = button.outside_url
    row.on_website = button.on_website
    row.in_portal = button.in_portal
    row.updated_at = now
    row.updated_by = by
    session.flush()


def booking_button_url(session: Session, practice_id: str, surface: Surface) -> str | None:
    """Where the practice's "Book a session" link on *surface* goes, or ``None``."""
    button = get_booking_button(session, practice_id)
    if not (button.on_website if surface == "website" else button.in_portal):
        return None
    if button.outside_url is not None:
        return button.outside_url
    if button.booking_link_id is None:
        return None
    link = owned_booking_link(session, practice_id, button.booking_link_id)
    if link is None or not link.is_active:
        return None
    host = booking_host(session, practice_id)
    origin = f"https://{host}" if host is not None else get_settings().app_url
    return f"{origin}/book/{link.slug}"
