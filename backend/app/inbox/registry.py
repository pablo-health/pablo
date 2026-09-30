# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The Inbox's two seams: where items come from, and what is added to them.

**Sources.** Each kind of item has one source, which answers "what of mine
needs this clinician?" from its own table. The Inbox holds no copy, so an
item handled where it lives — a refill answered on the Refills page, a note
signed on its session — leaves the Inbox because its source stops listing
it, with nothing to keep in step.

**Enrichers.** Given the assembled items, an enricher may return them with
more filled in (a severity, a suggested reply). The engine registers none.

Both follow the hook pattern used for messages and claim events: a
deployment registers at startup, the engine's own built-ins included (see
:mod:`app.inbox.sources`). Registering a source under a kind that is already
registered replaces it, so a deployment can swap one out without the engine
knowing. :meth:`InboxRegistry.clear` exists for tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from ..models.inbox import InboxItem


@dataclass(frozen=True)
class InboxContext:
    """Who is asking, and when. A source reads its rows as this clinician."""

    user_id: str
    now: datetime


@runtime_checkable
class InboxSource(Protocol):
    """One kind of Inbox item, read from the table it lives in.

    Runs inside the clinician's request, on its tenant-scoped session, so a
    source reaches its rows the way that kind's own routes do and is scoped
    by the same grants.
    """

    kind: str

    def list_open(self, ctx: InboxContext) -> list[InboxItem]:
        """Everything of this kind still waiting on the clinician.

        What the source itself considers unfinished — a refill still
        ``requested``. The Inbox applies dismissals and snoozes on top, so a
        source need not know about them.
        """
        ...

    def get_items(self, ctx: InboxContext, source_ids: Iterable[str]) -> list[InboxItem]:
        """These items, whatever state their source now has them in.

        For the Done view and for acting on one item, so a refill answered
        after it was dismissed still reads correctly there. An id the
        clinician cannot see is left out, exactly as one that does not exist.
        """
        ...


@runtime_checkable
class InboxEnricher(Protocol):
    """Fills in more on items that already exist. Never adds or removes one."""

    def enrich(self, items: list[InboxItem], ctx: InboxContext) -> list[InboxItem]: ...


class InboxRegistry:
    """The sources and enrichers a deployment registered, in order."""

    def __init__(self) -> None:
        self._sources: dict[str, InboxSource] = {}
        self._enrichers: list[InboxEnricher] = []

    def register_source(self, source: InboxSource) -> None:
        self._sources[source.kind] = source

    def register_enricher(self, enricher: InboxEnricher) -> None:
        self._enrichers.append(enricher)

    def clear(self) -> None:
        """Drop every registration. For test isolation."""
        self._sources.clear()
        self._enrichers.clear()

    def source(self, kind: str) -> InboxSource | None:
        return self._sources.get(kind)

    @property
    def sources(self) -> tuple[InboxSource, ...]:
        return tuple(self._sources.values())

    @property
    def enrichers(self) -> tuple[InboxEnricher, ...]:
        return tuple(self._enrichers)


_registry = InboxRegistry()


def get_inbox_registry() -> InboxRegistry:
    """The process-wide registry. A FastAPI dependency and a startup handle."""
    return _registry


def register_inbox_source(source: InboxSource) -> None:
    _registry.register_source(source)


def register_inbox_enricher(enricher: InboxEnricher) -> None:
    _registry.register_enricher(enricher)
