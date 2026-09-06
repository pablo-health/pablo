# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Clearinghouse webhook receiver — claim acknowledgements.

The clearinghouse posts a ``transaction.processed`` event whenever a
document lands on the practice's account: the outbound 837 it just
translated, or an inbound 277CA or 835. This endpoint is how a claim hears
about its acknowledgement without waiting for the poll (which still runs
as the backstop, through the same code).

Authentication
--------------

The vendor signs the raw body (see :mod:`app.claims.webhooks`). The
signature is verified in constant time before the body is parsed; a
missing or bad signature is ``401``, a body that is not a JSON event is
``400``. The vendor's ``event.ping`` test is answered ``200`` so a
destination can be verified from its dashboard.

What it does
------------

For a transaction event the receiver fetches the transaction through each
practice's own clearinghouse account (a transaction another account owns
is a 404 there, and the next practice is tried), reads the 277CA behind it
if that is what it is, and moves the claim it names — see
:func:`app.claims.acknowledgments.apply_acknowledgment`. An 835 is
acknowledged and left alone; remittance posting has its own path.

Idempotency
-----------

Every applied delivery is recorded on the claim's receipt ledger under
the vendor's event id, which is unique there, and under the transaction
id. A redelivery — or a webhook for a 277CA the poll already applied —
finds the receipt and answers ``200`` without a second transition.

Errors
------

A vendor outage while fetching the transaction is ``503`` so the vendor
redelivers later. Everything else, including a transaction naming no
claim of ours, is ``200``: the vendor treats a non-2xx as retryable and a
retry loop over an unhandleable event only gets the destination disabled.

Logs carry the event id, the transaction id and an outcome token. Never a
control number's owner, never a name.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from ..auth.route_security import truly_public
from ..claims.clearinghouse import ClearinghouseRateLimitedError, ClearinghouseUnavailableError
from ..claims.fanout import ingest_transaction_event
from ..claims.webhooks import (
    PING,
    TRANSACTION_PROCESSED,
    candidate_secrets,
    parse_event,
    verify_signature,
)
from ..settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["claim-webhooks"])

CLEARINGHOUSE_WEBHOOK_PATH = "/api/webhooks/clearinghouse"


@router.post(CLEARINGHOUSE_WEBHOOK_PATH)
async def clearinghouse_webhook(
    request: Request,
    webhook_signature: Annotated[str | None, Header()] = None,
    webhook_timestamp: Annotated[str | None, Header()] = None,
    webhook_id: Annotated[str | None, Header()] = None,
    _public: None = Depends(truly_public),
) -> dict[str, str]:
    """Receive clearinghouse transaction events and apply acknowledgements.

    Public by necessity — the vendor cannot hold a session — and
    authenticated by the signature over the request body instead.
    """
    body = await request.body()
    if not verify_signature(
        body,
        signature_header=webhook_signature,
        timestamp_header=webhook_timestamp,
        message_id=webhook_id,
        secrets=candidate_secrets(get_settings()),
    ):
        logger.warning(
            "clearinghouse_webhook_signature_invalid header_present=%s body_len=%d",
            bool(webhook_signature),
            len(body),
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid webhook signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        logger.warning("clearinghouse_webhook_bad_json err=%s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid JSON") from None
    event = parse_event(payload)
    if event is None:
        logger.warning("clearinghouse_webhook_payload_not_event")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "expected an event object")

    if event.type == PING:
        return {"status": "ok", "outcome": "ping"}
    if event.type != TRANSACTION_PROCESSED:
        logger.info("clearinghouse_webhook_unhandled event=%s type=%s", event.id, event.type)
        return {"status": "ok", "outcome": "ignored"}
    if event.transaction_id is None:
        logger.warning("clearinghouse_webhook_no_transaction event=%s", event.id)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing resource.id")

    try:
        outcome = await asyncio.to_thread(ingest_transaction_event, event)
    except (ClearinghouseUnavailableError, ClearinghouseRateLimitedError) as exc:
        logger.warning(
            "clearinghouse_webhook_vendor_unavailable event=%s error=%s",
            event.id,
            type(exc).__name__,
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "could not fetch the transaction; please retry"
        ) from None
    logger.info(
        "clearinghouse_webhook_processed event=%s transaction=%s outcome=%s",
        event.id,
        event.transaction_id,
        outcome,
    )
    return {"status": "ok", "outcome": outcome}
