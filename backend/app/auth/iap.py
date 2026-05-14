# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""
Google Cloud Identity-Aware Proxy (IAP) JWT verification.

When auth_mode=iap, IAP authenticates users at the load balancer level
via their Google account. This module provides defense-in-depth by
verifying the IAP-signed JWT header on each request — without it, a
request hitting the Cloud Run *.run.app URL directly would bypass IAP
entirely.

This satisfies HIPAA §164.312(d) (Person or Entity Authentication)
without requiring app-level MFA, since IAP provides strong Google
account authentication before traffic reaches Cloud Run.
"""

import logging
from typing import Any

from fastapi import HTTPException, Request, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from ..settings import get_settings

logger = logging.getLogger(__name__)

IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
IAP_HEADER = "X-Goog-IAP-JWT-Assertion"


def verify_iap_jwt(iap_jwt: str, expected_audience: str) -> dict[str, object]:
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
    logger.debug("IAP JWT verified for user: %s", decoded.get("sub", "unknown"))
    result: dict[str, object] = decoded
    return result


def require_iap_assertion(request: Request) -> dict[str, Any]:
    """Require a valid X-Goog-IAP-JWT-Assertion header on this request.

    Called from auth dependencies whenever ``auth_mode == "iap"`` to
    prove the request actually came through the IAP-protected load
    balancer. Without this check, a Firebase token sent directly to
    Cloud Run's *.run.app ingress would skip IAP entirely and reach
    PHI routes single-factor.

    The decoded claims are cached on ``request.state`` so multiple
    dependencies in a single request don't re-verify.

    Raises:
        HTTPException 401 if the header is missing or invalid.
        HTTPException 500 if iap_audience is not configured.
    """
    cached = getattr(request.state, "iap_claims", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    settings = get_settings()
    if not settings.iap_audience:
        logger.error("auth_mode=iap but IAP_AUDIENCE is unset")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": {
                    "code": "IAP_MISCONFIGURED",
                    "message": "IAP audience is not configured",
                    "details": {},
                }
            },
        )

    assertion = request.headers.get(IAP_HEADER)
    if not assertion:
        logger.warning("IAP assertion header missing on %s", request.url.path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "IAP_ASSERTION_MISSING",
                    "message": "Request did not traverse Identity-Aware Proxy",
                    "details": {},
                }
            },
        )

    try:
        claims = verify_iap_jwt(assertion, settings.iap_audience)
    except Exception as err:
        logger.warning("IAP assertion verification failed: %s", err)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "IAP_ASSERTION_INVALID",
                    "message": "IAP assertion is invalid or expired",
                    "details": {},
                }
            },
        ) from err

    result: dict[str, Any] = dict(claims)
    request.state.iap_claims = result
    return result
