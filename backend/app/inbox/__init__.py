# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The Inbox: one list of everything that needs the clinician.

Items stay in their sources' tables; see :mod:`app.inbox.registry` for the
seams and :mod:`app.inbox.service` for how the list is put together.
"""

from __future__ import annotations

from .registry import (
    InboxContext,
    InboxEnricher,
    InboxRegistry,
    InboxSource,
    get_inbox_registry,
    register_inbox_enricher,
    register_inbox_source,
)

__all__ = [
    "InboxContext",
    "InboxEnricher",
    "InboxRegistry",
    "InboxSource",
    "get_inbox_registry",
    "register_inbox_enricher",
    "register_inbox_source",
]
