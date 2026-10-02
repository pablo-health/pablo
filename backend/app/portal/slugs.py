# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which names a practice's portal address may be.

A practice's slug is its address in two places: the path segment of
``/portal/{slug}``, and — where the deployment names a hosted domain
(:mod:`app.portal.hosted`) — the first label of ``{slug}.{domain}`` and
``{slug}.portal.{domain}``. So a slug has to be a DNS label (lowercase letters,
digits and hyphens, not starting or ending with a hyphen, at most 63
characters), and it must not be a name that reads as part of the service
rather than as a practice.

No PHI: words.
"""

from __future__ import annotations

import re

MIN_SLUG_LENGTH = 3
MAX_SLUG_LENGTH = 63
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

# Names that would be confusing or actively misleading as a practice's own
# address. Not a moderation list — these are words the shell's own routing
# already gives meaning to, or that read as the service rather than as a
# practice. ``redeem`` is the load-bearing one: it is the segment a magic link
# lands on (see ``app.portal.factory``).
#
# The second group is every top-level route the web app serves outside the
# portal. Where the portal has a host of its own it is addressed as ``/{slug}``
# there, and those paths answer 404 rather than reach a practice, so a
# practice holding one would have an address that goes nowhere. The web app
# keeps the same names in ``CLINICIAN_ROUTE_SEGMENTS``
# (frontend/src/lib/portal-host/routing.ts); a frontend unit test reads this
# set and fails when a name there is missing here.
#
# The third group are the names a hosted domain's own infrastructure, mail and
# help pages would use, so that ``{slug}.{domain}`` never reads as the
# deployment speaking.
RESERVED_SLUGS = frozenset(
    {
        "api",
        "app",
        "admin",
        "auth",
        "redeem",
        "refresh",
        "practice",
        "practices",
        "www",
        "static",
        "assets",
        # Top-level web app routes (see above).
        "portal",
        "book",
        "dashboard",
        "fbauth-proxy",
        "launch",
        "login",
        "mfa-enrollment",
        "mfa-step-up",
        "native-auth",
        "onboarding",
        # Names a hosted domain's own hosts would use (see above).
        "sites",
        "dev",
        "staging",
        "mail",
        "email",
        "smtp",
        "notifications",
        "signin",
        "support",
        "help",
        "billing",
        "status",
        "security",
        "docs",
        "blog",
    }
)


def is_dns_label(name: str) -> bool:
    """Whether *name* can be one label of a hostname as it is stored: lowercase."""
    return bool(_DNS_LABEL.match(name))


def is_mintable_slug(name: str) -> bool:
    """Whether *name* may be given to a practice as its address."""
    return len(name) >= MIN_SLUG_LENGTH and is_dns_label(name) and name not in RESERVED_SLUGS
