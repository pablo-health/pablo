# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Route-level security markers.

Every API route MUST be classifiable into exactly one of four
postures, all of which are explicit at the route declaration:

1. **MFA-required (default)** — the route's dependency tree includes
   ``require_mfa`` (directly or transitively via ``get_current_user``,
   ``get_tenant_context``, ``require_admin``, etc.). This is what
   the vast majority of routes use.

2. **Pre-MFA-enrollment onboarding** — the route uses
   ``Depends(get_current_user_no_mfa)``. The caller is authenticated
   but has not yet completed MFA enrollment. Use only for the
   chicken-and-egg endpoints needed before MFA is in place.

3. **Service-account auth** — the route uses
   ``Depends(require_pentest_runner)`` (or a future similar
   service-account dependency). Authenticated by Cloud Tasks-style
   ID-token verification rather than user session.

4. **Truly public (this module)** — the route uses
   ``Depends(truly_public)``. No authentication is required;
   anonymous internet traffic is accepted. Use only for health
   probes, pre-auth flow endpoints, and webhooks that verify their
   own signature inside the handler.

The test ``tests/test_route_mfa_guardrails.py`` enumerates every
route on the app and fails if any route is not classifiable into one
of the four — there is no allowlist. The security posture of every
route is declared at the route itself, in the diff at PR time, where
a reviewer is naturally looking.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def truly_public() -> None:
    """Marker dependency: this route is intentionally public.

    A no-op dependency that exists to make the security posture
    explicit in the route declaration. ``Depends(truly_public)`` says:

    - No authentication is required.
    - Anonymous internet traffic is accepted.
    - This was a deliberate choice that a reviewer signed off on.

    Use only when there is no alternative — health probes, pre-auth
    flow endpoints, webhooks with their own signature verification,
    IAP-gated routes (where authentication happens at the load
    balancer). Anything that returns user data or accepts user
    actions MUST go through ``get_current_user`` (MFA required) or
    ``get_current_user_no_mfa`` (pre-MFA onboarding) instead.

    Any PR that adds ``Depends(truly_public)`` to a new route should
    be treated as a security-review-required change.
    """
    return None
