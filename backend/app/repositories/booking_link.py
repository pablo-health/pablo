# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Repository abstraction for public booking links.

Booking links live in the shared ``platform`` schema (no RLS): the
``user_id`` filter on every owner-facing read/write is the access-control
boundary, while ``get_by_slug`` is intentionally unfiltered — it backs
the public resolution step that happens before any tenant or user
context exists. See docs/design/public-booking.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.booking_link import BookingLink


class SlugTakenError(Exception):
    """The requested slug is already registered to a booking link."""


class BookingLinkRepository(ABC):
    """Abstract store for booking links."""

    @abstractmethod
    def get_by_slug(self, slug: str) -> BookingLink | None:
        """Resolve a slug regardless of owner or active state.

        Callers on the public path must treat an inactive link exactly
        like a missing one.
        """

    @abstractmethod
    def get(self, link_id: str, user_id: str) -> BookingLink | None:
        """Fetch one link, scoped to its owner."""

    @abstractmethod
    def list_by_user(self, user_id: str) -> list[BookingLink]:
        """All of a user's links, active and inactive, newest first."""

    @abstractmethod
    def create(self, link: BookingLink) -> BookingLink:
        """Insert a link.

        Raises:
            SlugTakenError: the slug is already registered.
        """

    @abstractmethod
    def update(self, link: BookingLink) -> BookingLink:
        """Persist mutable fields (display copy, duration, active state)."""

    @abstractmethod
    def delete(self, link_id: str, user_id: str) -> bool:
        """Delete a link, scoped to its owner. Returns whether a row existed."""


class InMemoryBookingLinkRepository(BookingLinkRepository):
    """In-memory implementation for tests and development."""

    def __init__(self) -> None:
        self._links: dict[str, BookingLink] = {}
        self._lock = Lock()

    def get_by_slug(self, slug: str) -> BookingLink | None:
        with self._lock:
            return next((link for link in self._links.values() if link.slug == slug), None)

    def get(self, link_id: str, user_id: str) -> BookingLink | None:
        with self._lock:
            link = self._links.get(link_id)
            return link if link and link.user_id == user_id else None

    def list_by_user(self, user_id: str) -> list[BookingLink]:
        with self._lock:
            links = [link for link in self._links.values() if link.user_id == user_id]
        return sorted(links, key=lambda link: link.created_at, reverse=True)

    def create(self, link: BookingLink) -> BookingLink:
        with self._lock:
            if any(existing.slug == link.slug for existing in self._links.values()):
                raise SlugTakenError(link.slug)
            self._links[link.id] = link
        return link

    def update(self, link: BookingLink) -> BookingLink:
        with self._lock:
            self._links[link.id] = link
        return link

    def delete(self, link_id: str, user_id: str) -> bool:
        with self._lock:
            link = self._links.get(link_id)
            if link is None or link.user_id != user_id:
                return False
            del self._links[link_id]
            return True
