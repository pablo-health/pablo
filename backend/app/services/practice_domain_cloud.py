# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What the domain reconciler asks of whatever serves practice hosts.

Serving a host takes four things, each kept per host: a DNS authorisation (the
``_acme-challenge`` CNAME the practice adds), a certificate issued against it,
a certificate-map entry that hands the certificate out for the hostname, and a
host rule on the load balancer's URL map that routes the hostname to the
deployment. :class:`DomainServing` is that list as a seam, so the reconciler's
decisions are tested against a fake and the Google Cloud calls live in
``practice_domain_gcp`` alone.

Every ``ensure_*`` is idempotent: it creates what is missing and reports what is
there. Every ``remove_*`` is idempotent too, and touches only a resource that
belongs to the host it was asked about.

No PHI: public hostnames and the state of their certificates.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from ..settings import Settings

CertificateState = Literal["PROVISIONING", "ACTIVE", "FAILED"]

#: Certificate Manager resource ids: a lowercase letter, then up to 62 of
#: letters, digits and hyphens, not ending in a hyphen.
_RESOURCE_ID = re.compile(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")
_MAX_RESOURCE_ID = 63
_DIGEST_LENGTH = 10


@dataclass(frozen=True)
class CertificateStatus:
    state: CertificateState
    #: What the issuer says is in the way, when it says anything.
    detail: str | None = None


class DomainServingError(Exception):
    """A call to serve or stop serving a host did not go through.

    ``transient`` separates "try again next sweep" (the service was busy or
    slow) from "this will not work until something changes" (a refused
    request, a missing permission), which is what puts a host in ``error``.
    """

    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


class DomainServing(Protocol):
    def ensure_dns_authorization(self, host: str) -> str:
        """Create the host's DNS authorisation if missing.

        Returns the ``_acme-challenge.<host>`` CNAME's value without
        ``.authorize.certificatemanager.goog``.
        """
        ...

    def ensure_certificate(self, host: str) -> CertificateStatus:
        """Request the host's certificate if missing, and report its state."""
        ...

    def ensure_map_entry(self, host: str) -> None:
        """Hand the host's certificate out for its hostname."""
        ...

    def ensure_host_rule(self, host: str) -> None:
        """Route the hostname to the deployment."""
        ...

    def remove_host_rule(self, host: str) -> None: ...

    def remove_map_entry(self, host: str) -> None: ...

    def remove_certificate(self, host: str) -> None: ...

    def remove_dns_authorization(self, host: str) -> None: ...


@dataclass(frozen=True)
class ServingConfig:
    """Where a deployment serves practice hosts from."""

    project: str
    certificate_map: str
    url_map: str
    path_matcher: str

    @classmethod
    def from_settings(cls, settings: Settings) -> ServingConfig | None:
        """The configuration, or ``None`` when none of it is set.

        Raises:
            ValueError: some of it is set and some is not; names what is missing.
        """
        values = {
            "practice_domain_serving_project": settings.practice_domain_serving_project.strip(),
            "practice_domain_certificate_map": settings.practice_domain_certificate_map.strip(),
            "practice_domain_url_map": settings.practice_domain_url_map.strip(),
            "practice_domain_path_matcher": settings.practice_domain_path_matcher.strip(),
        }
        missing = sorted(name for name, value in values.items() if not value)
        if len(missing) == len(values):
            return None
        if missing:
            msg = f"Serving practice hosts needs these settings too: {', '.join(missing)}"
            raise ValueError(msg)
        return cls(*values.values())


def serving_configured(settings: Settings) -> bool:
    """Whether this deployment serves practice hosts through the reconciler.

    A half-set configuration counts: the job refuses to run on it and says
    why, and hosts removed meanwhile wait for it rather than being dropped
    with their serving still in place.
    """
    try:
        return ServingConfig.from_settings(settings) is not None
    except ValueError:
        return True


def resource_id(prefix: str, host: str) -> str:
    """A resource id for *host*: *prefix* plus the host with dots as hyphens.

    ``cm-`` and ``portal.example.org`` give ``cm-portal-example-org``. A name
    that would be too long, or that cannot start the id, is shortened and
    given a digest of the host, so distinct hosts keep distinct ids. Two hosts
    can still slug alike (``a-b.example.org`` and ``a.b-example.org``); the
    ``ensure_*`` calls check a resource's hostname before adopting it.
    """
    slug = host.replace(".", "-")
    candidate = f"{prefix}{slug}"
    if len(candidate) <= _MAX_RESOURCE_ID and _RESOURCE_ID.fullmatch(candidate):
        return candidate
    digest = hashlib.sha256(host.encode()).hexdigest()[:_DIGEST_LENGTH]
    head = f"{prefix or 'h-'}{slug}"[: _MAX_RESOURCE_ID - _DIGEST_LENGTH - 1].rstrip("-")
    return f"{head}-{digest}"
