# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's portal welcome, as its clinicians edit it.

* ``GET``/``PUT``/``DELETE /api/portal/welcome`` — CLINICIAN. The practice's
  own welcome: read it (or the default), save it, or go back to the default.

The response carries the practice's name as the portal will fill it in, so
the editor's preview can be drawn in the browser without a round trip per
keystroke. What a client actually receives comes from the capability document
(:mod:`app.portal.account_routes`), rendered by the same function.

Not PHI: text a practice wrote about itself.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..api_errors import ConflictError, UnprocessableEntityError
from ..auth.service import _resolve_practice_from_email, require_active_subscription
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from .practice_routes import ensure_practice_slug
from .welcome import (
    DEFAULT_WELCOME,
    PLACEHOLDERS,
    UNNAMED_PRACTICE,
    PortalWelcome,
    welcome_problems,
)
from .welcome_store import PortalWelcomeStore, get_portal_welcome_store

router = APIRouter(tags=["patient-portal"])


class WelcomePlaceholder(BaseModel):
    name: str
    label: str


class PortalWelcomeResponse(BaseModel):
    heading: str
    body: str
    is_default: bool
    #: What ``{practice_name}`` becomes in the portal, for the preview.
    practice_name: str
    placeholders: list[WelcomePlaceholder]


class PortalWelcomeRequest(BaseModel):
    # Generous outer bounds; the real limits are checked by
    # ``welcome_problems`` so a clinician gets words, not a schema error.
    heading: str = Field(max_length=1000)
    body: str = Field(max_length=20_000)


def _practice_id(user: User) -> str:
    practice = _resolve_practice_from_email(user.email)
    if practice is None:
        raise ConflictError("No practice is associated with this account yet.")
    return practice[0]


def _welcome_response(stored: PortalWelcome | None, practice_id: str) -> PortalWelcomeResponse:
    welcome = stored or DEFAULT_WELCOME
    name = ensure_practice_slug(practice_id).display_name.strip() or UNNAMED_PRACTICE
    return PortalWelcomeResponse(
        heading=welcome.heading,
        body=welcome.body,
        is_default=stored is None,
        practice_name=name,
        placeholders=[
            WelcomePlaceholder(name=placeholder, label=label)
            for placeholder, label in PLACEHOLDERS.items()
        ],
    )


@router.get("/api/portal/welcome", response_model=PortalWelcomeResponse)
def get_portal_welcome(
    user: Annotated[User, Depends(require_active_subscription)],
    store: Annotated[PortalWelcomeStore, Depends(get_portal_welcome_store)],
) -> PortalWelcomeResponse:
    """The practice's portal welcome, or the default."""
    practice_id = _practice_id(user)
    return _welcome_response(store.get(practice_id), practice_id)


@router.put("/api/portal/welcome", response_model=PortalWelcomeResponse)
def save_portal_welcome(
    body: PortalWelcomeRequest,
    user: Annotated[User, Depends(require_active_subscription)],
    store: Annotated[PortalWelcomeStore, Depends(get_portal_welcome_store)],
) -> PortalWelcomeResponse:
    """Save the practice's welcome. 422 with what to fix."""
    welcome = PortalWelcome(heading=body.heading, body=body.body)
    problems = welcome_problems(welcome)
    if problems:
        raise UnprocessableEntityError(
            " ".join(problems), {"problems": problems}, code="PORTAL_WELCOME_INVALID"
        )
    practice_id = _practice_id(user)
    store.save(practice_id, welcome)
    return _welcome_response(store.get(practice_id), practice_id)


@router.delete("/api/portal/welcome", response_model=PortalWelcomeResponse)
def reset_portal_welcome(
    user: Annotated[User, Depends(require_active_subscription)],
    store: Annotated[PortalWelcomeStore, Depends(get_portal_welcome_store)],
) -> PortalWelcomeResponse:
    """Go back to the default welcome."""
    practice_id = _practice_id(user)
    store.reset(practice_id)
    return _welcome_response(None, practice_id)
