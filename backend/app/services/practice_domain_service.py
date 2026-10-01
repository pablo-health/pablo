# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's own domains: adding them, choosing the primary, removing them.

Every method takes the practice the caller acts for, resolved from the caller
and never from the request, and refuses a host that is not that practice's.

What this does NOT decide is whether a host works. A row is added ``pending``
and only whatever serves the host moves it on; the engine records the choice,
it does not vouch for the DNS. That is also why only an ``active`` host can be
made primary: links are built on the primary, and a link to a host nobody has
confirmed is a dead link.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends

from ..api_errors import ConflictError, NotFoundError, UnprocessableEntityError
from ..models.practice_domain import DnsRecord, PracticeDomain
from ..repositories.practice_domain import (
    DomainTakenError,
    PracticeDomainRepository,
    get_practice_domain_repository,
)
from ..settings import get_settings
from ..utcnow import utc_now
from .practice_domain_hosts import HostnameError, deployment_hosts, normalize_host

if TYPE_CHECKING:
    from ..models.practice_domain import DomainPurpose

#: ``example.com`` is two labels. A name like that is almost always an apex,
#: which is where people expect ``www.`` to work too.
_APEX_LABELS = 2

DOMAIN_TAKEN_MESSAGE = "That domain is already in use."


class PracticeDomainService:
    def __init__(
        self,
        repo: PracticeDomainRepository,
        *,
        reserved_hosts: frozenset[str] = frozenset(),
        cname_target: str = "",
    ) -> None:
        self._repo = repo
        self._reserved = reserved_hosts
        self._cname_target = cname_target.strip().lower().rstrip(".")

    def for_practice(self, practice_id: str) -> list[PracticeDomain]:
        return self._repo.list_for_practice(practice_id)

    def dns_records(self, domain: PracticeDomain) -> list[DnsRecord]:
        """What the practice adds at its DNS provider for this host."""
        if not self._cname_target:
            return []
        return [DnsRecord(type="CNAME", name=domain.domain, value=self._cname_target)]

    def add(
        self,
        practice_id: str,
        raw_domain: str,
        purpose: DomainPurpose,
        *,
        include_www: bool | None = None,
    ) -> list[PracticeDomain]:
        """Add a host, and for a website its ``www.`` alias. Returns what was added.

        Refuses the whole request, writing nothing, when the host — or the
        alias it would add — is already in use by another practice.
        """
        host = self._normalize(raw_domain)
        hosts = [host]
        www = self._www_alias(host, purpose, include_www)
        if www is not None:
            hosts.append(www)

        to_add: list[str] = []
        for candidate in hosts:
            existing = self._repo.get(candidate)
            if existing is None:
                to_add.append(candidate)
            elif existing.practice_id != practice_id:
                raise ConflictError(
                    DOMAIN_TAKEN_MESSAGE, {"domain": candidate}, code="DOMAIN_TAKEN"
                )
            elif candidate == host:
                raise ConflictError(
                    "You've already added that domain.",
                    {"domain": candidate},
                    code="DOMAIN_ALREADY_ADDED",
                )
            # An alias the practice already holds is left as it is.

        now = utc_now()
        added: list[PracticeDomain] = []
        for candidate in to_add:
            try:
                added.append(
                    self._repo.add(
                        PracticeDomain(
                            domain=candidate,
                            practice_id=practice_id,
                            purpose=purpose,
                            kind="vanity",
                            status="pending",
                            is_primary=False,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                )
            except DomainTakenError as e:
                raise ConflictError(
                    DOMAIN_TAKEN_MESSAGE, {"domain": candidate}, code="DOMAIN_TAKEN"
                ) from e
        return added

    def make_primary(self, practice_id: str, raw_domain: str) -> PracticeDomain:
        """Make an active host the practice's primary for its purpose."""
        domain = self._own(practice_id, raw_domain)
        if domain.status != "active":
            raise ConflictError(
                "A domain can be made primary once it's active.",
                {"domain": domain.domain, "status": domain.status},
                code="DOMAIN_NOT_ACTIVE",
            )
        if not domain.is_primary:
            self._repo.set_primary(domain.domain, practice_id, domain.purpose)
            domain.is_primary = True
        return domain

    def remove(self, practice_id: str, raw_domain: str) -> PracticeDomain:
        """Remove a host. Removing the primary leaves that purpose with none."""
        domain = self._own(practice_id, raw_domain)
        self._repo.remove(domain.domain, practice_id)
        return domain

    def _normalize(self, raw_domain: str) -> str:
        try:
            return normalize_host(raw_domain, reserved=self._reserved)
        except HostnameError as e:
            raise UnprocessableEntityError(str(e), code="DOMAIN_INVALID") from e

    def _www_alias(self, host: str, purpose: DomainPurpose, include_www: bool | None) -> str | None:
        if purpose != "site" or host.startswith("www."):
            return None
        wanted = include_www if include_www is not None else host.count(".") == _APEX_LABELS - 1
        return self._normalize(f"www.{host}") if wanted else None

    def _own(self, practice_id: str, raw_domain: str) -> PracticeDomain:
        host = raw_domain.strip().lower().removesuffix(".")
        domain = self._repo.get(host)
        if domain is None or domain.practice_id != practice_id:
            raise NotFoundError(
                "That domain isn't set up for this practice.", code="DOMAIN_NOT_FOUND"
            )
        return domain


def get_practice_domain_service(
    repo: PracticeDomainRepository = Depends(get_practice_domain_repository),
) -> PracticeDomainService:
    settings = get_settings()
    reserved = deployment_hosts(
        (
            settings.app_url,
            settings.backend_base_url,
            settings.portal_web_base_url,
            settings.companion_launch_url,
            settings.practice_domain_cname_target,
        )
    )
    return PracticeDomainService(
        repo, reserved_hosts=reserved, cname_target=settings.practice_domain_cname_target
    )
