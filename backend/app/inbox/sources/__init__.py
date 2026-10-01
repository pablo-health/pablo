# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The engine's own Inbox sources, registered at startup like anybody else's."""

from __future__ import annotations

from ..registry import InboxRegistry, get_inbox_registry
from .calendar_changes import CalendarChangeSource
from .intake_reviews import IntakeReviewSource
from .notes_to_sign import NoteToSignSource
from .portal_messages import PortalMessageSource
from .refills import RefillSource


def register_builtin_sources(registry: InboxRegistry | None = None) -> None:
    """Register every source this engine ships. Safe to call twice.

    A kind somebody already registered is left alone, so a deployment's own
    source for it wins whether it registered before the engine started or
    after.
    """
    target = registry or get_inbox_registry()
    for source in (
        PortalMessageSource(),
        RefillSource(),
        IntakeReviewSource(),
        NoteToSignSource(),
        CalendarChangeSource(),
    ):
        if target.source(source.kind) is None:
            target.register_source(source)


__all__ = [
    "CalendarChangeSource",
    "IntakeReviewSource",
    "NoteToSignSource",
    "PortalMessageSource",
    "RefillSource",
    "register_builtin_sources",
]
