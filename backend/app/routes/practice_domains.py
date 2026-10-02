# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The hosts a practice serves its client portal and website from.

* ``GET /api/practice/domains`` — any clinician of the practice: the hosts,
  their status, which is primary, and the DNS records to set.
* ``GET /api/practice/domains/describe?domain=`` — any clinician of the
  practice: what a name would be stored as, its registrable domain, and
  whether it is bare (which decides a website's ``www.`` default).
* ``POST /api/practice/domains`` — add a host (and, for a website, its
  ``www.`` alias).
* ``POST /api/practice/domains/check`` — look the records up in DNS and answer
  the list with what was found per record; records a domain's ownership when
  its TXT is found. Changes no host's status.
* ``POST /api/practice/domains/{domain}/primary`` — make an active host the
  primary for its purpose.
* ``DELETE /api/practice/domains/{domain}`` — remove a host. Where the
  deployment serves hosts through the domain reconciler job, the host leaves
  the list at once and the job takes it down before its row goes.

The writes are the practice owner's (see :func:`_manageable_practice_id`).
The practice is always the caller's own, resolved from the caller, never taken
from the request. Writes are audited: not PHI, but the primary portal host is
where clients' links lead, so who changed it belongs on the record.

Nothing here marks a host as working. See
:mod:`app.services.practice_domain_service`. What does is the domain reconciler
job, and a change that leaves it work to do asks for a run
(:func:`app.services.practice_domain_trigger.request_reconcile`) once the
request's writes have committed: as a background task, which runs after the
response, by when the session middleware has committed.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request

from ..api_errors import NotFoundError
from ..auth.service import require_active_subscription
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..models.practice_domain import (
    AddPracticeDomainRequest,
    DomainNameResponse,
    PracticeDomainListResponse,
)
from ..services.audit_service import AuditService, get_audit_service
from ..services.practice_domain_dns import DnsLookup, get_dns_lookup
from ..services.practice_domain_service import (
    PracticeDomainService,
    get_practice_domain_service,
)
from ..services.practice_domain_trigger import request_reconcile
from .users import _get_own_practice_as_owner, _resolve_practice_id_for

router = APIRouter(prefix="/api/practice/domains", tags=["practice"])


def _practice_id(user: User) -> str:
    practice_id = _resolve_practice_id_for(user)
    if practice_id is None:
        raise NotFoundError("No practice mapping for this account", code="NO_PRACTICE")
    return practice_id


def _manageable_practice_id(user: User) -> str:
    """The caller's practice, if the caller may change its domains.

    Owner-only today. When a practice-admin role exists, widening who may
    manage domains is this function and nothing else.
    """
    # Owner-only until a practice admin role exists; Domains and Website both widen here.
    return _get_own_practice_as_owner(user).id


def _list(service: PracticeDomainService, practice_id: str) -> PracticeDomainListResponse:
    return PracticeDomainListResponse(domains=service.responses(practice_id))


@router.get("", response_model=PracticeDomainListResponse)
def list_practice_domains(
    user: User = Depends(require_active_subscription),
    service: PracticeDomainService = Depends(get_practice_domain_service),
) -> PracticeDomainListResponse:
    """Every host the caller's practice serves from."""
    return _list(service, _practice_id(user))


@router.get("/describe", response_model=DomainNameResponse)
def describe_practice_domain(
    domain: str = Query(max_length=512),
    _user: User = Depends(require_active_subscription),
    service: PracticeDomainService = Depends(get_practice_domain_service),
) -> DomainNameResponse:
    """What a name would be stored as, its registrable domain, and whether it
    is bare. Reads nothing stored; 422 with what to fix."""
    return service.describe(domain)


@router.post("", response_model=PracticeDomainListResponse, status_code=201)
def add_practice_domain(
    body: AddPracticeDomainRequest,
    http_request: Request,
    background: BackgroundTasks,
    user: User = Depends(require_active_subscription),
    service: PracticeDomainService = Depends(get_practice_domain_service),
    audit: AuditService = Depends(get_audit_service),
) -> PracticeDomainListResponse:
    """Add a host. 409 if it is already in use; 422 with what to fix; 403
    (``DOMAIN_LIMIT``) if it would bring the practice past its allowance of
    domains, with the deployment's words for it."""
    practice_id = _manageable_practice_id(user)
    added = service.add(practice_id, body.domain, body.purpose, include_www=body.include_www)
    for domain in added:
        audit.log(
            AuditAction.PRACTICE_DOMAIN_ADDED,
            user,
            http_request,
            resource_type=ResourceType.PRACTICE,
            resource_id=practice_id,
            changes={"domain": domain.domain, "purpose": domain.purpose},
        )
    if added:
        background.add_task(request_reconcile)
    return _list(service, practice_id)


@router.post("/check", response_model=PracticeDomainListResponse)
def check_practice_domains(
    http_request: Request,
    background: BackgroundTasks,
    user: User = Depends(require_active_subscription),
    service: PracticeDomainService = Depends(get_practice_domain_service),
    lookup: DnsLookup = Depends(get_dns_lookup),
    audit: AuditService = Depends(get_audit_service),
) -> PracticeDomainListResponse:
    """Look the practice's records up in DNS and say, per record, what was found.

    Records when a domain's ownership record is found. Changes no host's status.
    """
    practice_id = _manageable_practice_id(user)
    domains, confirmed = service.check(practice_id, lookup)
    if confirmed:
        audit.log(
            AuditAction.PRACTICE_DOMAIN_OWNERSHIP_CONFIRMED,
            user,
            http_request,
            resource_type=ResourceType.PRACTICE,
            resource_id=practice_id,
            changes={"domains": confirmed},
        )
    # Only when a host is still waiting: a check over hosts that all work
    # gives the job nothing to do.
    if service.serving_work_left(practice_id):
        background.add_task(request_reconcile)
    return PracticeDomainListResponse(domains=domains)


@router.post("/{domain}/primary", response_model=PracticeDomainListResponse)
def make_practice_domain_primary(
    domain: str,
    http_request: Request,
    user: User = Depends(require_active_subscription),
    service: PracticeDomainService = Depends(get_practice_domain_service),
    audit: AuditService = Depends(get_audit_service),
) -> PracticeDomainListResponse:
    """Make an active host the primary for its purpose. 409 if it is not active."""
    practice_id = _manageable_practice_id(user)
    before = service.for_practice(practice_id)
    chosen = service.make_primary(practice_id, domain)
    previous = next(
        (
            d.domain
            for d in before
            if d.is_primary and d.purpose == chosen.purpose and d.domain != chosen.domain
        ),
        None,
    )
    audit.log(
        AuditAction.PRACTICE_DOMAIN_MADE_PRIMARY,
        user,
        http_request,
        resource_type=ResourceType.PRACTICE,
        resource_id=practice_id,
        changes={"domain": chosen.domain, "purpose": chosen.purpose, "previous": previous},
    )
    return _list(service, practice_id)


@router.delete("/{domain}", response_model=PracticeDomainListResponse)
def remove_practice_domain(
    domain: str,
    http_request: Request,
    background: BackgroundTasks,
    user: User = Depends(require_active_subscription),
    service: PracticeDomainService = Depends(get_practice_domain_service),
    audit: AuditService = Depends(get_audit_service),
) -> PracticeDomainListResponse:
    """Remove a host. Removing the primary leaves that purpose with none."""
    practice_id = _manageable_practice_id(user)
    removed = service.remove(practice_id, domain)
    audit.log(
        AuditAction.PRACTICE_DOMAIN_REMOVED,
        user,
        http_request,
        resource_type=ResourceType.PRACTICE,
        resource_id=practice_id,
        changes={
            "domain": removed.domain,
            "purpose": removed.purpose,
            "was_primary": removed.is_primary,
        },
    )
    background.add_task(request_reconcile)
    return _list(service, practice_id)
