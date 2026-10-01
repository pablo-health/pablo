# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's own domains: adding them, choosing the primary, removing them,
and checking the DNS records they need.

Every method takes the practice the caller acts for, resolved from the caller
and never from the request, and refuses a host that is not that practice's.

Hosts sit under a registrable domain (``example.co.uk`` for
``portal.example.co.uk``, found from the Public Suffix List), and a domain
belongs to one practice: a host under a domain another practice holds is
refused the way a taken host is. The domain's row is written with its first
host and removed with its last. It carries the token for the ownership record
(``_pablo-verify.<domain>`` TXT ``pablo-verify=<token>``) and the domain's
email sending identity.

What this does NOT decide is whether a host works. A row is added ``pending``
and only whatever serves the host moves it on; the engine records the choice,
it does not vouch for the DNS. A DNS check reports what it found per record and
records one thing — that the ownership record was found — and leaves the host's
status alone. That is also why only an ``active`` host can be made primary:
links are built on the primary, and a link to a host nobody has confirmed is a
dead link.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from fastapi import Depends

from ..api_errors import ConflictError, NotFoundError, UnprocessableEntityError
from ..models.practice_domain import (
    DnsRecord,
    PracticeDomain,
    PracticeDomainApex,
    PracticeDomainResponse,
)
from ..repositories.practice_domain import (
    DomainTakenError,
    PracticeDomainRepository,
    get_practice_domain_repository,
)
from ..settings import get_settings
from ..utcnow import utc_now
from .practice_domain_dns import DnsChecker, DnsLookup
from .practice_domain_hosts import HostnameError, apex_of, deployment_hosts, normalize_host

if TYPE_CHECKING:
    from ..models.practice_domain import DomainPurpose

DOMAIN_TAKEN_MESSAGE = "That domain is already in use."

#: The ownership record: ``_pablo-verify.<domain>`` TXT ``pablo-verify=<token>``.
VERIFY_LABEL = "_pablo-verify"
VERIFY_VALUE_PREFIX = "pablo-verify="
#: A certificate's DNS authorisation: ``_acme-challenge.<host>`` CNAME
#: ``<value>.authorize.certificatemanager.goog``.
CERT_AUTH_LABEL = "_acme-challenge"
CERT_AUTH_SUFFIX = "authorize.certificatemanager.goog"


def new_verify_token() -> str:
    return secrets.token_urlsafe(24)


class PracticeDomainService:
    def __init__(
        self,
        repo: PracticeDomainRepository,
        *,
        reserved_hosts: frozenset[str] = frozenset(),
        cname_target: str = "",
        apex_ips: tuple[str, ...] = (),
        dkim_cname_suffix: str = "dkim.amazonses.com",
    ) -> None:
        self._repo = repo
        self._reserved = reserved_hosts
        self._cname_target = cname_target.strip().lower().rstrip(".")
        self._apex_ips = apex_ips
        self._dkim_suffix = dkim_cname_suffix.strip().lower().strip(".")

    def for_practice(self, practice_id: str) -> list[PracticeDomain]:
        return self._repo.list_for_practice(practice_id)

    def _is_bare(self, domain: PracticeDomain) -> bool:
        return _apex_or_none(domain.domain) == domain.domain

    def dns_records(self, domain: PracticeDomain) -> list[DnsRecord]:
        """The record that points this host here.

        A bare domain (``example.org``) gets A/AAAA records when the deployment
        names its addresses: many DNS providers refuse a CNAME there, and an
        address record works at every one of them.
        """
        if self._is_bare(domain) and self._apex_ips:
            return [
                DnsRecord(type="AAAA" if ":" in ip else "A", name=domain.domain, value=ip)
                for ip in self._apex_ips
            ]
        if not self._cname_target:
            return []
        return [DnsRecord(type="CNAME", name=domain.domain, value=self._cname_target)]

    def alias_alternative(self, domain: PracticeDomain) -> str | None:
        """For a bare domain shown address records: the name an ALIAS/ANAME
        record could point at instead, where the DNS provider offers one."""
        if self._is_bare(domain) and self._apex_ips and self._cname_target:
            return self._cname_target
        return None

    def cert_auth_records(self, domain: PracticeDomain) -> list[DnsRecord]:
        """The host's certificate authorisation CNAME, once one was requested."""
        if not domain.cert_auth_value:
            return []
        return [
            DnsRecord(
                type="CNAME",
                name=f"{CERT_AUTH_LABEL}.{domain.domain}",
                value=f"{domain.cert_auth_value}.{CERT_AUTH_SUFFIX}",
            )
        ]

    def apex_records(self, apex: PracticeDomainApex) -> list[DnsRecord]:
        """The domain's ownership TXT, and its DKIM CNAMEs once it has them."""
        records = [
            DnsRecord(
                type="TXT",
                name=f"{VERIFY_LABEL}.{apex.apex}",
                value=f"{VERIFY_VALUE_PREFIX}{apex.verify_token}",
            )
        ]
        records.extend(
            DnsRecord(
                type="CNAME",
                name=f"{token}._domainkey.{apex.apex}",
                value=f"{token}.{self._dkim_suffix}",
            )
            for token in apex.email_dkim_tokens or []
        )
        return records

    def responses(self, practice_id: str) -> list[PracticeDomainResponse]:
        """Every host the practice holds, oldest first, with its records.

        A domain's own records (ownership, DKIM) are shown once, on one host
        under it: the bare domain itself when the practice holds it, otherwise
        the oldest host under it. Every host under the domain still reports
        the domain and when its ownership was last confirmed.
        """
        domains = self._repo.list_for_practice(practice_id)
        apexes = {a.apex: a for a in self._repo.list_apexes_for_practice(practice_id)}
        apex_by_host = {d.domain: _apex_or_none(d.domain) for d in domains}
        carriers: dict[str, str] = {}
        for d in domains:
            apex = apex_by_host[d.domain]
            if apex is not None and (apex not in carriers or d.domain == apex):
                carriers[apex] = d.domain

        responses: list[PracticeDomainResponse] = []
        for d in domains:
            apex_name = apex_by_host[d.domain]
            apex_row = apexes.get(apex_name) if apex_name else None
            records = self.dns_records(d) + self.cert_auth_records(d)
            if apex_row is not None and carriers.get(apex_row.apex) == d.domain:
                records += self.apex_records(apex_row)
            responses.append(
                PracticeDomainResponse(
                    domain=d.domain,
                    purpose=d.purpose,
                    status=d.status,
                    is_primary=d.is_primary,
                    verified_at=d.verified_at,
                    created_at=d.created_at,
                    dns_records=records,
                    alias_alternative=self.alias_alternative(d),
                    apex=apex_name,
                    apex_verified_at=apex_row.verified_at if apex_row else None,
                )
            )
        return responses

    def check(
        self, practice_id: str, lookup: DnsLookup
    ) -> tuple[list[PracticeDomainResponse], list[str]]:
        """Look every record up and say, per record, what was found.

        Records the time on a domain whose ownership TXT was found with its
        token, and returns those domains beside the hosts. Changes no host's
        status: a record being in place is not the host being served.
        """
        self._ensure_apexes(practice_id)
        checker = DnsChecker(lookup, cname_target=self._cname_target, apex_ips=self._apex_ips)
        responses = self.responses(practice_id)
        now = utc_now()
        verified: set[str] = set()
        for response in responses:
            response.dns_records = [
                checker.check(record, host=response.domain) for record in response.dns_records
            ]
            for record in response.dns_records:
                if (
                    record.type == "TXT"
                    and record.check == "ok"
                    and response.apex is not None
                    and record.name == f"{VERIFY_LABEL}.{response.apex}"
                ):
                    self._repo.mark_apex_verified(response.apex, practice_id, now)
                    verified.add(response.apex)
        for response in responses:
            if response.apex in verified:
                response.apex_verified_at = now
        return responses, sorted(verified)

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
        alias it would add, or the domain they sit under — is already in use
        by another practice.
        """
        host = self._normalize(raw_domain)
        apex = self._apex(host)
        hosts = [host]
        www = self._www_alias(host, apex, purpose, include_www)
        if www is not None:
            hosts.append(www)

        existing_apex = self._repo.get_apex(apex)
        if existing_apex is not None and existing_apex.practice_id != practice_id:
            raise ConflictError(DOMAIN_TAKEN_MESSAGE, {"domain": host}, code="DOMAIN_TAKEN")

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
        created_apex = False
        if existing_apex is None:
            try:
                self._repo.add_apex(_new_apex(apex, practice_id))
            except DomainTakenError as e:
                raise ConflictError(
                    DOMAIN_TAKEN_MESSAGE, {"domain": host}, code="DOMAIN_TAKEN"
                ) from e
            created_apex = True

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
                # The refusal is a response, not an exception, by the time the
                # request's transaction ends — so undo this call's own writes.
                for done in added:
                    self._repo.remove(done.domain, practice_id)
                if created_apex:
                    self._repo.remove_apex(apex, practice_id)
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
        """Remove a host. Removing the primary leaves that purpose with none.

        Removing the last host under a domain releases the domain too.
        """
        domain = self._own(practice_id, raw_domain)
        self._repo.remove(domain.domain, practice_id)
        apex = _apex_or_none(domain.domain)
        if apex is not None and not any(
            _apex_or_none(d.domain) == apex for d in self._repo.list_for_practice(practice_id)
        ):
            self._repo.remove_apex(apex, practice_id)
        return domain

    def _ensure_apexes(self, practice_id: str) -> None:
        """Give every host's domain its row, for hosts added before domains
        had one. A domain another practice already holds is left to them."""
        held = {a.apex for a in self._repo.list_apexes_for_practice(practice_id)}
        for d in self._repo.list_for_practice(practice_id):
            apex = _apex_or_none(d.domain)
            if apex is None or apex in held:
                continue
            held.add(apex)
            if self._repo.get_apex(apex) is not None:
                continue
            try:
                self._repo.add_apex(_new_apex(apex, practice_id))
            except DomainTakenError:
                continue

    def _normalize(self, raw_domain: str) -> str:
        try:
            return normalize_host(raw_domain, reserved=self._reserved)
        except HostnameError as e:
            raise UnprocessableEntityError(str(e), code="DOMAIN_INVALID") from e

    def _apex(self, host: str) -> str:
        try:
            return apex_of(host)
        except HostnameError as e:
            raise UnprocessableEntityError(str(e), code="DOMAIN_INVALID") from e

    def _www_alias(
        self, host: str, apex: str, purpose: DomainPurpose, include_www: bool | None
    ) -> str | None:
        if purpose != "site" or host.startswith("www."):
            return None
        wanted = include_www if include_www is not None else host == apex
        return self._normalize(f"www.{host}") if wanted else None

    def _own(self, practice_id: str, raw_domain: str) -> PracticeDomain:
        host = raw_domain.strip().lower().removesuffix(".")
        domain = self._repo.get(host)
        if domain is None or domain.practice_id != practice_id:
            raise NotFoundError(
                "That domain isn't set up for this practice.", code="DOMAIN_NOT_FOUND"
            )
        return domain


def _apex_or_none(host: str) -> str | None:
    try:
        return apex_of(host)
    except HostnameError:
        return None


def _new_apex(apex: str, practice_id: str) -> PracticeDomainApex:
    now = utc_now()
    return PracticeDomainApex(
        apex=apex,
        practice_id=practice_id,
        verify_token=new_verify_token(),
        created_at=now,
        updated_at=now,
    )


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
    apex_ips = tuple(
        ip.strip() for ip in settings.practice_domain_apex_ips.split(",") if ip.strip()
    )
    return PracticeDomainService(
        repo,
        reserved_hosts=reserved,
        cname_target=settings.practice_domain_cname_target,
        apex_ips=apex_ips,
        dkim_cname_suffix=settings.practice_domain_dkim_cname_suffix,
    )
