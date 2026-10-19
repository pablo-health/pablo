# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What the domain reconciler asks of whatever serves practice hosts.

Serving a host takes four things, each kept per host: a DNS authorisation (the
``_acme-challenge`` CNAME the practice adds), a certificate issued against it,
a certificate-map entry that hands the certificate out for the hostname, and a
host rule on the load balancer's URL map that routes the hostname somewhere.
:class:`DomainServing` is that list as a seam, so the reconciler's decisions
are tested against a fake and the Google Cloud calls live in
``practice_domain_gcp`` alone.

Where a host rule routes is a :class:`HostRoute`. Every host starts on the
deployment's own path matcher (the frontend answers for it). A deployment that
serves published websites straight from object storage (see
``app.sites.mirror``) routes a website host to a path matcher of the
practice's own, which rewrites every path into the live version's folder in
the public bucket, and routes the website's other hosts to a redirect to its
primary. The reconciler decides the route; the serving makes the URL map say
so.

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
    #: Set when the issuer's authorisation attempt for the host failed: its
    #: reason (``CONFIG`` when the ``_acme-challenge`` record was not in place
    #: when it looked, ``CAA``, ``RATE_LIMITED``). A certificate can sit in
    #: ``PROVISIONING`` with such an attempt and not try again for a long time.
    attempt_failure: str | None = None

    @property
    def stuck(self) -> bool:
        """Failed, or not active with a failed authorisation attempt behind it."""
        return self.state == "FAILED" or (
            self.state != "ACTIVE" and self.attempt_failure is not None
        )


class DomainServingError(Exception):
    """A call to serve or stop serving a host did not go through.

    ``transient`` separates "try again next sweep" (the service was busy or
    slow) from "this will not work until something changes" (a refused
    request, a missing permission), which is what puts a host in ``error``.
    """

    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


RouteKind = Literal["default", "site", "alias"]


@dataclass(frozen=True)
class HostRoute:
    """Where a host's host rule points.

    ``default``: the deployment's own path matcher (``ServingConfig.path_matcher``).
    ``site``: the practice's static-site matcher, serving ``version`` of the
    practice's website from the public bucket. ``alias``: a redirect to
    ``target``, for a website host that is not the website's primary.
    """

    kind: RouteKind
    practice_id: str = ""
    version: int = 0
    target: str = ""

    @classmethod
    def default(cls) -> HostRoute:
        return cls("default")

    @classmethod
    def site(cls, practice_id: str, version: int) -> HostRoute:
        return cls("site", practice_id=practice_id, version=version)

    @classmethod
    def alias(cls, target: str) -> HostRoute:
        return cls("alias", target=target)


def site_matcher_id(practice_id: str) -> str:
    """The URL-map path matcher a practice's static website is served by."""
    return resource_id("site-", practice_id)


def alias_matcher_id(host: str) -> str:
    """The URL-map path matcher that redirects *host* to its website's primary."""
    return resource_id("alias-", host)


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

    def recreate_certificate(self, host: str) -> CertificateStatus:
        """Delete the host's certificate and request it again against the same
        DNS authorisation, so the record the practice added stays right.

        A map entry naming the certificate is removed first and put back after,
        since a certificate in use cannot be deleted.
        """
        ...

    def ensure_map_entry(self, host: str) -> None:
        """Hand the host's certificate out for its hostname."""
        ...

    def ensure_host_rule(self, host: str, route: HostRoute | None = None) -> None:
        """Route the hostname where *route* says (the deployment itself when
        ``None``), making the path matcher the route needs if it is missing and
        moving the host off the one it was on. One edit, so the map never
        names a matcher nothing routes to."""
        ...

    def invalidate_host(self, host: str) -> None:
        """Drop whatever a cache in front of the load balancer holds for the
        host, after what it serves changed."""
        ...

    def remove_host_rule(self, host: str) -> None:
        """Stop routing the hostname, and drop a practice-site or alias path
        matcher left with nothing routing to it."""
        ...

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
    #: The URL map's backend bucket that published websites are served from,
    #: when the deployment serves them from object storage; ``None`` leaves
    #: every website host on ``path_matcher``.
    site_backend: str | None = None

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
        project, certificate_map, url_map, path_matcher = values.values()
        return cls(
            project,
            certificate_map,
            url_map,
            path_matcher,
            site_backend=settings.practice_domain_site_backend.strip() or None,
        )


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
