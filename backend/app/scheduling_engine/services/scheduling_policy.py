# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading and writing the practice's scheduling policy.

The policy answers what an appointment type does not: how late a patient may
cancel, how a new enquiry starts, whether patients may book at all. A type says
what an appointment IS; this says what the practice will allow to happen to its
calendar.

A practice that has never opened the settings has no row. That is not an error
and must not be treated as one — it means "nothing configured", and the honest
answer to "may a patient book?" in that state is no. So the defaults here are
uniformly off or strict, and are returned rather than written: reading a policy
never creates one.

Storing policy is all this does. Enforcing it when something is actually booked
is separate and not yet built, so do not read a call to ``load_policy`` as
proof that a rule is being applied anywhere.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ...db.models import SchedulingPolicyRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: The singleton row's fixed primary key.
SINGLETON_ID = 1

#: What a practice gets before it has configured anything.
#:
#: Every gate is off and every window is conservative. A practice upgrading
#: into this code must not discover that patients can suddenly book it, so the
#: safe reading of "unconfigured" is "not allowed", never "allowed by default".
DEFAULTS: dict[str, object] = {
    "min_notice_hours": 24,
    "max_horizon_days": 60,
    "cancel_cutoff_hours": 24,
    "reschedule_cutoff_hours": 24,
    "pending_hold_hours": 72,
    "self_book_existing": False,
    "self_book_new": False,
    "self_book_mode": "request",
    "new_patient_flow": "consult",
    "intake_forms_due_hours": 48,
}

#: Fields a caller may set. Anything else in a patch is ignored rather than
#: rejected, so a client sending a field this version does not know about does
#: not fail the whole save.
SETTABLE: frozenset[str] = frozenset(DEFAULTS)


def _to_dict(row: SchedulingPolicyRow) -> dict[str, object]:
    return {name: getattr(row, name) for name in SETTABLE}


def load_policy(session: Session) -> dict[str, object]:
    """The practice's stored policy, or the strict defaults when unset.

    Reads through the caller's already tenant-scoped session — this never
    names a schema and never filters by practice, because the search path has
    already decided which practice we are.

    Returns a deep copy of the defaults rather than the module-level dict
    itself. A shallow copy would let one caller's in-place edit leak into every
    other unconfigured practice's idea of "default", which is the kind of bug
    that only shows up under load.
    """
    row = session.get(SchedulingPolicyRow, SINGLETON_ID)
    return _to_dict(row) if row is not None else copy.deepcopy(DEFAULTS)


def update_policy(session: Session, patch: dict[str, object]) -> dict[str, object]:
    """Merge ``patch`` over the current policy and upsert the singleton row.

    Partial by design: a field the caller did not mention keeps its current
    value, so a settings page can save one row without resending the rest.
    Unknown keys are ignored. Does not commit — the caller owns the
    transaction.
    """
    merged = {**load_policy(session), **{k: v for k, v in patch.items() if k in SETTABLE}}
    now = datetime.now(UTC)

    row = session.get(SchedulingPolicyRow, SINGLETON_ID)
    if row is None:
        session.add(SchedulingPolicyRow(id=SINGLETON_ID, created_at=now, updated_at=now, **merged))
    else:
        for key, value in merged.items():
            setattr(row, key, value)
        row.updated_at = now

    session.flush()
    return merged


def may_self_book(policy: dict[str, object], *, is_new_patient: bool) -> bool:
    """Whether the practice allows self-booking for this kind of patient.

    Only half the answer. The appointment type must ALSO be marked
    ``self_bookable`` — this is the practice saying it allows self-booking at
    all, the type says whether that particular appointment is one of them.
    Both have to be true, and this function deliberately cannot see the type.
    """
    key = "self_book_new" if is_new_patient else "self_book_existing"
    return bool(policy.get(key, False))
