# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The theme a practice's portal wears on the practice's own host.

One place decides it, so a new source is one more line here: today it is the
theme kept with the practice's live website version (:mod:`app.sites.theme`),
and with no live version, or one without a theme, the portal keeps its own
look. Unpublishing a website therefore takes its theme away, and rolling back
brings back the one that version had.

No PHI: a practice's public colors and fonts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..sites.store import PracticeSiteStore
from ..sites.theme import stored_theme

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..sites.theme import PracticeTheme


def portal_theme(session: Session, practice_id: str) -> PracticeTheme | None:
    """The theme the practice's portal wears, or ``None`` for the portal's own look."""
    store = PracticeSiteStore(session)
    site = store.get(practice_id)
    if site is None or site.live_version is None:
        return None
    live = store.version(practice_id, site.live_version)
    return stored_theme(live.theme) if live else None
