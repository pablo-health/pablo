# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading the public practice directory, in both directions.

``platform.companion_practice_slugs`` maps a practice's public address to
the practice. Two routes need to walk that map and neither can reach it the
ordinary way:

* Recovery arrives with a slug out of a URL and no principal at all, so the
  practice — and therefore the schema its patients live in — has to be
  resolved from the slug before any tenant session can be opened. Exactly
  the inversion :class:`~app.db.platform_models.PortalPracticeSlugRow` exists
  for.
* The capability document arrives with a patient principal, which names a
  schema and not a practice, and wants the practice's own display name for
  the header. The same map, read backwards.

Both lookups run on a standalone platform session: the reader is either
unauthenticated or tenant-scoped, and in neither case is the request's own
session pointed at ``platform``.

**None of this is PHI.** A slug, a practice id, a schema name and a
practice's own business name. No chart is opened here, and nothing in this
module takes an email address or a patient id.

**Neither function distinguishes "no such practice" from "portal disabled".**
Both answer ``None``, the same way
:func:`~app.portal.practice_routes.resolve_portal_practice` answers one 404
for both — which of a deployment's practices have turned their portal off is
not a fact this surface hands out.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from ..db import create_standalone_session
from ..db.platform_models import PortalPracticeSlugRow, PracticeRow

logger = logging.getLogger(__name__)


def practice_schema_for_slug(slug: str) -> str | None:
    """The Postgres schema a practice's patients live in, from its address.

    ``None`` when the slug names nothing, names a practice whose portal is
    off, or names a practice that is inactive or deleted — one answer for
    all of them, and the caller must not tell them apart either.

    The returned value goes on to select a schema, so the filters here are
    the ones that decide whether a tenant session is opened at all: a
    deleted practice's schema may not exist, and an inactive practice is one
    the deployment has already stopped serving.
    """
    session = create_standalone_session()
    try:
        return session.execute(
            select(PracticeRow.schema_name)
            .join(PortalPracticeSlugRow, PortalPracticeSlugRow.practice_id == PracticeRow.id)
            .where(
                PortalPracticeSlugRow.slug == slug,
                PortalPracticeSlugRow.enabled.is_(True),
                PracticeRow.is_active.is_(True),
                PracticeRow.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
    finally:
        session.close()


def practice_display_name_for_schema(schema: str) -> str | None:
    """The practice's own name, for the header a signed-in patient sees.

    Read from the same directory the unauthenticated slug route answers
    from, so the header before sign-in and the header after it cannot
    disagree.

    A failure answers ``None`` rather than raising. This name is decoration
    on a document whose load-bearing half — which modules work — came from
    the route table and cannot fail; taking the whole response down over a
    missing display name would turn a blank header into a broken portal.
    """
    if not schema:
        return None
    session = create_standalone_session()
    try:
        return session.execute(
            select(PortalPracticeSlugRow.display_name)
            .join(PracticeRow, PracticeRow.id == PortalPracticeSlugRow.practice_id)
            .where(
                PracticeRow.schema_name == schema,
                PortalPracticeSlugRow.enabled.is_(True),
            )
        ).scalar_one_or_none()
    except Exception:
        logger.warning("Portal practice display-name lookup failed")
        return None
    finally:
        session.close()


__all__ = ["practice_display_name_for_schema", "practice_schema_for_slug"]
