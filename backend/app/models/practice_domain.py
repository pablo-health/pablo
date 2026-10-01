# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A host a practice serves its client portal or website from, and its API shapes.

No PHI: public hostnames and their setup state. See
``app.db.platform_models.PracticeDomainRow`` and ``PracticeDomainApexRow`` for
the tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DomainPurpose = Literal["portal", "site"]
DomainStatus = Literal["pending", "verifying", "active", "error"]
#: A stored host's status: the shown ones, plus ``removing`` — the practice
#: removed it and what serves it is still being taken down. A removing host is
#: no longer the practice's, as far as anything it is shown goes.
HostStatus = Literal["pending", "verifying", "active", "error", "removing"]
DomainKind = Literal["subdomain", "vanity"]
EmailIdentityStatus = Literal["pending", "verified", "failed"]
#: What a DNS check found for one record: there and matching (``ok``), not
#: there (``missing``), there with another value (``wrong``), or no answer in
#: time to tell (``unknown``).
RecordCheck = Literal["ok", "missing", "wrong", "unknown"]


@dataclass
class PracticeDomain:
    domain: str
    practice_id: str
    purpose: DomainPurpose
    kind: DomainKind
    status: HostStatus
    is_primary: bool
    created_at: datetime
    verified_at: datetime | None = None
    updated_at: datetime | None = None
    #: The ``_acme-challenge`` CNAME's value, before
    #: ``.authorize.certificatemanager.goog``. ``None`` until a certificate is
    #: requested for the host.
    cert_auth_value: str | None = None
    #: The certificate's state as its issuer last reported it (``PROVISIONING``,
    #: ``ACTIVE``, ``FAILED``); ``None`` until one is requested.
    cert_status: str | None = None
    #: Why the host is in ``error``, in words for whoever runs the deployment.
    last_error: str | None = None


@dataclass(frozen=True)
class ServingState:
    """What serving a host found: the columns the domain reconciler writes."""

    status: HostStatus
    cert_auth_value: str | None
    cert_status: str | None
    last_error: str | None
    verified_at: datetime | None

    @classmethod
    def of(cls, host: PracticeDomain) -> ServingState:
        return cls(
            status=host.status,
            cert_auth_value=host.cert_auth_value,
            cert_status=host.cert_status,
            last_error=host.last_error,
            verified_at=host.verified_at,
        )


@dataclass
class PracticeDomainApex:
    """The registrable domain a practice's hosts sit under."""

    apex: str
    practice_id: str
    verify_token: str
    created_at: datetime
    verified_at: datetime | None = None
    email_identity_status: EmailIdentityStatus | None = None
    email_dkim_tokens: list[str] | None = None
    updated_at: datetime | None = None


class DnsRecord(BaseModel):
    """A record the practice adds at its DNS provider.

    ``name`` is the full name (``_pablo-verify.example.com``), as the host
    records have always been given.
    """

    type: str
    name: str
    value: str
    #: Set only in the answer to a DNS check: what was found for this record.
    check: RecordCheck | None = None
    #: With ``check``: the values found at that name and type, if any.
    found: list[str] | None = None


class PracticeDomainResponse(BaseModel):
    domain: str
    purpose: DomainPurpose
    status: DomainStatus
    is_primary: bool
    verified_at: datetime | None
    created_at: datetime
    #: What to add at the DNS provider. Empty when this deployment has no single
    #: target to name and nothing else is known yet; the page then says only
    #: that the host must point here.
    #:
    #: The host's own record comes first. Then, when known: the certificate
    #: authorisation CNAME for the host, and — on one host per registrable
    #: domain, see ``PracticeDomainService.list_responses`` — the domain's
    #: ownership TXT and DKIM CNAMEs.
    dns_records: list[DnsRecord]
    #: For a bare domain shown address records: the name an ALIAS/ANAME record
    #: could point at instead, where the DNS provider offers one.
    alias_alternative: str | None = None
    #: The registrable domain the host sits under (``example.co.uk``).
    apex: str | None = None
    #: When the domain's ownership record was last found.
    apex_verified_at: datetime | None = None


class PracticeDomainListResponse(BaseModel):
    domains: list[PracticeDomainResponse]


class DomainNameResponse(BaseModel):
    """What the server makes of a name before it is added."""

    #: The name as it would be stored.
    domain: str
    #: The registrable domain it sits under.
    apex: str
    #: Whether it is the registrable domain itself, which is when a website
    #: gets its ``www.`` alias unless told otherwise.
    bare: bool


class AddPracticeDomainRequest(BaseModel):
    # Generous outer bound; the hostname rules are checked by the service so
    # the reader gets words rather than a schema error.
    domain: str = Field(max_length=512)
    purpose: DomainPurpose
    #: Website only: also add ``www.<domain>`` as an alias. Left out, it is
    #: added for a bare domain (``example.com``, ``example.co.uk``) and not
    #: otherwise.
    include_www: bool | None = None
