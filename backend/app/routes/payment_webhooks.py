# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Card-processor webhook receiver — charge outcomes.

The charge route (``app.routes.patient_payments``) writes its ledger row and
updates it from the synchronous PaymentIntent response, so on the happy path
this endpoint is a confirmation. It exists for the three cases that response
cannot cover:

* ``payment_intent.succeeded`` — a charge that completed after our HTTP call
  gave up (a timeout, a redeploy mid-flight). The ledger row is still
  ``pending`` and only Stripe knows it went through.
* ``payment_intent.payment_failed`` — the same in the other direction, with the
  decline code the practice needs to see.
* ``charge.refunded`` — a refund the practice issued in its own Stripe
  dashboard. This application deliberately does not initiate refunds; the
  practice already has a full dashboard for that, and re-implementing it here
  would be a second, worse refund UI. So this event is the only way the ledger
  learns about one. Any refund, partial or full, flips the row to ``refunded``;
  the exact amounts live at the processor.

Authentication
--------------

Stripe signs the raw request body. :func:`app.payments.reconcile.verify_signature`
recomputes the HMAC and compares it in constant time, before the body is parsed
or otherwise touched. Missing or invalid signature is ``401``; a body that is
not a JSON object is ``400``. Everything else answers ``200``, including events
this deployment ignores — Stripe treats a non-2xx as retryable, and a retry
loop over an unhandleable event helps nobody and eventually gets the endpoint
disabled, which would stop the real charges reconciling too.

Idempotency, and the one thing not recorded
-------------------------------------------

Handled events are recorded in ``platform.processed_payment_events``, so a
redelivery short-circuits before any practice schema is touched.

**The dedupe row is written only when the event was actually handled.**
Recording an event is a promise that Stripe may stop redelivering it, and that
redelivery is the only retry this endpoint has. So the row is written for: the
ledger moved (``APPLIED``); a stale delivery the status guard correctly refused
(``STALE``, since a retry could only produce the same refusal); and a charge
that was never this application's (``FOREIGN``, which no redelivery could
change).

The one case NOT recorded is ``NOT_FOUND``: the event carried our own metadata,
so the charge *is* ours, and the ledger write still matched nothing. That is a
money-visible anomaly and a silent one by nature — Stripe holds a completed
payment while the ledger says ``pending``, and the practice concludes it was
not paid. So the event is left unrecorded, the endpoint answers ``503`` to buy
another redelivery, and ``charge_unreconciled`` is logged at error level as the
marker to alert on.

Is this charge even ours?
-------------------------

Most events on a practice's Stripe account are not. The practice has a real
Stripe dashboard and will routinely take money outside this application —
manual charges, payment links, invoices — and every one of those emits
``payment_intent.succeeded`` on the same account. That is ordinary traffic, not
an anomaly, and it must cost a ``200``.

The discriminator is our own metadata. ``app.routes.patient_payments`` stamps
the ledger row id, the acting clinician and the practice onto every
PaymentIntent it creates, and Stripe copies PaymentIntent metadata onto the
charge it creates, so every handled event type carries it back whenever the
charge is ours — ``charge.refunded`` included. No metadata therefore means the
practice's own charge: record it and 200.

Logs here carry the event id, our own charge id and a status token. Never a
client identifier, never an amount, never a name.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from ..auth.route_security import truly_public
from ..payments.reconcile import (
    METADATA_CHARGE_ID,
    METADATA_PRACTICE_ID,
    METADATA_USER_ID,
    ChargeApply,
    ChargeOutcome,
    apply_charge_outcome,
    event_already_processed,
    extract_charge_fields,
    log_unreconciled,
    record_processed_event,
    resolve_practice_schema,
    verify_signature,
)
from ..settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["payment-webhooks"])

PAYMENT_WEBHOOK_PATH = "/api/webhooks/payments/stripe"

#: Event types this receiver acts on. Anything else is acknowledged and
#: ignored.
_HANDLED_EVENTS = frozenset(
    {"payment_intent.succeeded", "payment_intent.payment_failed", "charge.refunded"}
)


@router.post(PAYMENT_WEBHOOK_PATH)
async def payment_webhook(
    request: Request,
    stripe_signature: Annotated[str | None, Header()] = None,
    _public: None = Depends(truly_public),
) -> dict[str, str]:
    """Receive card-payment events and reconcile the charge ledger.

    Public by necessity — the processor cannot hold a session — and
    authenticated by the signature over the request body instead.
    """
    settings = get_settings()
    body = await request.body()

    if not verify_signature(body, stripe_signature or "", settings):
        logger.warning(
            "payment_webhook_signature_invalid header_present=%s body_len=%d",
            bool(stripe_signature),
            len(body),
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid webhook signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        logger.warning("payment_webhook_bad_json err=%s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid JSON") from None
    if not isinstance(payload, dict):
        logger.warning("payment_webhook_payload_not_object")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "expected JSON object")

    event_id = str(payload.get("id") or "")
    event_type = str(payload.get("type") or "")
    if not event_id:
        logger.info("payment_webhook_unusable event_type=%s", event_type)
        return {"status": "ok"}

    if event_type not in _HANDLED_EVENTS:
        logger.info("payment_webhook_unhandled event=%s type=%s", event_id, event_type)
        return {"status": "ok"}

    # Dedupe BEFORE any practice write: a redelivery of an event already
    # applied is a no-op.
    if event_already_processed(event_id):
        logger.info("payment_webhook_duplicate event=%s type=%s", event_id, event_type)
        return {"status": "ok", "deduped": "true"}

    data = payload.get("data")
    obj = data.get("object") if isinstance(data, dict) else None
    if not isinstance(obj, dict):
        logger.warning("payment_webhook_no_object event=%s type=%s", event_id, event_type)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing data.object")

    new_status, status_detail, payment_intent_id = extract_charge_fields(event_type, obj)
    metadata = obj.get("metadata") or {}
    acting_user_id = str(metadata.get(METADATA_USER_ID) or "")
    charge_id = str(metadata.get(METADATA_CHARGE_ID) or "")
    practice_id = str(metadata.get(METADATA_PRACTICE_ID) or "") or None

    # Is it ours? Absence of our metadata is the discriminator: the practice
    # took this money some other way through its own Stripe account, which is
    # ordinary traffic rather than an error.
    if not payment_intent_id or not acting_user_id or not charge_id:
        logger.info("payment_webhook_foreign_charge event=%s type=%s", event_id, event_type)
        record_processed_event(
            event_id=event_id,
            event_type=event_type,
            practice_id=None,
            created=payload.get("created"),
        )
        return {"status": "ok"}

    resolved = resolve_practice_schema(practice_id)
    if resolved is None:
        # A signed event naming a practice this deployment does not have (or
        # has deactivated). Nothing a redelivery could fix, so acknowledge and
        # record rather than retry forever.
        logger.info("payment_webhook_unknown_practice event=%s", event_id)
        record_processed_event(
            event_id=event_id,
            event_type=event_type,
            practice_id=None,
            created=payload.get("created"),
        )
        return {"status": "ok"}
    resolved_practice_id, schema_name = resolved

    applied: ChargeApply = apply_charge_outcome(
        schema_name=schema_name,
        payment_intent_id=payment_intent_id,
        new_status=new_status,
        status_detail=status_detail,
        acting_user_id=acting_user_id,
    )

    if applied.outcome is ChargeOutcome.NOT_FOUND:
        # The event carried our metadata and still matched no ledger row. Do
        # NOT record it: the processor's redelivery is the only retry this
        # endpoint has, and spending it on a failure is how a completed payment
        # ends up sitting at ``pending`` indefinitely.
        log_unreconciled(
            event_id=event_id,
            event_type=event_type,
            payment_intent_id=payment_intent_id,
            reason="event carries our metadata but no ledger row matched",
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "could not reconcile charge; please retry"
        )

    if applied.attributed_to is not None:
        # The row updated was created by a different clinician than the event
        # claimed. The write still stands and the event is still recorded — the
        # row was pinned by the signed PaymentIntent id inside a schema
        # resolved from the signed practice id, so the ledger outcome is
        # correct and a retry would change nothing — but the event's claim
        # about WHO disagreed with our own record, and that is worth waking
        # someone for.
        log_unreconciled(
            event_id=event_id,
            event_type=event_type,
            payment_intent_id=payment_intent_id,
            reason="event user id does not match the ledger row's creator",
        )

    # APPLIED or STALE — both handled. A stale delivery is a late duplicate of
    # something already applied, so a retry could only reach the same answer;
    # recording is what stops the loop.
    record_processed_event(
        event_id=event_id,
        event_type=event_type,
        practice_id=resolved_practice_id,
        created=payload.get("created"),
    )
    logger.info(
        "payment_webhook_processed event=%s type=%s status=%s outcome=%s",
        event_id,
        event_type,
        new_status,
        applied.outcome.value,
    )
    return {"status": "ok"}
