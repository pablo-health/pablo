# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading and writing the practice's billing identity.

Practice-level configuration — same shape and same audit posture as
``/api/scheduling/policy``: no patient data crosses this route, so it is
not a PHI-access surface, and there is nothing here for ``AuditService``
to attribute a chart read or write to.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth.service import TenantContext, get_tenant_context, require_active_subscription
from ..db import get_db_session
from ..models.practice_billing import BillingProfileResponse, UpdateBillingProfileRequest
from ..services.practice_billing_profile import load_billing_profile, update_billing_profile

router = APIRouter(
    prefix="/api/practice/billing-profile",
    tags=["practice"],
    dependencies=[Depends(require_active_subscription)],
)


@router.get("", response_model=BillingProfileResponse)
def get_billing_profile(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> BillingProfileResponse:
    """The practice's billing identity.

    A practice that has never saved one gets an all-``None`` response
    rather than a 404 — reading never creates a row.
    """
    return BillingProfileResponse(**load_billing_profile(get_db_session()))  # type: ignore[arg-type]


@router.patch("", response_model=BillingProfileResponse)
def update_billing_profile_route(
    request: UpdateBillingProfileRequest,
    _ctx: TenantContext = Depends(get_tenant_context),
) -> BillingProfileResponse:
    """Change part of the billing profile.

    Partial: a field the caller did not send keeps its current value. The
    raw ``tax_id`` (if sent) is encrypted before it touches the database
    and is never echoed back — the response only ever carries
    ``tax_id_last4``.
    """
    session = get_db_session()
    merged = update_billing_profile(session, request.model_dump(exclude_unset=True))
    session.commit()
    return BillingProfileResponse(**merged)  # type: ignore[arg-type]
