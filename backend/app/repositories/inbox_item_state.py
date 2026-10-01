# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Inbox item state repository: what each clinician did with each item.

Every verb takes the acting clinician's ``user_id`` and touches only that
clinician's rows; the row policy says the same thing underneath. A write
never edits a row in place — it supersedes the live one and inserts its
successor, so how an item was handled, and undone, stays on record.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..models.inbox import InboxItemState, hides

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    ItemKey = tuple[str, str]


class InboxItemStateRepository(ABC):
    @abstractmethod
    def live_states(self, user_id: str, keys: Iterable[ItemKey]) -> dict[ItemKey, InboxItemState]:
        """The live state of each ``(source_kind, source_id)`` that has one."""

    @abstractmethod
    def list_hidden(
        self, user_id: str, now: datetime, *, kinds: set[str] | None, limit: int
    ) -> list[InboxItemState]:
        """Live states that keep their item out of the Open view, newest first.

        What the Done view lists. ``kinds`` narrows it; ``None`` is every kind.
        """

    @abstractmethod
    def record(
        self,
        user_id: str,
        key: ItemKey,
        disposition: str,
        now: datetime,
        *,
        snoozed_until: datetime | None = None,
    ) -> InboxItemState:
        """Make *disposition* the item's live state, superseding the previous one."""


class InMemoryInboxItemStateRepository(InboxItemStateRepository):
    """In-memory repository for unit tests. Keeps every row, live or not."""

    def __init__(self) -> None:
        self.rows: list[tuple[InboxItemState, str | None]] = []

    def _live(self, user_id: str) -> list[InboxItemState]:
        return [
            row
            for row, superseded_by in self.rows
            if superseded_by is None and row.user_id == user_id
        ]

    def live_states(self, user_id: str, keys: Iterable[ItemKey]) -> dict[ItemKey, InboxItemState]:
        wanted = set(keys)
        return {
            (row.source_kind, row.source_id): row
            for row in self._live(user_id)
            if (row.source_kind, row.source_id) in wanted
        }

    def list_hidden(
        self, user_id: str, now: datetime, *, kinds: set[str] | None, limit: int
    ) -> list[InboxItemState]:
        rows = [
            row
            for row in self._live(user_id)
            if hides(row, now) and (kinds is None or row.source_kind in kinds)
        ]
        rows.sort(key=lambda row: row.resolved_at, reverse=True)
        return rows[:limit]

    def record(
        self,
        user_id: str,
        key: ItemKey,
        disposition: str,
        now: datetime,
        *,
        snoozed_until: datetime | None = None,
    ) -> InboxItemState:
        state = InboxItemState(
            id=str(uuid.uuid4()),
            user_id=user_id,
            source_kind=key[0],
            source_id=key[1],
            disposition=disposition,
            resolved_at=now,
            resolved_by=user_id,
            snoozed_until=snoozed_until,
        )
        self.rows = [
            (row, state.id if superseded_by is None and _same(row, user_id, key) else superseded_by)
            for row, superseded_by in self.rows
        ]
        self.rows.append((state, None))
        return state


def _same(row: InboxItemState, user_id: str, key: ItemKey) -> bool:
    return row.user_id == user_id and (row.source_kind, row.source_id) == key
