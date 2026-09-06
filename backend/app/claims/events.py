# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Claim lifecycle events and the listeners that act on them.

A rejection, a denial, a claim that has stalled at the payer, a filing or
appeal deadline, an enrollment the payer wants paperwork for — each is
something a person must act on. The code that discovers them (the outbox
worker, the acknowledgement poller, the remittance ingest) should not have
to know where that person looks. It calls :func:`emit` with a
:class:`ClaimEvent`; whatever is registered with
:func:`register_claim_event_listener` decides what to do with it. That lets
a deployment route claim events to its own surfaces — a ticketing system,
a chat channel, an on-call pager — without touching the claims workers.

Events are synchronous and run inside the caller's transaction. A listener
sees the same session that recorded the state change, so what it writes
commits or rolls back with that change. A listener that raises is logged
and skipped; it never prevents the next listener from running or the
caller's transaction from committing.

The one listener shipped here writes a compliance reminder, so out of the
box every actionable claim event lands on the clinician's compliance
dashboard next to their license renewal and CAQH attestation.

An event carries identifiers, codes and dates only. It never carries a
member id, a date of birth, a diagnosis code or a subscriber name, so a
listener can forward it to a surface that must not hold clinical detail.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Literal, get_args

from sqlalchemy import select

from ..db.models import ComplianceItemRow
from ..utcnow import utc_now

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

ClaimEventKind = Literal[
    "rejected",
    "denied",
    "partial",
    "stalled",
    "deadline_approaching",
    "deadline_missed",
    "enrollment_action_required",
    "unmatched_remittance",
    "paid",
]

CodeSystem = Literal["carc", "rarc", "edit", "status"]
"""Where a code came from: a claim adjustment reason (CARC), a remittance
advice remark (RARC), a clearinghouse edit, or a claim status category."""

DeadlineKind = Literal["filing", "correction", "appeal"]


@dataclass(frozen=True)
class CodeRef:
    """One code the payer or clearinghouse attached to the claim."""

    system: CodeSystem
    code: str
    description: str | None = None


@dataclass(frozen=True)
class ClaimEventDetail:
    """What a listener may show a person about the event.

    Codes and dates only. ``payer_instructions`` is set on enrollment
    events, where the payer's own wording of what it needs is the whole
    point; ``amount_cents`` on paid and partial events.
    """

    codes: tuple[CodeRef, ...] = ()
    deadline_kind: DeadlineKind | None = None
    deadline_date: date | None = None
    days_left: int | None = None
    payer_instructions: str | None = None
    amount_cents: int | None = None


@dataclass(frozen=True)
class ClaimEvent:
    """Something happened to a claim that a person may need to act on.

    ``control_number`` is the patient control number the claim was filed
    under and is how a person finds the claim in the clearinghouse portal.
    ``user_id`` is the clinician the claim belongs to; reminders and any
    other per-person surface are addressed to them. ``state`` is the
    claim's state after this event, in the claims pipeline's own words.
    """

    kind: ClaimEventKind
    control_number: str
    claim_id: str
    user_id: str
    payer_id: str | None
    payer_name: str | None
    state: str
    occurred_at: datetime
    detail: ClaimEventDetail = field(default_factory=ClaimEventDetail)

    def to_dict(self) -> dict[str, object]:
        """A JSON-ready view of the event, for tests and audit records."""
        detail = self.detail
        return {
            "kind": self.kind,
            "control_number": self.control_number,
            "claim_id": self.claim_id,
            "user_id": self.user_id,
            "payer_id": self.payer_id,
            "payer_name": self.payer_name,
            "state": self.state,
            "occurred_at": self.occurred_at.isoformat(),
            "detail": {
                "codes": [
                    {"system": c.system, "code": c.code, "description": c.description}
                    for c in detail.codes
                ],
                "deadline_kind": detail.deadline_kind,
                "deadline_date": (
                    detail.deadline_date.isoformat() if detail.deadline_date else None
                ),
                "days_left": detail.days_left,
                "payer_instructions": detail.payer_instructions,
                "amount_cents": detail.amount_cents,
            },
        }


ClaimEventListener = Callable[["Session", ClaimEvent], None]

_listeners: list[ClaimEventListener] = []


def register_claim_event_listener(listener: ClaimEventListener) -> None:
    """Add a listener that :func:`emit` will call, in registration order.

    Call once during startup. Registering the same listener twice invokes
    it twice, the same way ``register_post_provision_hook`` behaves; the
    caller guards against re-registration on hot reload.
    """
    _listeners.append(listener)


def clear_claim_event_listeners() -> None:
    """Remove every listener, the default one included. For tests."""
    _listeners.clear()


def emit(session: Session, event: ClaimEvent) -> None:
    """Hand ``event`` to every registered listener inside the caller's transaction.

    Safe to call with no listeners registered. A listener that raises is
    logged at WARNING with its name, the event kind and the control
    number, then skipped; the remaining listeners still run and the
    caller's transaction is untouched.
    """
    for listener in list(_listeners):
        try:
            listener(session, event)
        except Exception as exc:  # a listener must never take the caller down
            logger.warning(
                "claim_event_listener_failed listener=%s error=%s kind=%s control_number=%s",
                getattr(listener, "__qualname__", repr(listener)),
                type(exc).__name__,
                event.kind,
                event.control_number,
            )


# --- Default listener: a compliance reminder ---------------------------------

_REMINDER_DUE_IN = timedelta(days=7)
"""How long a person gets to act when the event carries no deadline of its own."""

_CONTROL_NUMBER_LABEL_CHARS = 8

_KIND_PHRASES: dict[ClaimEventKind, str] = {
    "rejected": "rejected by {payer}",
    "denied": "denied by {payer}",
    "partial": "partially paid by {payer}",
    "stalled": "stalled at {payer}",
    "deadline_approaching": "{deadline} deadline with {payer}",
    "deadline_missed": "{deadline} deadline missed with {payer}",
    "enrollment_action_required": "enrollment action needed for {payer}",
    "unmatched_remittance": "unmatched remittance from {payer}",
    "paid": "paid by {payer}",
}


def compliance_item_type(kind: ClaimEventKind) -> str:
    """The ``compliance_items.item_type`` a claim event of ``kind`` is filed under."""
    return f"claim_{kind}"


def _control_number_marker(control_number: str) -> str:
    return f"Claim control number: {control_number}"


def _label(event: ClaimEvent) -> str:
    detail = event.detail
    phrase = _KIND_PHRASES[event.kind].format(
        payer=event.payer_name or "payer",
        deadline=detail.deadline_kind or "claim",
    )
    label = f"Claim {event.control_number[:_CONTROL_NUMBER_LABEL_CHARS]} {phrase}"
    if detail.deadline_date is not None:
        label += f", by {detail.deadline_date.isoformat()}"
    return label


def _notes(event: ClaimEvent) -> str:
    detail = event.detail
    lines = [_control_number_marker(event.control_number)]
    descriptions = [
        code.description or f"{code.system.upper()} {code.code}" for code in detail.codes
    ]
    if descriptions:
        lines.append("; ".join(descriptions))
    if detail.payer_instructions:
        lines.append(detail.payer_instructions)
    return "\n".join(lines)


def _existing_reminder(session: Session, event: ClaimEvent) -> ComplianceItemRow | None:
    """The reminder already written for this (kind, control number), if any.

    The control number is kept on the first line of ``notes``; there is no
    column for it because the reminder is the only place it is needed.
    """
    return session.execute(
        select(ComplianceItemRow)
        .where(ComplianceItemRow.user_id == event.user_id)
        .where(ComplianceItemRow.item_type == compliance_item_type(event.kind))
        .where(
            ComplianceItemRow.notes.startswith(
                _control_number_marker(event.control_number), autoescape=True
            )
        )
        .limit(1)
    ).scalar_one_or_none()


def compliance_reminder_listener(session: Session, event: ClaimEvent) -> None:
    """Write one compliance reminder per actionable event.

    A paid claim needs nothing from anyone, so it writes no reminder.
    Everything else gets exactly one row per (kind, control number): a
    second emit of the same event, as happens when a poller sees the same
    acknowledgement twice, finds the existing row and leaves it alone.
    """
    if event.kind == "paid":
        return
    if _existing_reminder(session, event) is not None:
        return
    detail = event.detail
    now = utc_now()
    due_date = detail.deadline_date or (event.occurred_at + _REMINDER_DUE_IN).date()
    session.add(
        ComplianceItemRow(
            id=str(uuid.uuid4()),
            user_id=event.user_id,
            item_type=compliance_item_type(event.kind),
            label=_label(event),
            due_date=due_date,
            notes=_notes(event),
            completed_at=None,
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()


def resolve_compliance_reminder(
    session: Session, *, kind: ClaimEventKind, control_number: str, user_id: str
) -> bool:
    """Mark the default listener's reminder for ``(kind, control number)`` done.

    The counterpart of :func:`compliance_reminder_listener` for the events
    that stop needing a person once the world moves on — an enrollment the
    payer wanted paperwork for that has since gone live. Returns whether a
    reminder was open to resolve; a reminder that is already complete, or
    was never written (a deployment that routes events elsewhere), is left
    alone.
    """
    row = _existing_reminder(
        session,
        ClaimEvent(
            kind=kind,
            control_number=control_number,
            claim_id="",
            user_id=user_id,
            payer_id=None,
            payer_name=None,
            state="",
            occurred_at=utc_now(),
        ),
    )
    if row is None or row.completed_at is not None:
        return False
    now = utc_now()
    row.completed_at = now
    row.updated_at = now
    session.flush()
    return True


COMPLIANCE_ITEM_TYPES: tuple[str, ...] = tuple(
    compliance_item_type(kind) for kind in get_args(ClaimEventKind) if kind != "paid"
)
"""Every ``item_type`` the default listener can write. The compliance
template catalog carries one template per entry so the compliance routes
accept and list them."""

register_claim_event_listener(compliance_reminder_listener)
