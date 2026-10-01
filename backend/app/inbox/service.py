# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Assembling the Inbox from its sources, and remembering what was done.

**Open** is every source's unfinished items, less the ones this clinician
dismissed, marked handled, replied to, or snoozed into the future. **Done**
is the other side of the same line: the items with a live state that hides
them, newest first, read back from their sources so each still says what
it is. Nothing is ever deleted — restoring an item supersedes its state
with ``restored``, and it is open again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models.inbox import InboxItem, InboxItemState, hides

if TYPE_CHECKING:
    from datetime import datetime

    from ..models.inbox import InboxView
    from ..repositories import InboxItemStateRepository
    from .registry import InboxContext, InboxRegistry, InboxSource

#: The most items the Done view lists. It is there to undo what was just
#: handled, not to page through history.
DONE_LIMIT = 100


class UnknownInboxItemError(Exception):
    """No registered source has this item, or not for this clinician."""


def _with_state(item: InboxItem, state: InboxItemState) -> InboxItem:
    return item.model_copy(
        update={
            "disposition": state.disposition,
            "resolved_at": state.resolved_at,
            "snoozed_until": state.snoozed_until,
        }
    )


def _open_order(items: list[InboxItem]) -> list[InboxItem]:
    """Urgent first, then newest first."""
    newest = sorted(items, key=lambda item: item.occurred_at, reverse=True)
    return sorted(newest, key=lambda item: item.severity != "urgent")


class InboxService:
    def __init__(self, registry: InboxRegistry, states: InboxItemStateRepository) -> None:
        self._registry = registry
        self._states = states

    def _sources(self, kinds: set[str] | None) -> list[InboxSource]:
        return [s for s in self._registry.sources if kinds is None or s.kind in kinds]

    def _enrich(self, items: list[InboxItem], ctx: InboxContext) -> list[InboxItem]:
        for enricher in self._registry.enrichers:
            items = enricher.enrich(items, ctx)
        return items

    def list_items(
        self, ctx: InboxContext, view: InboxView, kinds: set[str] | None = None
    ) -> list[InboxItem]:
        if view == "open":
            return _open_order(self._enrich(self.open_items(ctx, kinds), ctx))
        return self._enrich(self._done_items(ctx, kinds), ctx)

    def open_items(self, ctx: InboxContext, kinds: set[str] | None = None) -> list[InboxItem]:
        items = [item for source in self._sources(kinds) for item in source.list_open(ctx)]
        states = self._states.live_states(ctx.user_id, [(i.kind, i.source_id) for i in items])
        return [
            item
            for item in items
            if (state := states.get((item.kind, item.source_id))) is None
            or not hides(state, ctx.now)
        ]

    def count(self, ctx: InboxContext) -> int:
        return len(self.open_items(ctx))

    def _done_items(self, ctx: InboxContext, kinds: set[str] | None) -> list[InboxItem]:
        registered = {source.kind for source in self._sources(kinds)}
        hidden = self._states.list_hidden(ctx.user_id, ctx.now, kinds=registered, limit=DONE_LIMIT)
        by_kind: dict[str, list[InboxItemState]] = {}
        for state in hidden:
            by_kind.setdefault(state.source_kind, []).append(state)
        found: dict[tuple[str, str], InboxItem] = {}
        for kind, states in by_kind.items():
            source = self._registry.source(kind)
            if source is None:
                continue
            for fetched in source.get_items(ctx, [s.source_id for s in states]):
                found[(fetched.kind, fetched.source_id)] = fetched
        return [
            _with_state(item, state)
            for state in hidden
            if (item := found.get((state.source_kind, state.source_id))) is not None
        ]

    def find(self, ctx: InboxContext, kind: str, source_id: str) -> InboxItem:
        """The item, whatever state it is in, or :class:`UnknownInboxItemError`."""
        source = self._registry.source(kind)
        items = source.get_items(ctx, [source_id]) if source is not None else []
        if not items:
            raise UnknownInboxItemError(f"{kind}:{source_id}")
        return items[0]

    def record(
        self,
        ctx: InboxContext,
        item: InboxItem,
        disposition: str,
        *,
        snoozed_until: datetime | None = None,
    ) -> InboxItem:
        state = self._states.record(
            ctx.user_id,
            (item.kind, item.source_id),
            disposition,
            ctx.now,
            snoozed_until=snoozed_until,
        )
        return _with_state(item, state)
