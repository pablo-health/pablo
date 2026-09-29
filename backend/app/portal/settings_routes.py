# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether a practice offers its clients the portal, and which parts of it.

* ``GET``/``PUT /api/portal/settings`` — CLINICIAN, inside their own practice.

The practice is resolved from the caller, never taken from the request, so a
clinician can only ever change their own practice's portal. Any clinician of
the practice may change it, as with the practice's portal welcome and
invitation wording.

Turning the portal on mints the practice's portal address if it has none, so
the first invitation has somewhere to lead. Turning it off takes effect on the
next request any client makes; see :mod:`app.portal.portal_settings`.

The modules a practice can choose are the ones this deployment actually
serves — configured and mounted — less the ones with a gate of their own
(chat). A practice can turn those off for its clients; it can never turn on
one the deployment does not serve.

Not PHI: a switch and module names.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..api_errors import ConflictError, UnprocessableEntityError
from ..auth.service import _resolve_practice_from_email, require_active_subscription
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..services.audit_service import AuditService, get_audit_service
from ..settings import get_settings
from .modules import known_modules, mounted_modules_on
from .portal_settings import (
    PortalSettings,
    PortalSettingsStore,
    choosable_modules,
    get_portal_settings_store,
    practice_offers_module,
)
from .practice_routes import ensure_practice_slug

router = APIRouter(tags=["patient-portal"])


class PortalSettingsResponse(BaseModel):
    enabled: bool
    #: Whether the practice has ever answered. False only for a practice that
    #: has never been asked.
    decided: bool
    #: Every module the practice can choose, in the order clients meet them,
    #: and whether it is on.
    modules: dict[str, bool]


class PortalSettingsRequest(BaseModel):
    enabled: bool | None = None
    #: Modules to turn on or off. Ones left out keep their setting.
    modules: dict[str, bool] | None = None
    #: Apply this only if the practice has never answered. The first-client
    #: prompt sends it: a "not now" from a screen loaded before a colleague
    #: turned the portal on must not turn it off for the whole practice.
    only_if_undecided: bool = False


def _practice_id(user: User) -> str:
    practice = _resolve_practice_from_email(user.email)
    if practice is None:
        raise ConflictError("No practice is associated with this account yet.")
    return practice[0]


def _choosable(request: Request) -> tuple[str, ...]:
    """The modules this deployment serves that a practice may choose, in order."""
    served = set(known_modules(get_settings().portal_module_names))
    served &= mounted_modules_on(request.app)
    return choosable_modules(known_modules(served))


def _modules_on(settings: PortalSettings, choosable: tuple[str, ...]) -> dict[str, bool]:
    return {name: practice_offers_module(settings, name) for name in choosable}


def _response(settings: PortalSettings, choosable: tuple[str, ...]) -> PortalSettingsResponse:
    return PortalSettingsResponse(
        enabled=settings.enabled,
        decided=settings.decided_at is not None,
        modules=_modules_on(settings, choosable),
    )


def _chosen_modules(
    requested: dict[str, bool], current: dict[str, bool], choosable: tuple[str, ...]
) -> tuple[str, ...]:
    """The modules on after applying *requested* to *current*. 422 on a name
    the practice cannot choose, or when nothing would be left on."""
    unknown = sorted(set(requested) - set(choosable))
    if unknown:
        names = ", ".join(unknown)
        raise UnprocessableEntityError(
            f"This portal does not offer {names}.",
            {"modules": unknown},
            code="PORTAL_MODULE_NOT_SERVED",
        )
    merged = {**current, **requested}
    chosen = tuple(name for name in choosable if merged[name])
    if not chosen:
        raise UnprocessableEntityError(
            "Keep at least one thing clients can do in the portal.",
            {},
            code="PORTAL_MODULES_EMPTY",
        )
    return chosen


@router.get("/api/portal/settings", response_model=PortalSettingsResponse)
def get_portal_settings(
    request: Request,
    user: Annotated[User, Depends(require_active_subscription)],
    store: Annotated[PortalSettingsStore, Depends(get_portal_settings_store)],
) -> PortalSettingsResponse:
    """Whether the caller's practice offers the portal, and which parts."""
    return _response(store.get(_practice_id(user)), _choosable(request))


@router.put("/api/portal/settings", response_model=PortalSettingsResponse)
def save_portal_settings(
    body: PortalSettingsRequest,
    request: Request,
    user: Annotated[User, Depends(require_active_subscription)],
    store: Annotated[PortalSettingsStore, Depends(get_portal_settings_store)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> PortalSettingsResponse:
    """Turn the practice's portal on or off, and choose what clients can do."""
    practice_id = _practice_id(user)
    choosable = _choosable(request)
    before = store.get(practice_id)
    if body.only_if_undecided and before.decided_at is not None:
        # Somebody at the practice already answered; theirs stands.
        return _response(before, choosable)
    after = before
    changes: dict[str, Any] = {}

    if body.modules is not None:
        current = _modules_on(before, choosable)
        chosen = _chosen_modules(body.modules, current, choosable)
        after = store.set_modules(practice_id, modules=chosen, by=user.id)
        was_on = [name for name in choosable if current[name]]
        if was_on != list(chosen):
            changes["modules"] = {"old": was_on, "new": list(chosen)}

    if body.enabled is not None:
        if body.enabled:
            # Before the switch flips, so a practice never reads as offering a
            # portal it has no address for.
            ensure_practice_slug(practice_id)
        after = store.set_enabled(practice_id, enabled=body.enabled, by=user.id)
        if before.enabled != after.enabled:
            changes["enabled"] = {"old": before.enabled, "new": after.enabled}

    if changes:
        audit.log(
            AuditAction.PRACTICE_PORTAL_OFFERING_CHANGED,
            user,
            request,
            resource_type=ResourceType.PRACTICE_PORTAL,
            resource_id=practice_id,
            changes=changes,
        )
    return _response(after, choosable)
