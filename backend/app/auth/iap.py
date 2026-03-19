# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""
Google Cloud Identity-Aware Proxy (IAP) JWT verification.

When auth_mode=iap, IAP authenticates users at the load balancer level
via their Google account. This module provides defense-in-depth by
verifying the IAP-signed JWT header on each request.

This satisfies HIPAA §164.312(d) (Person or Entity Authentication)
without requiring app-level MFA, since IAP provides strong Google
account authentication before traffic reaches Cloud Run.
"""

import logging

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

logger = logging.getLogger(__name__)

IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"


def verify_iap_jwt(iap_jwt: str, expected_audience: str) -> dict:
    """Verify a Google Cloud IAP JWT assertion.

    Args:
        iap_jwt: The value of the X-Goog-IAP-JWT-Assertion header.
        expected_audience: The expected audience claim, typically
            /projects/{number}/global/backendServices/{id}.

    Returns:
        The decoded JWT claims dict (sub, email, etc.).

    Raises:
        ValueError: If the JWT is invalid, expired, or has wrong audience.
    """
    decoded = id_token.verify_token(
        iap_jwt,
        google_requests.Request(),
        audience=expected_audience,
        certs_url=IAP_CERTS_URL,
    )
    logger.debug("IAP JWT verified for user: %s", decoded.get("email", "unknown"))
    return decoded
