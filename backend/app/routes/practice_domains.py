# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The hosts a practice serves its client portal and website from.

* ``GET /api/practice/domains`` — any clinician of the practice: the hosts,
  their status, which is primary, the DNS records to set, and whether a host
  is taking too long now that its records are in place. Where the deployment
  names a hosted domain, also the practice's hosted addresses
  (:mod:`app.portal.hosted`), giving the practice its portal address first if
  it has none yet; every answer below carries them too.
* ``GET /api/practice/domains/describe?domain=`` — any clinician of the
  practice: what a name would be stored as, its registrable domain, and
  whether it is bare (which decides a website's ``www.`` default).
* ``POST /api/practice/domains`` — add a host (and, for a website, its
  ``www.`` alias). A portal host also makes sure the practice has a portal
  address for it to serve.
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
where clients' links lead, so who changed it belongs on the record. So is the
first time a host is seen taking too long (``practice_domain_stuck``), whether
a check or a plain read sees it first: the practice is shown it as soon as it
is true, and whoever runs the deployment should hear of it then too.

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
from ..db import create_standalone_session
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..models.practice_domain import (
    AddPracticeDomainRequest,
    DomainNameResponse,
    HostedAddressesResponse,
    PracticeDomainListResponse,
)
from ..portal.hosted import hosted_domain, hosted_portal_host, hosted_site_host
from ..portal.practice_routes import ensure_practice_slug
from ..services.audit_service import AuditService, get_audit_service
from ..services.practice_domain_dns import DnsLookup, get_dns_lookup
from ..services.practice_domain_service import (
    PracticeDomainService,
    get_practice_domain_service,
)
from ..services.practice_domain_trigger import request_reconcile
from ..sites.store import PracticeSiteStore
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


def _hosted(practice_id: str) -> HostedAddressesResponse | None:
    """The practice's hosted addresses, where the deployment has a hosted domain.

    Both come from the practice's portal address, which is minted here when it
    has none, so every practice has them from its first look at this page.
    ``None`` too for an address that cannot be a hostname label
    (:mod:`app.portal.slugs`).
    """
    if hosted_domain() is None:
        return None
    address = ensure_practice_slug(practice_id)
    portal_host = hosted_portal_host(address.slug)
    site_host = hosted_site_host(address.slug)
    if portal_host is None or site_host is None:
        return None
    session = create_standalone_session()
    try:
        site = PracticeSiteStore(session).get(practice_id)
    finally:
        session.close()
    return HostedAddressesResponse(
        portal_host=portal_host,
        portal_on=address.enabled,
        site_host=site_host,
        site_live=site is not None and site.live_version is not None,
    )


def _list(service: PracticeDomainService, practice_id: str) -> PracticeDomainListResponse:
    return PracticeDomainListResponse(
        domains=service.responses(practice_id), hosted=_hosted(practice_id)
    )


@router.get("", response_model=PracticeDomainListResponse)
def list_practice_domains(
    http_request: Request,
    user: User = Depends(require_active_subscription),
    service: PracticeDomainService = Depends(get_practice_domain_service),
    audit: AuditService = Depends(get_audit_service),
) -> PracticeDomainListResponse:
    """Every host the caller's practice serves from."""
    practice_id = _practice_id(user)
    for host in service.report_stuck(practice_id):
        audit.log(
            AuditAction.PRACTICE_DOMAIN_STUCK,
            user,
            http_request,
            resource_type=ResourceType.PRACTICE,
            resource_id=practice_id,
            changes={"domain": host},
        )
    return _list(service, practice_id)


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
    if any(domain.purpose == "portal" for domain in added):
        # A portal host serves the practice's portal address, which is
        # otherwise minted only when the portal is switched on or a client is
        # first invited. Without one, a host that goes active would answer
        # every visitor with the not-found page an unknown host gets.
        ensure_practice_slug(practice_id)
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

    Records when a domain's ownership record is found, and when a host's
    records are all in place. Changes no host's status.
    """
    practice_id = _manageable_practice_id(user)
    check = service.check(practice_id, lookup)
    if check.confirmed:
        audit.log(
            AuditAction.PRACTICE_DOMAIN_OWNERSHIP_CONFIRMED,
            user,
            http_request,
            resource_type=ResourceType.PRACTICE,
            resource_id=practice_id,
            changes={"domains": check.confirmed},
        )
    for host in check.stuck:
        audit.log(
            AuditAction.PRACTICE_DOMAIN_STUCK,
            user,
            http_request,
            resource_type=ResourceType.PRACTICE,
            resource_id=practice_id,
            changes={"domain": host},
        )
    # Only when a host is still waiting: a check over hosts that all work
    # gives the job nothing to do.
    if service.serving_work_left(practice_id):
        background.add_task(request_reconcile)
    return PracticeDomainListResponse(domains=check.domains, hosted=_hosted(practice_id))


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
    """Remove a host. Removing the primary leaves that purpose with none until
    the reconciler makes another active host of it primary."""
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
