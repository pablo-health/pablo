# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a practice's portal takes from the practice's website, on the
practice's own host: the theme it wears, and the address it links back to.

One place decides each, so a new source is one more line here. Both come from
the practice's live website version (:mod:`app.sites`): with nothing live, the
portal keeps its own look and links to no website. Unpublishing a website
therefore takes both away, and rolling back brings back the theme that version
had.

No PHI: a practice's public colors, fonts and website host.
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


def live_site_host(session: Session, practice_id: str) -> str | None:
    """The working host the practice's live website is served at — the primary,
    else the oldest — or ``None`` when nothing is published or no host works.
    The same host Settings > Website calls "live"."""
    store = PracticeSiteStore(session)
    site = store.get(practice_id)
    if site is None or site.live_version is None:
        return None
    hosts = store.active_site_hosts(practice_id)
    return hosts[0].domain if hosts else None
