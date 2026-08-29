# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL implementation of BookingLinkRepository."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ...db.platform_models import BookingLinkRow, PracticeRow
from ...models.booking_link import BookingLink
from ...utcnow import utc_now
from ..booking_link import BookingLinkRepository, SlugTakenError

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult
    from sqlalchemy.orm import Session


# SQLSTATE 23505, unique_violation. ``booking_links`` carries exactly one
# unique constraint besides the primary key (slug), and the primary key is
# a freshly minted uuid4, so a 23505 here is a slug collision.
_UNIQUE_VIOLATION = "23505"


def _row_to_link(row: BookingLinkRow) -> BookingLink:
    return BookingLink(
        id=row.id,
        slug=row.slug,
        user_id=row.user_id,
        practice_id=row.practice_id,
        host_name=row.host_name,
        title=row.title,
        description=row.description,
        duration_minutes=row.duration_minutes,
        session_type=row.session_type,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
        require_email_confirmation=row.require_email_confirmation,
        deleted_at=row.deleted_at,
    )


class PostgresBookingLinkRepository(BookingLinkRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_slug(self, slug: str) -> BookingLink | None:
        stmt = (
            select(
                BookingLinkRow,
                PracticeRow.schema_name,
                PracticeRow.edition,
                PracticeRow.is_active,
                PracticeRow.deleted_at,
            )
            .outerjoin(PracticeRow, PracticeRow.id == BookingLinkRow.practice_id)
            .where(BookingLinkRow.slug == slug, BookingLinkRow.deleted_at.is_(None))
        )
        result = self._session.execute(stmt).one_or_none()
        if result is None:
            return None
        row, schema_name, edition, practice_is_active, practice_deleted_at = result
        link = _row_to_link(row)
        link.practice_schema = schema_name
        link.practice_edition = edition
        if schema_name is not None:
            link.practice_is_active = practice_is_active and practice_deleted_at is None
        return link

    def get(self, link_id: str, user_id: str) -> BookingLink | None:
        stmt = select(BookingLinkRow).where(
            BookingLinkRow.id == link_id,
            BookingLinkRow.user_id == user_id,
            BookingLinkRow.deleted_at.is_(None),
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        return _row_to_link(row) if row else None

    def list_by_user(self, user_id: str) -> list[BookingLink]:
        stmt = (
            select(BookingLinkRow)
            .where(BookingLinkRow.user_id == user_id, BookingLinkRow.deleted_at.is_(None))
            .order_by(BookingLinkRow.created_at.desc())
        )
        return [_row_to_link(row) for row in self._session.execute(stmt).scalars()]

    def create(self, link: BookingLink) -> BookingLink:
        row = BookingLinkRow(
            id=link.id,
            slug=link.slug,
            user_id=link.user_id,
            practice_id=link.practice_id,
            host_name=link.host_name,
            title=link.title,
            description=link.description,
            duration_minutes=link.duration_minutes,
            session_type=link.session_type,
            is_active=link.is_active,
            created_at=link.created_at,
            updated_at=link.updated_at,
        )
        # SAVEPOINT, not a bare flush: a slug collision must undo this INSERT
        # and nothing else. Rolling back the request session here would
        # discard unrelated work the caller had already staged, and a
        # repository is not the right scope to make that decision.
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except IntegrityError as e:
            if getattr(e.orig, "pgcode", None) != _UNIQUE_VIOLATION:
                # A foreign-key or check violation is not a taken slug;
                # reporting it as one would send the caller chasing a
                # conflict that isn't there.
                raise
            raise SlugTakenError(link.slug) from e
        return link

    def update(self, link: BookingLink) -> BookingLink:
        stmt = select(BookingLinkRow).where(
            BookingLinkRow.id == link.id,
            BookingLinkRow.user_id == link.user_id,
            BookingLinkRow.deleted_at.is_(None),
        )
        row = self._session.execute(stmt).scalar_one()
        row.host_name = link.host_name
        row.title = link.title
        row.description = link.description
        row.duration_minutes = link.duration_minutes
        row.is_active = link.is_active
        row.updated_at = utc_now()
        self._session.flush()
        return _row_to_link(row)

    def delete(self, link_id: str, user_id: str) -> bool:
        # Tombstone, not a hard delete: the row -- and the UNIQUE(slug)
        # constraint on it -- stays, so the slug can never be reclaimed.
        now = utc_now()
        # cast: Session.execute is typed Result[Any]; an UPDATE returns a
        # CursorResult, which is what carries rowcount (same as appointment.py).
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(BookingLinkRow)
                .where(
                    BookingLinkRow.id == link_id,
                    BookingLinkRow.user_id == user_id,
                    BookingLinkRow.deleted_at.is_(None),
                )
                .values(deleted_at=now, is_active=False, updated_at=now)
            ),
        )
        self._session.flush()
        return bool(result.rowcount)
