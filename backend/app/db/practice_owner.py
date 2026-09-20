# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""How a practice comes to know which account owns it.

``platform.practices.owner_user_id`` is the practice's principal: the
clinician whose diary a patient is offered a time in, and the identity
:func:`app.routes.patient_booking.owner_session` arms before it reads the
whole diary to work out what is free. Until the column is filled, every
self-booking route refuses, because a booking with no clinician to make it
with is not a booking.

Nothing used to fill it. A practice is registered before anyone has signed
in -- at boot for the deployment's own practice, or by whoever registers one
for somebody else -- so at the moment the row is written the owner exists as
an email address and nothing more. An account is attached to a practice
afterwards, and separately, through the email-to-practice mapping. The two
halves never met.

This module is where they meet, and it does the joining in one place so that
both callers agree about what "owner" means:

* :func:`record_owner_on_sign_in` runs on the authenticated path, once the
  caller's account and their practice are both known.
* :func:`reconcile_practice_owners` is the one-off for practices registered
  before any of this existed, run by the migrate step.

**The rule.** A practice records as its owner the account whose email is the
one the practice was registered under. A practice registered under no email
at all -- the deployment's own practice is, because at boot there is nobody
to name -- records the first account that signs into it.

**What the rule deliberately is not.** "The practice has one clinician, so
that is the owner" is the shortcut, and it is the same shortcut
``app.auth.service`` rejects one layer up when it resolves an account to a
practice: counting rows to decide an identity question gives a different
answer the day a second row appears, and gives it silently. So the owner is
read off something declared, never inferred from the population.

**Never overwritten.** Every write here is conditional on the column being
empty, in the statement rather than around it, so concurrent sign-ins settle
on one answer and a re-sign-in changes nothing. An owner who should change
is an operator action on a practice that already has one, not something a
login gets to do.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text

from . import PLATFORM_SCHEMA

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

#: Fill the owner from the account signing in. The predicate carries the whole
#: decision so the read and the write cannot disagree under a concurrent
#: sign-in: ``owner_user_id IS NULL`` makes it a one-time write, and the
#: email test makes it the right account rather than whoever arrived first.
#:
#: The second half of that test is the practice registered under no email.
#: ``''`` is what the boot path writes, having nobody to name yet, and it is
#: the only case where the first account through the door is the answer.
_RECORD_OWNER_SQL = text(
    f"UPDATE {PLATFORM_SCHEMA}.practices"  # noqa: S608 — module constant, no caller input
    " SET owner_user_id = CAST(:user_id AS uuid)"
    " WHERE id = :practice_id"
    " AND owner_user_id IS NULL"
    " AND deleted_at IS NULL"
    " AND (lower(btrim(owner_email)) = :email OR btrim(owner_email) = '')"
)

#: Fill the owner from the registry itself, for rows that predate the sign-in
#: path above. ``platform.users.email`` is unique, so the join names at most
#: one account and the statement needs no tie-break. A practice whose
#: registered email belongs to no account is left alone: there is nothing to
#: record, and inventing a principal for a diary is worse than refusing to
#: offer times in it.
_RECONCILE_SQL = text(
    f"UPDATE {PLATFORM_SCHEMA}.practices AS p"  # noqa: S608 — module constant, no caller input
    " SET owner_user_id = u.id"
    f" FROM {PLATFORM_SCHEMA}.users AS u"
    " WHERE p.owner_user_id IS NULL"
    " AND p.deleted_at IS NULL"
    " AND btrim(p.owner_email) <> ''"
    " AND lower(u.email) = lower(btrim(p.owner_email))"
)


def record_owner_on_sign_in(practice_id: str, email: str, user_id: str) -> bool:
    """Record *user_id* as the owner of *practice_id*, if it has none yet.

    Called from the authenticated path with the account that is signing in and
    the practice their email resolved to. Returns whether this call was the one
    that filled the column -- true at most once in a practice's life, false on
    every later sign-in and on every account that is not the owner.

    Cheap enough for a request path: one statement, matched on the primary key,
    that touches no rows in the ordinary case.

    Opens its own session rather than using the request's. The request session
    is inside the practice's schema with the caller's identity armed on it; the
    registry is neither, and this write has to land whether or not the
    surrounding request goes on to succeed.
    """
    from . import create_standalone_session

    normalized = email.strip().lower()
    if not normalized:
        return False

    with create_standalone_session() as db:
        # Through the session's connection rather than ``db.execute``, because
        # what this needs back is how many rows the statement matched, and only
        # the cursor-level result carries that.
        result = db.connection().execute(
            _RECORD_OWNER_SQL,
            {"practice_id": practice_id, "email": normalized, "user_id": user_id},
        )
        recorded = bool(result.rowcount)
        db.commit()

    if not recorded:
        return False

    # By practice, never by account: which practice learned something is
    # operational, who its owner is belongs to the registry and not to a log.
    logger.info("Practice '%s' recorded its owner on sign-in", practice_id)
    return True


def reconcile_practice_owners(engine: Engine) -> int:
    """Fill in owners for practices registered before sign-in recorded them.

    Run by the migrate step, where every schema change already happens: it has
    the database in front of it, its output in the log, and it runs once per
    deploy rather than on a request. Idempotent -- a second run matches nothing,
    because the first one filled every row it could.

    Returns how many practices learned their owner.
    """
    with engine.begin() as conn:
        filled = int(conn.execute(_RECONCILE_SQL).rowcount)

    # A count, and nothing else. Which practices they were, and whose accounts
    # they now name, is registry data with no business in a deploy log.
    if filled:
        logger.info("Recorded the owner of %d practice(s) from the registry.", filled)
    return filled
