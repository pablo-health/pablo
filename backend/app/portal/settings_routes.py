# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether a practice offers its clients the portal, as its clinicians set it.

* ``GET``/``PUT /api/portal/settings`` — CLINICIAN, inside their own practice.

The practice is resolved from the caller, never taken from the request, so a
clinician can only ever change their own practice's portal. Any clinician of
the practice may change it, as with the practice's portal welcome and
invitation wording.

Turning the portal on mints the practice's portal address if it has none, so
the first invitation has somewhere to lead. Turning it off takes effect on the
next request any client makes; see :mod:`app.portal.portal_settings`.

Not PHI: a switch.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..api_errors import ConflictError
from ..auth.service import _resolve_practice_from_email, require_active_subscription
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..services.audit_service import AuditService, get_audit_service
from .portal_settings import PortalSettings, PortalSettingsStore, get_portal_settings_store
from .practice_routes import ensure_practice_slug

router = APIRouter(tags=["patient-portal"])


class PortalSettingsResponse(BaseModel):
    enabled: bool
    #: Whether the practice has ever answered. False only for a practice that
    #: has never been asked.
    decided: bool


class PortalSettingsRequest(BaseModel):
    enabled: bool


def _practice_id(user: User) -> str:
    practice = _resolve_practice_from_email(user.email)
    if practice is None:
        raise ConflictError("No practice is associated with this account yet.")
    return practice[0]


def _response(settings: PortalSettings) -> PortalSettingsResponse:
    return PortalSettingsResponse(enabled=settings.enabled, decided=settings.decided_at is not None)


@router.get("/api/portal/settings", response_model=PortalSettingsResponse)
def get_portal_settings(
    user: Annotated[User, Depends(require_active_subscription)],
    store: Annotated[PortalSettingsStore, Depends(get_portal_settings_store)],
) -> PortalSettingsResponse:
    """Whether the caller's practice offers the portal."""
    return _response(store.get(_practice_id(user)))


@router.put("/api/portal/settings", response_model=PortalSettingsResponse)
def save_portal_settings(
    body: PortalSettingsRequest,
    request: Request,
    user: Annotated[User, Depends(require_active_subscription)],
    store: Annotated[PortalSettingsStore, Depends(get_portal_settings_store)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> PortalSettingsResponse:
    """Turn the practice's portal on or off."""
    practice_id = _practice_id(user)
    before = store.get(practice_id)
    if body.enabled:
        # Before the switch flips, so a practice never reads as offering a
        # portal it has no address for.
        ensure_practice_slug(practice_id)
    after = store.set_enabled(practice_id, enabled=body.enabled, by=user.id)
    if before.enabled != after.enabled:
        audit.log(
            AuditAction.PRACTICE_PORTAL_OFFERING_CHANGED,
            user,
            request,
            resource_type=ResourceType.PRACTICE_PORTAL,
            resource_id=practice_id,
            changes={"enabled": {"old": before.enabled, "new": after.enabled}},
        )
    return _response(after)
