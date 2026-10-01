# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""API shapes for one-click DNS setup (Domain Connect) on Settings > Domains.

No PHI: domain names, a DNS provider's name, and a link to it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .practice_domain import (  # noqa: TC001 — Pydantic resolves the field types at runtime
    DomainPurpose,
    PracticeDomainResponse,
)

#: Why a template is not offered for a domain:
#:
#: * ``hosts_differ`` — the practice's hosts under the domain are not the ones
#:   the template sets up (a portal at exactly ``portal.<domain>``; a website
#:   at both ``<domain>`` and ``www.<domain>``).
#: * ``records_pending`` — a value the template needs is not known yet (a
#:   certificate not yet requested, email signing keys not yet issued).
#: * ``provider_unsupported`` — the domain's DNS provider does not offer
#:   Domain Connect's one-click flow for it.
#: * ``template_unsupported`` — the provider offers it, but not this template.
#: * ``lookup_failed`` — the provider could not be asked in time.
#: * ``not_allowed`` — the caller may not change the practice's domains.
ConnectReason = Literal[
    "hosts_differ",
    "records_pending",
    "provider_unsupported",
    "template_unsupported",
    "lookup_failed",
    "not_allowed",
]


class DomainConnectOffer(BaseModel):
    """One template for one domain."""

    service_id: str
    #: Which section of the page it belongs to.
    purpose: DomainPurpose
    #: Whether the domain's DNS provider has the template. ``None`` when it
    #: was not asked, because something closer to home already rules it out.
    supported: bool | None = None
    #: The DNS provider's name, when it was found.
    provider_name: str | None = None
    #: The signed link to the provider. Set only when everything checks out
    #: and the caller may change the practice's domains; signed fresh on every
    #: request.
    url: str | None = None
    reason: ConnectReason | None = None


class DomainConnectApex(BaseModel):
    apex: str
    offers: list[DomainConnectOffer]


class DomainConnectResponse(BaseModel):
    #: Empty when this deployment does not offer one-click setup.
    domains: list[DomainConnectApex]


class DomainConnectReturnRequest(BaseModel):
    """What the DNS provider appended to the return link."""

    state: str = Field(min_length=1, max_length=2048)
    #: An OAuth-style error code (``access_denied`` and the like) when the
    #: provider made no change.
    error: str | None = Field(default=None, max_length=64)


class DomainConnectReturnResponse(BaseModel):
    #: The domain the link was for.
    apex: str
    #: The provider's error code, if it reported one.
    error: str | None = None
    #: The practice's hosts with a fresh DNS check on every record. This, not
    #: the return itself, is what says whether the records are in place.
    domains: list[PracticeDomainResponse]
