# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Thin Stripe REST client for the card-payment routes.

Form-encoded body, secret key as the basic-auth username, every call through
the shared outbound-retry engine (``app.reliability``), and every failure mode
mapped onto one 502 contract so callers do not each invent their own. Writes
carry an ``Idempotency-Key`` derived from the caller's own ledger row, so a
retry — ours or the retry engine's — can never charge a card twice.

Two entry points, because a card decline is not a transport failure:

* :func:`stripe_request` raises on any non-2xx. Right for customers and
  SetupIntents, where there is no such thing as a meaningful 4xx.
* :func:`payment_intent_request` passes HTTP 402 back to the caller unraised.
  402 is Stripe saying "the card said no", which is an *answer*: it carries the
  decline code the ledger has to record, and retrying it would be useless.

Log lines here carry the request path and the upstream status only — never a
request body, never an email, never a client identifier.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import HTTPException, status

from ..reliability import HTTP_REQUEST, Idempotency, RetryExhaustedError, call_with_retry

logger = logging.getLogger(__name__)

STRIPE_API_BASE = "https://api.stripe.com"

_REQUEST_TIMEOUT_SECONDS = 10.0

#: Stripe's "the card was declined" status — a terminal answer, not a
#: transport failure.
HTTP_PAYMENT_REQUIRED = 402


def _headers(*, idempotency_key: str | None, account_id: str | None) -> dict[str, str]:
    """Build the request headers.

    ``Stripe-Account`` is sent only when the deployment's provider named an
    account (see :mod:`app.payments.provider`); the default configuration omits
    it, and a call with no header is a plain charge on the key's own account.
    """
    headers: dict[str, str] = {}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    if account_id:
        headers["Stripe-Account"] = account_id
    return headers


def stripe_request(  # noqa: PLR0913 — keyword-only request knobs, not a call-site burden
    method: str,
    path: str,
    *,
    secret_key: str,
    account_id: str | None = None,
    data: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Call the Stripe v1 REST API. Raises ``HTTPException(502)`` on failure."""

    def _call() -> httpx.Response:
        response = httpx.request(
            method,
            f"{STRIPE_API_BASE}{path}",
            data=data,
            auth=(secret_key, ""),
            timeout=_REQUEST_TIMEOUT_SECONDS,
            headers=_headers(idempotency_key=idempotency_key, account_id=account_id),
        )
        response.raise_for_status()
        return response

    idempotency = Idempotency.KEYED if idempotency_key else Idempotency.SAFE
    try:
        response = call_with_retry(_call, policy=HTTP_REQUEST, idempotency=idempotency)
    except RetryExhaustedError as exc:
        logger.error("stripe_request_unreachable path=%s err=%s", path, exc.last_exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach the card processor.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        logger.error("stripe_request_error path=%s status=%d", path, exc.response.status_code)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The card processor rejected the request.",
        ) from None
    except httpx.RequestError as exc:
        logger.error("stripe_request_failed path=%s err=%s", path, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach the card processor.",
        ) from None

    body: dict[str, Any] = response.json()
    return body


def payment_intent_request(
    path: str,
    *,
    secret_key: str,
    account_id: str | None,
    data: dict[str, Any],
    idempotency_key: str,
) -> tuple[bool, dict[str, Any]]:
    """POST to a PaymentIntent endpoint; return ``(accepted, body)``.

    ``accepted`` is False for exactly one case: HTTP 402, the card decline. The
    body is then Stripe's error envelope, which carries the decline code the
    ledger records and the PaymentIntent the decline happened on. Every other
    non-2xx keeps :func:`stripe_request`'s behaviour and becomes a 502, so the
    caller leaves its ledger row ``pending`` for reconciliation rather than
    guessing an outcome.

    Note what the idempotency key does and does not cover. It is derived from
    the caller's ledger row id, so the retry engine re-sending a timed-out
    confirm cannot charge twice. A second click, by contrast, mints a new
    ledger row and therefore a new key — a second, deliberate charge. Guarding
    against double-clicks belongs in the UI, which is the right layer: two
    charges a minute apart can be entirely legitimate.
    """
    headers = _headers(idempotency_key=idempotency_key, account_id=account_id)

    def _call() -> httpx.Response:
        response = httpx.request(
            "POST",
            f"{STRIPE_API_BASE}{path}",
            data=data,
            auth=(secret_key, ""),
            timeout=_REQUEST_TIMEOUT_SECONDS,
            headers=headers,
        )
        if response.status_code != HTTP_PAYMENT_REQUIRED:
            response.raise_for_status()
        return response

    try:
        response = call_with_retry(_call, policy=HTTP_REQUEST, idempotency=Idempotency.KEYED)
    except RetryExhaustedError as exc:
        logger.error("stripe_payment_intent_unreachable err=%s", exc.last_exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach the card processor; the charge was not completed.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        logger.error("stripe_payment_intent_error status=%d", exc.response.status_code)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The card processor rejected the charge request.",
        ) from None
    except httpx.RequestError as exc:
        logger.error("stripe_payment_intent_request_failed err=%s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach the card processor; the charge was not completed.",
        ) from None

    body: dict[str, Any] = response.json()
    return response.status_code != HTTP_PAYMENT_REQUIRED, body
