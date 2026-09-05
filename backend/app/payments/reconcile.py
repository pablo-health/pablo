# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reconciling processor events against the charge ledger.

The machinery behind ``app.routes.payment_webhooks``: signature verification,
working out which practice schema an event belongs to, the guarded ledger
update, and the processed-event ledger that stops a redelivery re-applying
something already applied.

It lives beside the route rather than inside it for two reasons — the tenant
update is raw SQL, which route handlers do not do, and the outcome vocabulary
below is what the route's one interesting decision turns on.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, NamedTuple

from sqlalchemy import text

from ..db import (
    DEFAULT_PRACTICE_SCHEMA,
    PLATFORM_SCHEMA,
    _validate_schema_name,
    create_standalone_session,
    get_engine,
)
from ..db.platform_models import PracticeRow, ProcessedPaymentEventRow
from ..settings import get_settings

if TYPE_CHECKING:
    from ..settings import Settings

logger = logging.getLogger(__name__)

#: How far out of date a signature timestamp may be. Stripe's own libraries
#: default to five minutes; beyond that a delivery is a replay, not a retry.
SIGNATURE_TOLERANCE_SECONDS = 300

#: Metadata keys stamped on every PaymentIntent this application creates.
#: All three are opaque ids — nothing here names a client or carries clinical
#: content, and nothing outside this deployment can resolve them to anything.
METADATA_CHARGE_ID = "pablo_charge_id"
METADATA_USER_ID = "pablo_user_id"
METADATA_PRACTICE_ID = "pablo_practice_id"

#: Which prior statuses a given transition may overwrite. Stops a delayed or
#: out-of-order delivery regressing a row: a late failure must not un-succeed a
#: charge, and nothing may un-refund one.
_ALLOWED_PRIOR_STATUS: dict[str, tuple[str, ...]] = {
    "succeeded": ("pending", "failed"),
    "failed": ("pending",),
    "refunded": ("succeeded", "failed", "refunded"),
}


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def _parse_signature_header(header: str) -> tuple[int | None, list[str]]:
    """Split ``t=…,v1=…,v1=…`` into ``(timestamp, [signatures])``.

    Returns ``(None, [])`` for anything unparseable, which the caller treats as
    an unverified request.
    """
    timestamp: int | None = None
    signatures: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return None, []
        elif key == "v1":
            signatures.append(value)
    return timestamp, signatures


def _candidate_secrets(settings: Settings) -> list[str]:
    """The current and previous signing secrets, whichever are configured.

    Empty when neither is set, which rejects every request — the right failure
    mode for a deployment that has not configured the endpoint.
    """
    secrets = [
        settings.stripe_patient_billing_webhook_secret.get_secret_value(),
        settings.stripe_patient_billing_webhook_secret_previous.get_secret_value(),
    ]
    return [secret for secret in secrets if secret]


def verify_signature(
    payload: bytes,
    header: str,
    settings: Settings,
    *,
    now: float | None = None,
) -> bool:
    """Constant-time check of Stripe's ``Stripe-Signature`` over the raw body.

    The signed payload is ``"{t}.{raw_body}"`` under HMAC-SHA256. A plain
    ``==`` here would be a timing side-channel, so the comparison goes through
    :func:`hmac.compare_digest`.

    Every ``v1`` scheme in the header is checked against every configured
    secret, so a rotation window never drops a delivery. Returns ``False`` on a
    missing or malformed header, an out-of-tolerance timestamp, no configured
    secret, or no match.
    """
    if not header:
        return False

    timestamp, signatures = _parse_signature_header(header)
    if timestamp is None or not signatures:
        return False

    current = time.time() if now is None else now
    if abs(current - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        return False

    secrets = _candidate_secrets(settings)
    if not secrets:
        return False

    signed_payload = str(timestamp).encode() + b"." + payload
    for secret in secrets:
        expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
        for candidate in signatures:
            if hmac.compare_digest(expected, candidate):
                return True
    return False


# ---------------------------------------------------------------------------
# Reading the event
# ---------------------------------------------------------------------------


def extract_charge_fields(event_type: str, obj: dict[str, Any]) -> tuple[str, str | None, str]:
    """Return ``(new_status, status_detail, payment_intent_id)`` for an event.

    ``payment_intent.*`` events carry the PaymentIntent itself; a
    ``charge.refunded`` event carries a Charge, whose ``payment_intent`` field
    points back at the object the ledger row is keyed on.
    """
    if event_type == "charge.refunded":
        # Partial and full refunds both land here. The ledger records that a
        # refund happened and leaves the amounts to the processor, which is
        # where a practice issues and reads them.
        return "refunded", None, str(obj.get("payment_intent") or "")

    if event_type == "payment_intent.succeeded":
        return "succeeded", None, str(obj.get("id") or "")

    error = obj.get("last_payment_error") or {}
    detail = error.get("decline_code") or error.get("code")
    return "failed", (str(detail) if detail else None), str(obj.get("id") or "")


# ---------------------------------------------------------------------------
# Tenant resolution
# ---------------------------------------------------------------------------


def resolve_practice_schema(practice_id: str | None) -> tuple[str | None, str] | None:
    """Map the event's practice id to ``(practice_id, schema_name)``.

    The charge route stamps its own practice id onto every PaymentIntent it
    creates, and Stripe hands it back on the event. That is the only usable
    answer here: the endpoint is reachable by anyone, the event body is the
    only thing that says which practice a payment belongs to, and a signature
    over the whole body is what makes it trustworthy. The id itself is an
    internal uuid — it names no person and resolves to nothing outside this
    deployment — so putting it in processor metadata discloses nothing.

    A deployment running a single practice has no practice registry to key on
    and stamps no id; those events resolve to the default schema.

    ``None`` means the event cannot be placed: it named a practice this
    deployment does not have (or has deactivated). The caller acknowledges it
    rather than making the processor retry something no retry can fix.
    """
    if practice_id is None:
        if get_settings().multi_tenancy_enabled:
            return None
        return None, DEFAULT_PRACTICE_SCHEMA

    with create_standalone_session() as db:
        practice = db.get(PracticeRow, practice_id)
        if practice is None or not practice.is_active or practice.deleted_at is not None:
            return None
        return practice.id, practice.schema_name


# ---------------------------------------------------------------------------
# Applying the outcome
# ---------------------------------------------------------------------------


class ChargeOutcome(StrEnum):
    """Why the ledger write ended where it did — the input to the dedupe call.

    A bare row count cannot carry this distinction, and the distinction is the
    whole point: three of these mean "handled, stop retrying" and one means "we
    failed, please redeliver".
    """

    #: The ledger row moved. Handled.
    APPLIED = "applied"
    #: A row exists, but the transition was refused by the status guard — a
    #: late duplicate of an already-applied event, or an out-of-order delivery.
    #: Correct behaviour, not a failure: handled.
    STALE = "stale"
    #: Not one of this application's charges at all. The practice took this
    #: money some other way through its own Stripe account — a dashboard
    #: charge, a payment link, an invoice — so there is no ledger row and never
    #: was one. Ordinary traffic, and handled.
    FOREIGN = "foreign"
    #: The event carried our metadata and no ledger row matched. A genuine
    #: anomaly, not routine traffic: alert and ask for a redelivery.
    NOT_FOUND = "not_found"


class ChargeApply(NamedTuple):
    """The ledger write's result: what happened, and whether the row we touched
    was created by the clinician the event named."""

    outcome: ChargeOutcome
    #: The row's real ``created_by_user_id`` when it differs from the id we
    #: armed with; ``None`` when they agree or nothing was written. Never a
    #: reason to withhold the write — see :func:`apply_charge_outcome`.
    attributed_to: str | None = None


def apply_charge_outcome(
    *,
    schema_name: str,
    payment_intent_id: str,
    new_status: str,
    status_detail: str | None,
    acting_user_id: str,
) -> ChargeApply:
    """Update the practice's ledger row for ``payment_intent_id``.

    The schema is bound explicitly, since there is no request and therefore no
    middleware to do it, and ``app.current_user_id`` is armed from the event's
    metadata so the row's access policy admits the write.

    **The event-supplied user id is a lookup key that gets verified against our
    own row, never an authority.** It cannot select which row is touched: the
    row is pinned by the PaymentIntent id the processor signed, inside a schema
    resolved from the practice id the processor signed. What the armed id *can*
    do is fail to satisfy the row policy, in which case nothing is visible and
    the outcome is ``NOT_FOUND``. So the UPDATE returns ``created_by_user_id``
    and the caller compares it against what was armed: on a mismatch the write
    still stands and the event is still recorded — the ledger outcome is
    correct either way and a retry would change nothing — but it is logged as
    something to alert on.

    That check costs nothing today, because this path writes no audit rows and
    nothing stamps an actor from session state. It exists because the
    route-audit guardrail pushes anything touching a client's chart toward
    mandatory auditing, and a future audit write here would otherwise silently
    attribute the action to whatever the event claimed. This makes that change
    safe by construction rather than by luck.

    The current status is read ``FOR UPDATE`` before writing rather than
    inferring everything from the UPDATE's row count. Zero rows updated has two
    completely different meanings — "there is no such row" (an anomaly, since
    the event carried our metadata) and "the status guard refused a stale
    transition" (correct, and must not be retried) — and a row count cannot
    tell them apart. ``FOR UPDATE`` makes the check-then-act atomic, so a
    concurrent redelivery cannot slip between the two statements.
    """
    _validate_schema_name(schema_name)
    engine = get_engine()
    allowed = _ALLOWED_PRIOR_STATUS[new_status]

    with engine.begin() as conn:
        # Signature-verified callback: schema validated by
        # _validate_schema_name(), never taken from the request path.
        # nosemgrep
        conn.execute(text(f"SET search_path = {schema_name}, {PLATFORM_SCHEMA}, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": acting_user_id},
        )
        current = conn.execute(
            text(
                """
                SELECT status FROM patient_charges
                 WHERE stripe_payment_intent_id = :pi
                   FOR UPDATE
                """
            ),
            {"pi": payment_intent_id},
        ).first()

        if current is None:
            return ChargeApply(ChargeOutcome.NOT_FOUND)
        if current[0] not in allowed:
            return ChargeApply(ChargeOutcome.STALE)

        updated = conn.execute(
            text(
                """
                UPDATE patient_charges
                   SET status = :new_status,
                       status_detail = :status_detail,
                       updated_at = :now
                 WHERE stripe_payment_intent_id = :pi
                   AND status = ANY(:allowed)
             RETURNING created_by_user_id
                """
            ),
            {
                "new_status": new_status,
                "status_detail": status_detail,
                "now": datetime.now(UTC),
                "pi": payment_intent_id,
                "allowed": list(allowed),
            },
        ).first()

        # The row was there and the guard admitted it a statement ago, under
        # FOR UPDATE — so nothing coming back is not a stale transition, it is
        # an anomaly. Fail toward a retry rather than record it as handled.
        if updated is None:
            return ChargeApply(ChargeOutcome.NOT_FOUND)

        owner = str(updated[0])
        mismatch = owner if owner != acting_user_id else None
        return ChargeApply(ChargeOutcome.APPLIED, mismatch)


# ---------------------------------------------------------------------------
# The processed-event ledger
# ---------------------------------------------------------------------------


def event_already_processed(event_id: str) -> bool:
    """Has this event already been handled?"""
    with create_standalone_session() as db:
        return db.get(ProcessedPaymentEventRow, event_id) is not None


def record_processed_event(
    *, event_id: str, event_type: str, practice_id: str | None, created: object
) -> None:
    """Mark an event handled so redeliveries short-circuit.

    Call this only once the event has genuinely been dealt with — applied, or
    deliberately determined to be none of this application's business.
    Recording an event we failed to reconcile spends the processor's redelivery
    on nothing and strands the ledger row.
    """
    with create_standalone_session() as db:
        db.add(
            ProcessedPaymentEventRow(
                event_id=event_id,
                event_type=event_type,
                practice_id=practice_id,
                event_created_at=(
                    datetime.fromtimestamp(created, tz=UTC) if isinstance(created, int) else None
                ),
                processed_at=datetime.now(UTC),
            )
        )
        db.commit()


def log_unreconciled(
    *,
    event_id: str,
    event_type: str,
    payment_intent_id: str,
    reason: str,
) -> None:
    """Log a charge that could not be reconciled, loudly enough to alert on.

    A ledger row stuck at ``pending`` while the processor holds a completed
    payment is quiet by nature — nothing downstream notices, the practice
    simply believes it was not paid — so this line is the only signal. Alert on
    ``charge_unreconciled``.

    Processor ids and a fixed reason string only: no client identifier, no
    amount, no name.
    """
    logger.error(
        "charge_unreconciled: payment event matched no ledger row "
        "event=%s type=%s payment_intent=%s reason=%s",
        event_id,
        event_type,
        payment_intent_id,
        reason,
    )
