# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL InboxItemStateRepository implementation.

Every query names ``user_id`` beside the row policy that enforces it, so a
query that forgot its filter still reads nothing of anybody else's.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import and_, exists, or_, select, tuple_, update

from ...db.models import InboxItemStateRow
from ...models.inbox import DISPOSITION_RESTORED, DISPOSITION_SNOOZED, InboxItemState
from ..inbox_item_state import InboxItemStateRepository

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from sqlalchemy import ColumnElement
    from sqlalchemy.orm import Session

    from ..inbox_item_state import ItemKey


def _hiding(now: datetime) -> ColumnElement[bool]:
    """:func:`app.models.inbox.hides`, as SQL over the live row."""
    return and_(
        InboxItemStateRow.disposition != DISPOSITION_RESTORED,
        or_(
            InboxItemStateRow.disposition != DISPOSITION_SNOOZED,
            InboxItemStateRow.snoozed_until > now,
        ),
    )


def hidden_by_state(
    source_kind: str, source_id: ColumnElement[str], user_id: str, now: datetime
) -> ColumnElement[bool]:
    """``EXISTS`` a live state hiding this item from *user_id*.

    For a source whose candidate rows accumulate — client messages stay in an
    open conversation long after they are answered — so it can leave handled
    rows out in SQL instead of reading them all back to discard them.
    """
    return exists().where(
        InboxItemStateRow.user_id == user_id,
        InboxItemStateRow.source_kind == source_kind,
        InboxItemStateRow.source_id == source_id,
        InboxItemStateRow.superseded_by.is_(None),
        _hiding(now),
    )


def _state(row: InboxItemStateRow) -> InboxItemState:
    return InboxItemState(
        id=row.id,
        user_id=row.user_id,
        source_kind=row.source_kind,
        source_id=row.source_id,
        disposition=row.disposition,
        resolved_at=row.resolved_at,
        resolved_by=row.resolved_by,
        snoozed_until=row.snoozed_until,
    )


class PostgresInboxItemStateRepository(InboxItemStateRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def _live(self, user_id: str) -> tuple[ColumnElement[bool], ...]:
        return (
            InboxItemStateRow.user_id == user_id,
            InboxItemStateRow.superseded_by.is_(None),
        )

    def live_states(self, user_id: str, keys: Iterable[ItemKey]) -> dict[ItemKey, InboxItemState]:
        wanted = list(set(keys))
        if not wanted:
            return {}
        rows = self._session.execute(
            select(InboxItemStateRow).where(
                *self._live(user_id),
                tuple_(InboxItemStateRow.source_kind, InboxItemStateRow.source_id).in_(wanted),
            )
        ).scalars()
        return {(row.source_kind, row.source_id): _state(row) for row in rows}

    def list_hidden(
        self, user_id: str, now: datetime, *, kinds: set[str] | None, limit: int
    ) -> list[InboxItemState]:
        query = select(InboxItemStateRow).where(*self._live(user_id), _hiding(now))
        if kinds is not None:
            query = query.where(InboxItemStateRow.source_kind.in_(kinds))
        rows = self._session.execute(
            query.order_by(InboxItemStateRow.resolved_at.desc()).limit(limit)
        ).scalars()
        return [_state(row) for row in rows]

    def record(
        self,
        user_id: str,
        key: ItemKey,
        disposition: str,
        now: datetime,
        *,
        snoozed_until: datetime | None = None,
    ) -> InboxItemState:
        new_id = str(uuid.uuid4())
        # Retire the live row first: the partial unique index is checked per
        # statement, so there is never a moment with two live rows.
        self._session.execute(
            update(InboxItemStateRow)
            .where(
                *self._live(user_id),
                InboxItemStateRow.source_kind == key[0],
                InboxItemStateRow.source_id == key[1],
            )
            .values(superseded_by=new_id, superseded_at=now)
        )
        row = InboxItemStateRow(
            id=new_id,
            user_id=user_id,
            source_kind=key[0],
            source_id=key[1],
            disposition=disposition,
            snoozed_until=snoozed_until,
            resolved_by=user_id,
            resolved_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return _state(row)
