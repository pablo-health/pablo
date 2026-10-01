# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""One-click DNS setup on Settings > Domains, through Domain Connect.

* ``GET /api/practice/domains/connect`` — any clinician of the practice: per
  domain, the templates that could set it up at its DNS provider, and for the
  practice owner a freshly signed link to each one that can.
* ``POST /api/practice/domains/connect/return`` — the practice owner, back
  from the provider: checks the returned ``state`` was issued here for this
  practice, then runs the ordinary DNS check and answers with what it found.

The return is never taken as proof that anything changed; the check is.
Issuing links and coming back are audited with the domain and nothing else.
See :mod:`app.services.domain_connect`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..api_errors import ForbiddenError, UnprocessableEntityError
from ..auth.service import require_active_subscription
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..models.domain_connect import (
    DomainConnectResponse,
    DomainConnectReturnRequest,
    DomainConnectReturnResponse,
)
from ..services.audit_service import AuditService, get_audit_service
from ..services.domain_connect import DomainConnectService, get_domain_connect_service
from ..services.domain_connect_state import DomainConnectStateError
from ..services.practice_domain_dns import DnsLookup, get_dns_lookup
from ..services.practice_domain_service import (
    PracticeDomainService,
    get_practice_domain_service,
)
from .practice_domains import _manageable_practice_id, _practice_id

router = APIRouter(prefix="/api/practice/domains/connect", tags=["practice"])


@router.get("", response_model=DomainConnectResponse)
def list_domain_connect_offers(
    http_request: Request,
    user: User = Depends(require_active_subscription),
    service: DomainConnectService = Depends(get_domain_connect_service),
    audit: AuditService = Depends(get_audit_service),
) -> DomainConnectResponse:
    """Per domain, whether one-click setup is offered, and its link."""
    practice_id = _practice_id(user)
    try:
        can_manage = _manageable_practice_id(user) == practice_id
    except ForbiddenError:
        can_manage = False
    domains = service.offers(practice_id, can_manage=can_manage)
    issued = sorted({d.apex for d in domains if any(o.url for o in d.offers)})
    if issued:
        audit.log(
            AuditAction.PRACTICE_DOMAIN_CONNECT_LINK_ISSUED,
            user,
            http_request,
            resource_type=ResourceType.PRACTICE,
            resource_id=practice_id,
            changes={"domains": issued},
        )
    return DomainConnectResponse(domains=domains)


@router.post("/return", response_model=DomainConnectReturnResponse)
def return_from_domain_connect(
    body: DomainConnectReturnRequest,
    http_request: Request,
    user: User = Depends(require_active_subscription),
    service: DomainConnectService = Depends(get_domain_connect_service),
    domains: PracticeDomainService = Depends(get_practice_domain_service),
    lookup: DnsLookup = Depends(get_dns_lookup),
    audit: AuditService = Depends(get_audit_service),
) -> DomainConnectReturnResponse:
    """Accept a return from the DNS provider and check the practice's records.

    422 when the ``state`` was not issued here for this practice, or has
    expired.
    """
    practice_id = _manageable_practice_id(user)
    try:
        state = service.verify_return(practice_id, body.state)
    except DomainConnectStateError as e:
        raise UnprocessableEntityError(
            "That link has expired. Start again from this page.", code="DOMAIN_CONNECT_STATE"
        ) from e
    audit.log(
        AuditAction.PRACTICE_DOMAIN_CONNECT_RETURNED,
        user,
        http_request,
        resource_type=ResourceType.PRACTICE,
        resource_id=practice_id,
        changes={"domain": state.apex},
    )
    checked, confirmed = domains.check(practice_id, lookup)
    if confirmed:
        audit.log(
            AuditAction.PRACTICE_DOMAIN_OWNERSHIP_CONFIRMED,
            user,
            http_request,
            resource_type=ResourceType.PRACTICE,
            resource_id=practice_id,
            changes={"domains": confirmed},
        )
    return DomainConnectReturnResponse(apex=state.apex, error=body.error, domains=checked)
