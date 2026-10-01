# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""One-click DNS setup for a practice's own domains, through Domain Connect.

Where a domain's DNS provider supports Domain Connect's synchronous flow, the
practice can be sent there with a signed link that fills in the records Pablo
would otherwise list for them to copy. The provider shows the change, the
practice approves it, and the provider sends them back to Settings > Domains.

The templates are published by the deployment's operator in the public
Domain Connect template registry; this module knows them only by the
variables they take, which are part of that published contract. Both are
applied to the registrable domain (``domain=<apex>``, no ``host``):

**Portal and email** (``practice_domain_connect_portal_service_id``)

=================  ==========================================================
``verify``         the domain's ownership token: TXT ``_pablo-verify``
                   ``pablo-verify=%verify%``
``dkim1``..``3``   the domain's three email signing tokens: CNAME
                   ``%dkimN%._domainkey`` -> ``%dkimN%.<DKIM suffix>``
``portaltarget``   CNAME ``portal`` -> ``%portaltarget%.<fixed suffix>``;
                   ``practice_domain_connect_portal_target``, which must be
                   the first label(s) of ``practice_domain_cname_target``
``certauth``       the portal host's certificate authorisation: CNAME
                   ``_acme-challenge.portal`` -> ``%certauth%.<cert suffix>``
=================  ==========================================================

**Website** (``practice_domain_connect_website_service_id``)

=================  ==========================================================
``siteip``         A ``@``: the one address in ``practice_domain_apex_ips``
                   (``www`` is a CNAME to ``@``)
``certauth``       ``_acme-challenge`` for the bare domain
``certauthwww``    ``_acme-challenge.www``
=================  ==========================================================

A template is offered only when it would publish exactly the records the
page would otherwise list: every variable known, and the practice's hosts
set up the way the template sets them up — a portal at ``portal.<domain>``;
a website at both ``<domain>`` and ``www.<domain>``. Anything else keeps the
records to copy by hand, and nothing new is shown.

Nothing here decides that a record is in place. Coming back from the
provider leads to the ordinary DNS check, and the check decides.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Depends

from ..models.domain_connect import (
    ConnectReason,
    DomainConnectApex,
    DomainConnectOffer,
)
from ..repositories.practice_domain import (
    PracticeDomainRepository,
    get_practice_domain_repository,
)
from ..settings import get_settings
from .domain_connect_discovery import DomainConnectDiscovery, HttpFetch, HttpxFetch
from .domain_connect_signing import KmsSigner, Signer, apply_url
from .domain_connect_state import (
    ConnectState,
    DomainConnectStateError,
    mint_state,
    verify_state,
)
from .practice_domain_dns import DnsLookup, get_dns_lookup
from .practice_domain_hosts import HostnameError, apex_of
from .token_encryption import TokenEncryptionError, derive_subkey

if TYPE_CHECKING:
    from ..models.practice_domain import DomainPurpose, PracticeDomain, PracticeDomainApex

#: The return lands on Settings > Domains. The deployment's app origin must be
#: within each template's ``syncRedirectDomain``, or providers will not send
#: the practice back.
RETURN_PATH = "/dashboard/settings/domains"
_STATE_PURPOSE = "domain-connect-state"
_DKIM_TOKENS = 3


@dataclass(frozen=True)
class DomainConnectConfig:
    provider_id: str = ""
    portal_service_id: str = ""
    website_service_id: str = ""
    key_host: str = ""
    portal_target: str = ""
    cname_target: str = ""
    apex_ips: tuple[str, ...] = ()
    redirect_uri: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.provider_id and self.key_host and self.redirect_uri)

    @property
    def offers_portal(self) -> bool:
        """The portal template's CNAME lands where the page's own record
        does — otherwise the check would call the applied record wrong."""
        return bool(
            self.portal_service_id
            and self.portal_target
            and self.cname_target.startswith(f"{self.portal_target}.")
        )

    @property
    def site_ip(self) -> str | None:
        """The website template has one A record: offered only when the
        deployment names exactly one IPv4 address."""
        if len(self.apex_ips) != 1:
            return None
        try:
            address = ipaddress.ip_address(self.apex_ips[0])
        except ValueError:
            return None
        return str(address) if address.version == 4 else None  # noqa: PLR2004

    @property
    def offers_website(self) -> bool:
        return bool(self.website_service_id and self.site_ip)


def _apex_or_none(host: str) -> str | None:
    try:
        return apex_of(host)
    except HostnameError:
        return None


@dataclass(frozen=True)
class _Template:
    service_id: str
    purpose: DomainPurpose


@dataclass(frozen=True)
class _Signing:
    signer: Signer
    state_key: bytes


class DomainConnectService:
    def __init__(
        self,
        repo: PracticeDomainRepository,
        discovery: DomainConnectDiscovery,
        signer: Signer | None,
        state_key: bytes | None,
        config: DomainConnectConfig,
    ) -> None:
        self._repo = repo
        self._discovery = discovery
        self._signer = signer
        self._state_key = state_key
        self._config = config

    def offers(self, practice_id: str, *, can_manage: bool) -> list[DomainConnectApex]:
        """Per domain the practice holds hosts under, the templates that
        could set it up, and a signed link for each one that can."""
        if not self._config.enabled or self._signer is None or self._state_key is None:
            return []
        signing = _Signing(self._signer, self._state_key)
        hosts_by_apex: dict[str, dict[str, PracticeDomain]] = {}
        for domain in self._repo.list_for_practice(practice_id):
            apex = _apex_or_none(domain.domain)
            if apex is not None:
                hosts_by_apex.setdefault(apex, {})[domain.domain] = domain
        apex_rows = {a.apex: a for a in self._repo.list_apexes_for_practice(practice_id)}

        result: list[DomainConnectApex] = []
        for apex, hosts in hosts_by_apex.items():
            offers = [
                self._offer(practice_id, apex, template, params, signing, can_manage=can_manage)
                for template, params in self._candidates(apex, hosts, apex_rows.get(apex))
            ]
            if offers:
                result.append(DomainConnectApex(apex=apex, offers=offers))
        return result

    def verify_return(self, practice_id: str, state: str) -> ConnectState:
        """What a returned ``state`` was issued for. Raises
        ``DomainConnectStateError`` unless this deployment issued it for this
        practice within the last 15 minutes."""
        if self._state_key is None:
            raise DomainConnectStateError("one-click setup is not configured")
        return verify_state(self._state_key, state, practice_id)

    def _candidates(
        self, apex: str, hosts: dict[str, PracticeDomain], apex_row: PracticeDomainApex | None
    ) -> list[tuple[_Template, dict[str, str] | ConnectReason]]:
        purposes = {d.purpose for d in hosts.values()}
        candidates: list[tuple[_Template, dict[str, str] | ConnectReason]] = []
        if "portal" in purposes and self._config.offers_portal:
            template = _Template(self._config.portal_service_id, "portal")
            candidates.append((template, self._portal_params(apex, hosts, apex_row)))
        if "site" in purposes and self._config.offers_website:
            template = _Template(self._config.website_service_id, "site")
            candidates.append((template, self._website_params(apex, hosts)))
        return candidates

    def _portal_params(
        self, apex: str, hosts: dict[str, PracticeDomain], apex_row: PracticeDomainApex | None
    ) -> dict[str, str] | ConnectReason:
        portal = hosts.get(f"portal.{apex}")
        if portal is None or portal.purpose != "portal":
            return "hosts_differ"
        dkim = (apex_row.email_dkim_tokens or []) if apex_row else []
        if apex_row is None or len(dkim) != _DKIM_TOKENS or not portal.cert_auth_value:
            return "records_pending"
        return {
            "verify": apex_row.verify_token,
            "dkim1": dkim[0],
            "dkim2": dkim[1],
            "dkim3": dkim[2],
            "portaltarget": self._config.portal_target,
            "certauth": portal.cert_auth_value,
        }

    def _website_params(
        self, apex: str, hosts: dict[str, PracticeDomain]
    ) -> dict[str, str] | ConnectReason:
        bare, www = hosts.get(apex), hosts.get(f"www.{apex}")
        if bare is None or www is None or bare.purpose != "site" or www.purpose != "site":
            return "hosts_differ"
        site_ip = self._config.site_ip
        if not bare.cert_auth_value or not www.cert_auth_value or site_ip is None:
            return "records_pending"
        return {
            "siteip": site_ip,
            "certauth": bare.cert_auth_value,
            "certauthwww": www.cert_auth_value,
        }

    def _offer(
        self,
        practice_id: str,
        apex: str,
        template: _Template,
        params: dict[str, str] | ConnectReason,
        signing: _Signing,
        *,
        can_manage: bool,
    ) -> DomainConnectOffer:
        offer = DomainConnectOffer(service_id=template.service_id, purpose=template.purpose)
        if isinstance(params, str):
            offer.reason = params
            return offer
        if not can_manage:
            offer.reason = "not_allowed"
            return offer
        provider = self._discovery.provider_for(apex)
        if provider is None:
            offer.reason = "provider_unsupported"
            return offer
        offer.provider_name = provider.display_name
        offer.supported = self._discovery.supports(
            provider, self._config.provider_id, template.service_id
        )
        if offer.supported is None:
            offer.reason = "lookup_failed"
            return offer
        if not offer.supported:
            offer.reason = "template_unsupported"
            return offer
        state = mint_state(signing.state_key, ConnectState(practice_id, apex, template.service_id))
        offer.url = apply_url(
            provider.url_sync_ux,
            self._config.provider_id,
            template.service_id,
            {
                "domain": apex,
                **params,
                "redirect_uri": self._config.redirect_uri,
                "state": state,
            },
            signing.signer,
            self._config.key_host,
        )
        return offer


def get_http_fetch() -> HttpFetch:
    return HttpxFetch()


def _state_key() -> bytes | None:
    try:
        return derive_subkey(_STATE_PURPOSE)
    except TokenEncryptionError:
        return None


def get_domain_connect_service(
    repo: PracticeDomainRepository = Depends(get_practice_domain_repository),
    lookup: DnsLookup = Depends(get_dns_lookup),
    fetch: HttpFetch = Depends(get_http_fetch),
) -> DomainConnectService:
    settings = get_settings()
    config = DomainConnectConfig(
        provider_id=settings.practice_domain_connect_provider_id.strip(),
        portal_service_id=settings.practice_domain_connect_portal_service_id.strip(),
        website_service_id=settings.practice_domain_connect_website_service_id.strip(),
        key_host=settings.practice_domain_connect_key_host.strip(),
        portal_target=settings.practice_domain_connect_portal_target.strip().lower(),
        cname_target=settings.practice_domain_cname_target.strip().lower().rstrip("."),
        apex_ips=tuple(
            ip.strip() for ip in settings.practice_domain_apex_ips.split(",") if ip.strip()
        ),
        redirect_uri=f"{settings.app_url.rstrip('/')}{RETURN_PATH}" if settings.app_url else "",
    )
    key_version = settings.practice_domain_connect_kms_key_version.strip()
    enabled = config.enabled and bool(key_version)
    return DomainConnectService(
        repo,
        DomainConnectDiscovery(lookup, fetch),
        KmsSigner(key_version) if enabled else None,
        _state_key() if enabled else None,
        config,
    )
