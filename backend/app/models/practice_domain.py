# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A host a practice serves its client portal or website from, and its API shapes.

No PHI: public hostnames and their setup state. See
``app.db.platform_models.PracticeDomainRow`` for the table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DomainPurpose = Literal["portal", "site"]
DomainStatus = Literal["pending", "verifying", "active", "error"]
DomainKind = Literal["subdomain", "vanity"]


@dataclass
class PracticeDomain:
    domain: str
    practice_id: str
    purpose: DomainPurpose
    kind: DomainKind
    status: DomainStatus
    is_primary: bool
    created_at: datetime
    verified_at: datetime | None = None
    updated_at: datetime | None = None


class DnsRecord(BaseModel):
    """A record the practice adds at its DNS provider."""

    type: str
    name: str
    value: str


class PracticeDomainResponse(BaseModel):
    domain: str
    purpose: DomainPurpose
    status: DomainStatus
    is_primary: bool
    verified_at: datetime | None
    created_at: datetime
    #: What to add at the DNS provider. Empty when this deployment has no single
    #: target to name; the page then says only that the host must point here.
    dns_records: list[DnsRecord]


class PracticeDomainListResponse(BaseModel):
    domains: list[PracticeDomainResponse]


class AddPracticeDomainRequest(BaseModel):
    # Generous outer bound; the hostname rules are checked by the service so
    # the reader gets words rather than a schema error.
    domain: str = Field(max_length=512)
    purpose: DomainPurpose
    #: Website only: also add ``www.<domain>`` as an alias. Left out, it is
    #: added for a two-label name (``example.com``) and not otherwise.
    include_www: bool | None = None
