# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""EHR navigation route endpoints.

Serves cached navigation routes to the Pablo Companion app and provides
a goal-based LLM navigation endpoint that guides step-by-step navigation
to the SOAP note entry form.

HIPAA: No PHI in any request or response — navigation metadata only.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth.service import (
    TenantContext,
    get_current_user,
    get_tenant_context,
    require_active_subscription,
)
from ..models import User
from ..models.ehr_route import (
    EhrRouteResponse,
    GoalNavigationRequest,
    GoalNavigationResponse,
    UpdateEhrRouteStepRequest,
)
from ..models.enums import EhrSystem
from ..rate_limit import get_ehr_navigate_limiter
from ..repositories import (
    get_ehr_prompt_repository as _ehr_prompt_repo_factory,
)
from ..repositories import (
    get_ehr_route_repository as _ehr_route_repo_factory,
)
from ..repositories.ehr_prompt import EhrPromptRepository
from ..repositories.ehr_route import EhrRouteRepository
from ..services.ehr_navigation_service import (
    EhrNavigationService,
    GeminiEhrNavigationService,
)
from ..settings import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


def get_ehr_route_repository(
    ctx: TenantContext = Depends(get_tenant_context),
) -> EhrRouteRepository:
    """Get EHR route repository scoped to the tenant's database."""
    return _ehr_route_repo_factory(firestore_db=ctx.firestore_db)


def get_ehr_prompt_repository() -> EhrPromptRepository:
    """Get EHR prompt repository from the default (shared) database.

    EHR prompts are system configuration (URL patterns, navigation instructions),
    not tenant data. Shared across all tenants with no PHI.
    """
    return _ehr_prompt_repo_factory()


def get_ehr_navigation_service(
    prompt_repo: EhrPromptRepository = Depends(get_ehr_prompt_repository),
) -> EhrNavigationService:
    """Get EHR navigation LLM service."""
    settings = get_settings()
    return GeminiEhrNavigationService(
        model=settings.ehr_navigate_model,
        prompt_repo=prompt_repo,
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

route_router = APIRouter(
    prefix="/api/ehr-routes",
    tags=["ehr-navigation"],
    dependencies=[Depends(require_active_subscription)],
)
navigate_router = APIRouter(
    tags=["ehr-navigation"],
    dependencies=[Depends(require_active_subscription)],
)


# ---------------------------------------------------------------------------
# GET /api/ehr-routes/{ehr_system}
# ---------------------------------------------------------------------------


@route_router.get("/{ehr_system}")
def get_ehr_route(
    ehr_system: EhrSystem,
    _user: User = Depends(get_current_user),
    repo: EhrRouteRepository = Depends(get_ehr_route_repository),
) -> EhrRouteResponse:
    """Get cached navigation route for an EHR system."""
    route = repo.get(ehr_system.value)
    if not route:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No route found for EHR system '{ehr_system}'",
        )
    return EhrRouteResponse.from_ehr_route(route)


# ---------------------------------------------------------------------------
# PATCH /api/ehr-routes/{ehr_system}/steps/{step_index}
# ---------------------------------------------------------------------------


@route_router.patch("/{ehr_system}/steps/{step_index}")
def update_ehr_route_step(
    ehr_system: EhrSystem,
    step_index: int,
    request: UpdateEhrRouteStepRequest,
    _user: User = Depends(get_current_user),
    repo: EhrRouteRepository = Depends(get_ehr_route_repository),
) -> EhrRouteResponse:
    """Update a step in an EHR route (route learning).

    Called by the companion app after LLM-assisted recovery finds a new
    working selector, so future therapists benefit.
    """
    try:
        updated = repo.update_step(
            ehr_system=ehr_system.value,
            step_index=step_index,
            selector=request.selector,
            a11y_fingerprint=request.a11y_fingerprint,
        )
    except IndexError as err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(err),
        ) from err

    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No route found for EHR system '{ehr_system}'",
        )
    return EhrRouteResponse.from_ehr_route(updated)


# ---------------------------------------------------------------------------
# POST /api/ehr-navigate
# ---------------------------------------------------------------------------


@navigate_router.post("/api/ehr-navigate")
async def ehr_navigate(
    request: GoalNavigationRequest,
    user: User = Depends(get_current_user),
    service: EhrNavigationService = Depends(get_ehr_navigation_service),
) -> GoalNavigationResponse:
    """Goal-based LLM navigation for EHR systems.

    The companion app sends the current page DOM + a navigation goal.
    The LLM returns the next action to take. The companion loops until
    is_on_target_page is true. Rate limited to 50 calls/user/day.
    """
    get_ehr_navigate_limiter().check(user.id)

    try:
        return await service.navigate(request)
    except LookupError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err
    except (ValueError, RuntimeError) as err:
        logger.exception("EHR navigate failed for user %s", user.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Navigation failed — please try again",
        ) from err
