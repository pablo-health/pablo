# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Every practice's hosted addresses, under a domain the deployment names.

A deployment that sets ``PRACTICE_HOSTED_DOMAIN`` (say ``hosted.example``)
gives every practice two addresses that need no DNS work of its own, from the
practice's portal slug (:mod:`app.portal.slugs`):

* ``{slug}.hosted.example`` — its website, once it has published one;
* ``{slug}.portal.hosted.example`` — its client portal.

The deployment points ``*.hosted.example`` and ``*.portal.hosted.example`` at
whatever serves practice hosts, with one certificate for both wildcards, so a
new practice needs nothing from anyone. Website and portal stay on different
origins: a website runs the practice's own scripts, and a portal session lives
in its origin's storage.

These sit beside the hosts a practice adds itself (Settings > Domains), never
in place of them. When the practice has a working primary host of its own for
a purpose, its hosted address of that purpose sends visitors there; otherwise
the hosted address is the practice's address. A practice cannot add a host
under the hosted domain itself (:mod:`app.services.practice_domain_service`).

A slug that is not a DNS label, or that is reserved, has no hosted address.
Unset, nothing here answers anything and every host is served as before.

**Shown only once served.** The hosted addresses resolve as soon as the
domain is named, but nothing points anyone at them — Settings shows none,
portal links stay on the practice's own host or the shared one, and the
portal links back to no hosted website — until ``PRACTICE_HOSTED_DOMAIN_READY``
says the wildcards are in DNS and their certificate is issued
(:func:`hosted_addresses_ready`). So naming the domain early never sends
anyone to an address that cannot be reached yet.

No PHI: hostnames and slugs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from sqlalchemy import select

from ..db.platform_models import PortalPracticeSlugRow
from ..settings import get_settings
from .slugs import RESERVED_SLUGS, is_dns_label

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: The label between a practice's slug and the hosted domain on its portal host.
PORTAL_LABEL = "portal"

type HostedPurpose = Literal["portal", "site"]


def hosted_domain() -> str | None:
    """The deployment's hosted domain, or ``None`` when it names none."""
    return get_settings().practice_hosted_domain or None


def hosted_addresses_ready() -> bool:
    """Whether hosted addresses may be shown and linked to: a domain is named
    and the deployment says it is served (``PRACTICE_HOSTED_DOMAIN_READY``)."""
    settings = get_settings()
    return bool(settings.practice_hosted_domain) and settings.practice_hosted_domain_ready


def _hostable(slug: str) -> bool:
    return is_dns_label(slug) and slug not in RESERVED_SLUGS


def hosted_site_host(slug: str) -> str | None:
    """The website host the practice whose address is *slug* has here."""
    domain = hosted_domain()
    if domain is None or not _hostable(slug):
        return None
    return f"{slug}.{domain}"


def hosted_portal_host(slug: str) -> str | None:
    """The portal host the practice whose address is *slug* has here."""
    domain = hosted_domain()
    if domain is None or not _hostable(slug):
        return None
    return f"{slug}.{PORTAL_LABEL}.{domain}"


def hosted_slug(host: str, purpose: HostedPurpose) -> str | None:
    """The slug *host* is the hosted address of, for *purpose*; else ``None``.

    *host* is already normalized
    (:func:`app.portal.practice_hosts.normalize_request_host`).
    """
    domain = hosted_domain()
    if domain is None:
        return None
    suffix = f".{PORTAL_LABEL}.{domain}" if purpose == "portal" else f".{domain}"
    slug = host.removesuffix(suffix)
    if slug == host or "." in slug or not _hostable(slug):
        return None
    return slug


def hosted_practice_id(session: Session, host: str, purpose: HostedPurpose) -> str | None:
    """The practice *host* is the hosted *purpose* address of, or ``None``."""
    slug = hosted_slug(host, purpose)
    if slug is None:
        return None
    return session.execute(
        select(PortalPracticeSlugRow.practice_id).where(PortalPracticeSlugRow.slug == slug)
    ).scalar_one_or_none()


def practice_slug(session: Session, practice_id: str) -> str | None:
    """The practice's portal slug, if it has been given one."""
    return session.execute(
        select(PortalPracticeSlugRow.slug).where(PortalPracticeSlugRow.practice_id == practice_id)
    ).scalar_one_or_none()


def is_under_hosted_domain(host: str) -> bool:
    """Whether *host* is the hosted domain or a name under it."""
    domain = hosted_domain()
    return domain is not None and (host == domain or host.endswith(f".{domain}"))
