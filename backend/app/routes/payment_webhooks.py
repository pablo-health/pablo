# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Card-processor webhook receiver — charge outcomes.

The charge route updates its ledger row from the synchronous PaymentIntent
response, so on the happy path this is a confirmation. It exists for what
that response cannot cover:

* ``payment_intent.succeeded`` — completed after our HTTP call gave up. The
  row is still ``pending`` and only Stripe knows.
* ``payment_intent.payment_failed`` — same, with the decline code.
* ``charge.refunded`` — a refund the practice issued in its own dashboard.
  This app never initiates refunds, so this event is the only way the ledger
  learns of one. Partial or full both flip the row to ``refunded``; exact
  amounts live at the processor.
* ``charge.dispute.created`` / ``charge.dispute.closed`` — a chargeback. Not
  a refund: nothing has moved, so the row goes ``disputed``, and closing
  resolves to ``succeeded`` (won) or ``dispute_lost``. Evidence and
  deadlines stay Stripe's; this only keeps the status honest.

**Fees.** ``succeeded`` and ``refunded`` carry the balance transaction when
the endpoint is configured to expand it, and ``fee``/``net`` are read off it.
Both stay NULL until then — some payment methods settle the balance
transaction after the charge, so ``succeeded`` with an unknown fee is real
and is never treated as a zero fee.

**Auth.** Stripe signs the raw body; :func:`app.payments.reconcile.verify_signature`
compares the HMAC in constant time before the body is parsed. Missing or bad
signature is 401, a non-object body is 400. Everything else is 200 — Stripe
retries non-2xx, and a retry loop over an unhandleable event eventually gets
the endpoint disabled, taking the real charges down with it.

**Idempotency.** Handled events are recorded in
``platform.processed_payment_events`` so a redelivery short-circuits before
any practice schema is touched.

Recording an event promises Stripe may stop redelivering, and redelivery is
this endpoint's only retry. So the row is written when the ledger moved
(APPLIED), when the status guard correctly refused a stale delivery (STALE —
a retry could only refuse again), and when the charge was never ours
(FOREIGN — no redelivery changes that).

NOT_FOUND is the one case deliberately left unrecorded: the event carried our
metadata, so the charge IS ours, and the ledger write still matched nothing.
Stripe holds a completed payment while the ledger says ``pending`` and the
practice concludes it was not paid. So: no dedupe row, 503 to buy another
redelivery, and ``charge_unreconciled`` at error level to alert on.

**Is the charge ours?** Usually not. A practice takes money outside this app
— manual charges, payment links, invoices — and every one emits
``payment_intent.succeeded`` on the same account. Ordinary traffic, and it
must cost a 200.

The discriminator is our own metadata: the charge route stamps the ledger row
id, clinician and practice onto every PaymentIntent, and Stripe copies that
onto the Charge. That includes ``charge.refunded``, which delivers a Charge
rather than a PaymentIntent — verified against Stripe test mode to carry the
metadata verbatim. No metadata means the practice's own charge: record, 200.

Disputes are the exception. The bank raises them, so Stripe never populates a
Dispute's own ``metadata``; see ``app.payments.reconcile.event_metadata``.

Logs carry the event id, our charge id and a status token. Never a client
identifier, an amount, or a name.
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
    event_metadata,
    extract_balance_transaction,
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
#: ignored. The two dispute events are not delivered until the endpoint's
#: subscription is updated in the Stripe dashboard — that list is deployment
#: configuration, not something this code controls.
_HANDLED_EVENTS = frozenset(
    {
        "payment_intent.succeeded",
        "payment_intent.payment_failed",
        "charge.refunded",
        "charge.dispute.created",
        "charge.dispute.closed",
    }
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
    metadata = event_metadata(event_type, obj)
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

    fee_cents, net_cents = extract_balance_transaction(event_type, obj)
    applied: ChargeApply = apply_charge_outcome(
        schema_name=schema_name,
        payment_intent_id=payment_intent_id,
        new_status=new_status,
        status_detail=status_detail,
        acting_user_id=acting_user_id,
        fee_cents=fee_cents,
        net_cents=net_cents,
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
