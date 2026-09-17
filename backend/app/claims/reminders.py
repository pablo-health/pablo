# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading and completing the reminders a claim raises.

The writing half lives in :mod:`app.claims.events`, next to the listener that
does it. This is the half the claims surface calls.

**Why these are not compliance items.** They were, until 2026-09, and the
dashboard showed a rejection beside a licence renewal — good for a clinician,
wrong underneath. A compliance item is about HER: her licence, her attestation,
her training, recurring on a cadence, isolated by ``user_id``. A claim reminder
is about one patient's claim, ends when that claim moves on, and is isolated by
the claim's own ``has_patient_access`` policy.

Sharing one table cost a foreign key and a unique constraint. The dedupe key
was the claim's control number kept in the first line of ``notes`` — a field
the compliance route replaces wholesale on every edit — so a clinician tidying
her own note severed the link, and the pipeline filed a duplicate on that tick
and every tick after it.

Every read here returns the claim's control number beside the reminder, because
that is the handle a person uses to find the claim in a payer portal, and
looking it up separately is how a list becomes N+1 queries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from ..db.models import ClaimReminderRow, ClaimRow
from ..utcnow import utc_now

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def list_open(session: Session) -> list[tuple[ClaimReminderRow, str]]:
    """Every unfinished reminder this session can see, soonest deadline first.

    No ``user_id`` filter, and that is deliberate: the row is isolated by the
    claim's ``has_patient_access`` policy, so the session already sees exactly
    the reminders for claims it can open. Filtering by user on top would narrow
    it to whoever filed the claim and hide a rejection from the colleague
    covering her caseload — the opposite of what the policy says.

    Completed reminders are left out. A finished job is not work, and the
    record of it stays on the row for anyone who goes looking.

    NULL due dates sort last rather than first: a reminder with no deadline is
    not urgent, it is undated.
    """
    query = (
        select(ClaimReminderRow, ClaimRow.control_number)
        .join(ClaimRow, ClaimRow.id == ClaimReminderRow.claim_id)
        .where(ClaimReminderRow.completed_at.is_(None))
        .order_by(
            ClaimReminderRow.due_date.is_(None),
            ClaimReminderRow.due_date.asc(),
            ClaimReminderRow.created_at.asc(),
        )
    )
    return [(row, control_number) for row, control_number in session.execute(query).all()]


def get_with_control_number(
    session: Session, reminder_id: str
) -> tuple[ClaimReminderRow, str] | None:
    """One reminder and its claim's control number, or ``None``.

    Invisible and absent are the same answer on purpose: telling them apart
    would confirm that a claim exists which the caller may not open.
    """
    query = (
        select(ClaimReminderRow, ClaimRow.control_number)
        .join(ClaimRow, ClaimRow.id == ClaimReminderRow.claim_id)
        .where(ClaimReminderRow.id == reminder_id)
        .limit(1)
    )
    found = session.execute(query).one_or_none()
    if found is None:
        return None
    return (found[0], found[1])


def complete(session: Session, reminder: ClaimReminderRow) -> ClaimReminderRow:
    """Mark it done, keeping the first completion time if there was one."""
    if reminder.completed_at is None:
        now = utc_now()
        reminder.completed_at = now
        reminder.updated_at = now
        session.flush()
    return reminder
