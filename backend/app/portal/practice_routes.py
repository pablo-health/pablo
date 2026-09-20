# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The public practice directory the portal address resolves through.

Two routes, and — same posture as :mod:`app.portal.routes` — they do not share
a principal:

* ``GET /api/portal/practices/{slug}`` — UNAUTHENTICATED. The shell's bootstrap
  call: given the slug out of its own URL, what practice is this and what
  should the header say? Returns ``{slug, display_name}`` and nothing else.
  Unknown and disabled slugs are the SAME 404 — a slug is not a credential, but
  which practices exist and which have turned the portal off is not this
  endpoint's to reveal either.
* ``POST /api/portal/practice-slug`` — CLINICIAN, inside their own practice.
  Idempotent get-or-create: mints, or returns, the one address a practice's
  portal links resolve through, so the invite affordance has something to build
  a link with.

Neither route is PHI. A slug, a practice id and a practice's own display name
are business identifiers; nothing here reads a chart.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..auth.route_security import truly_public
from ..auth.service import _resolve_practice_from_email, require_active_subscription
from ..db import create_standalone_session
from ..db.platform_models import PortalPracticeSlugRow, PracticeRow
from ..models import User
from ..rate_limit import require_portal_practice_resolve_rate_limit
from ..utcnow import utc_now

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

router = APIRouter(tags=["patient-portal"])

_MIN_SLUG_LEN = 3
_MAX_SLUG_LEN = 63
_MAX_SLUG_ATTEMPTS = 25
_SLUG_COLLAPSE_RE = re.compile(r"[^a-z0-9]+")
# SQLSTATE 23505, unique_violation. The table's only unique constraints are the
# slug primary key and practice_id, and a fresh insert always uses a
# not-yet-seen practice_id (idempotency is checked first), so a 23505 here is a
# slug collision.
_UNIQUE_VIOLATION = "23505"

# The handful of path segments that would be confusing or actively misleading
# as a practice's own address. Not a moderation list — these are words the
# shell's own routing already gives meaning to, or that read as platform
# surface rather than as a practice. ``redeem`` is the load-bearing one: it is
# the segment a magic link lands on (see ``app.portal.factory``).
_RESERVED_SLUGS = frozenset(
    {
        "api",
        "app",
        "admin",
        "auth",
        "redeem",
        "refresh",
        "practice",
        "practices",
        "www",
        "static",
        "assets",
    }
)


class PortalPracticeResolution(BaseModel):
    """What the shell learns from a slug — nothing more."""

    slug: str
    display_name: str


class PortalPracticeSlugResponse(BaseModel):
    """The address a practice's portal links resolve through."""

    slug: str


def _practice_not_found() -> HTTPException:
    """The single 404 for both an unknown slug and a disabled one.

    Distinguishable responses would make this an oracle for which practices
    exist and which have turned the portal off. That is also why the response
    carries no ``enabled`` field: a caller who could read one would learn the
    difference the 404 exists to withhold.
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")


@router.get(
    "/api/portal/practices/{slug}",
    response_model=PortalPracticeResolution,
    dependencies=[Depends(require_portal_practice_resolve_rate_limit)],
)
def resolve_portal_practice(
    slug: str,
    _public: None = Depends(truly_public),
) -> PortalPracticeResolution:
    """Resolve a slug to the display name the shell should show.

    Rate-limited by address before the slug ever reaches a query — the route
    dependency runs first, the same ordering the public booking surface uses.
    Unauthenticated by design: this is the shell's very first call, before any
    session exists.

    ``truly_public`` states that posture where the guardrail can see it. No
    subscription is in scope to check: there is no signed-in practitioner here,
    and the response is a practice's own display name.
    """
    session = create_standalone_session()
    try:
        row = session.get(PortalPracticeSlugRow, slug)
    finally:
        session.close()

    if row is None or not row.enabled:
        raise _practice_not_found()
    return PortalPracticeResolution(slug=row.slug, display_name=row.display_name)


@router.post(
    "/api/portal/practice-slug",
    response_model=PortalPracticeSlugResponse,
)
def get_or_create_portal_practice_slug(
    user: Annotated[User, Depends(require_active_subscription)],
) -> PortalPracticeSlugResponse:
    """Mint, or return, the caller's practice's portal address. Idempotent.

    A practice has at most one address (``practice_id`` is UNIQUE), so a second
    call from the same practice returns the first one unchanged rather than
    minting another. ``User`` carries no practice of its own, so it is resolved
    from the caller's address through the platform email mapping.
    """
    practice = _resolve_practice_from_email(user.email)
    if practice is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No practice is associated with this account yet.",
        )
    practice_id, _schema_name = practice

    session = create_standalone_session()
    try:
        existing = session.execute(
            select(PortalPracticeSlugRow).where(PortalPracticeSlugRow.practice_id == practice_id)
        ).scalar_one_or_none()
        if existing is not None:
            return PortalPracticeSlugResponse(slug=existing.slug)

        practice_row = session.get(PracticeRow, practice_id)
        if practice_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such practice.")

        base = _slugify(practice_row.name)
        now = utc_now()
        for candidate in _candidate_slugs(base):
            if candidate in _RESERVED_SLUGS:
                continue
            row = PortalPracticeSlugRow(
                slug=candidate,
                practice_id=practice_id,
                display_name=practice_row.name,
                enabled=True,
                created_at=now,
            )
            try:
                with session.begin_nested():
                    session.add(row)
                    session.flush()
            except IntegrityError as e:
                session.rollback()
                if getattr(e.orig, "pgcode", None) != _UNIQUE_VIOLATION:
                    raise
                continue
            session.commit()
            return PortalPracticeSlugResponse(slug=candidate)

        # Every candidate in the budget collided — practically unreachable (it
        # means every numeric suffix of the same base is already taken), but
        # failing loudly beats minting nothing.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not mint a portal address for this practice.",
        )
    finally:
        session.close()


def _slugify(name: str) -> str:
    """A lowercase, hyphenated, length-bounded starting point for a slug.

    Not guaranteed unique or unreserved on its own — :func:`_candidate_slugs`
    and the reserved-word check handle that. Falls back to ``practice`` when
    the name collapses to nothing printable.
    """
    base = _SLUG_COLLAPSE_RE.sub("-", name.strip().lower()).strip("-")
    base = base[:_MAX_SLUG_LEN].rstrip("-")
    if len(base) < _MIN_SLUG_LEN:
        base = "practice"
    return base


def _candidate_slugs(base: str) -> Iterator[str]:
    """``base``, then ``base-2``, ``base-3``, … up to the attempt budget."""
    yield base
    for n in range(2, _MAX_SLUG_ATTEMPTS + 1):
        suffix = f"-{n}"
        trimmed = base[: _MAX_SLUG_LEN - len(suffix)]
        yield f"{trimmed}{suffix}"
