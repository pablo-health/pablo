# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice an account belongs to (``platform.practice_members``).

Recorded, not yet routed by. A signed-in request resolves its practice through
the email mapping (``app.auth.service._resolve_practice``); this table is
written beside that answer and compared with it, so a wrong row costs nothing
while the comparison shows whether the two ever disagree.

Writers, one row per account, none of which overwrites a row that exists:

* :func:`claim`, on a request that resolved through the mapping with an
  address its sign-in token verified, for an account with no row yet;
* :func:`backfill`, run by the migrate step for accounts that were mapped
  before this table existed;
* an invitation redeemed (not written anywhere yet).

And one remover: :func:`remove_for_practice`, for whatever deactivates a
practice, so a membership does not outlive it.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: ``event`` on the log line written when an account's membership names a
#: different practice than its sign-in address maps to. Ids only.
MEMBERSHIP_DISAGREES_EVENT = "practice_membership_disagrees"


def is_account_id(user_id: str | None) -> bool:
    """Whether ``user_id`` can be an account id at all.

    An account with no identity row yet is known only by its provider's subject
    id, which some paths hand on in place of an account id. ``users.id`` is a
    uuid column, so such a value would make the statement fail rather than
    match nothing. It identifies no account, so nothing is read or written for
    it (the same rule ``app.db.practice_owner`` applies).
    """
    if not user_id:
        return False
    try:
        uuid.UUID(user_id)
    except ValueError:
        return False
    return True


_MEMBER_PRACTICE_SQL = text(
    "SELECT practice_id FROM platform.practice_members WHERE user_id = :user_id"
)


def member_practice_id(user_id: str) -> str | None:
    """The practice the account is a member of, or ``None`` when it has no row."""
    from . import create_standalone_session

    with create_standalone_session() as db:
        value = db.execute(_MEMBER_PRACTICE_SQL, {"user_id": user_id}).scalar_one_or_none()
    return None if value is None else str(value)


# One statement, so the grant is checked at the moment of writing rather than
# when the request read it: a revoke that commits between the two leaves no
# mapping row, and nothing is inserted. The practice must still be active for
# the same reason a deactivation removes its rows (``remove_for_practice``).
_CLAIM_SQL = text(
    """
    INSERT INTO platform.practice_members
        (user_id, practice_id, joined_at, joined_via, invited_by, claimed_email)
    SELECT CAST(:user_id AS uuid), m.practice_id, now(), 'email_claim', NULL, m.email
    FROM platform.email_tenant_mappings m
    JOIN platform.practices p ON p.id = m.practice_id AND p.is_active
    WHERE m.email = :email AND m.practice_id = :practice_id
    ON CONFLICT (user_id) DO NOTHING
    """
)


def claim(user_id: str, practice_id: str, email: str) -> bool:
    """Record the practice an account's request resolved to by its address.

    ``email`` must be the address the request's sign-in token verified, never
    the one stored on the account: the stored address is written once and not
    refreshed, so it can name a practice the account was never granted.

    Writes only while ``email`` is still mapped to ``practice_id``, and never
    overwrites: an account that already has a membership keeps it, and two
    first requests racing each other write one row. Returns whether this call
    wrote it.

    Opens its own session, like ``record_owner_on_sign_in``: the registry is not
    the request's schema, and the row should land whether or not the request
    goes on to succeed. A failure is logged by type and swallowed; the request
    resolves the same either way, and the next one tries again.
    """
    from . import create_standalone_session

    if not is_account_id(user_id) or not email:
        return False
    params = {"user_id": user_id, "practice_id": practice_id, "email": email.lower()}
    try:
        with create_standalone_session() as db:
            result = db.execute(_CLAIM_SQL, params)
            db.commit()
    except Exception as exc:
        # Type only: a constraint message can carry row values.
        logger.warning(
            "practice_members: claim failed (%s); routing is unaffected",
            type(exc).__name__,
        )
        return False
    claimed = bool(getattr(result, "rowcount", 0))
    if claimed:
        logger.info(
            "practice_members: account %s claimed practice %s",
            user_id,
            practice_id,
            extra={"user_id": user_id, "practice_id": practice_id},
        )
    return claimed


def remove_for_practice(db: Session | Connection, practice_id: str) -> int:
    """Delete every membership in ``practice_id``. Returns how many rows went.

    Called by whatever sets ``practices.is_active`` false, on the same session
    or connection, so the rows go in the same transaction as the deactivation.
    A membership must not outlive its practice: once memberships decide where
    an account signs in, one left behind would hold the account to a practice
    that no longer serves it. A later sign-in through a live mapping claims
    again.
    """
    result = db.execute(
        text("DELETE FROM platform.practice_members WHERE practice_id = :practice_id"),
        {"practice_id": practice_id},
    )
    return int(getattr(result, "rowcount", 0) or 0)


@dataclass(frozen=True)
class BackfillReport:
    inserted: int
    #: Accounts whose address maps to an active practice and which still have
    #: no membership. Zero after a successful backfill.
    unmembered: int


# Reads the address stored on the account, which is not refreshed after it is
# created. That is acceptable only while memberships are recorded and not
# routed by: a row written from a stale address shows up as a disagreement on
# the account's next sign-in (``MEMBERSHIP_DISAGREES_EVENT``) before anything
# depends on it.
_BACKFILL_SQL = text(
    """
    INSERT INTO platform.practice_members
        (user_id, practice_id, joined_at, joined_via, invited_by, claimed_email)
    SELECT u.id, m.practice_id, now(), 'backfill', NULL, m.email
    FROM platform.users u
    JOIN platform.email_tenant_mappings m ON m.email = lower(u.email)
    JOIN platform.practices p ON p.id = m.practice_id AND p.is_active
    ON CONFLICT (user_id) DO NOTHING
    """
)

_UNMEMBERED_SQL = text(
    """
    SELECT count(*)
    FROM platform.users u
    JOIN platform.email_tenant_mappings m ON m.email = lower(u.email)
    JOIN platform.practices p ON p.id = m.practice_id AND p.is_active
    LEFT JOIN platform.practice_members pm ON pm.user_id = u.id
    WHERE pm.user_id IS NULL
    """
)


def backfill(engine: Engine) -> BackfillReport:
    """Give every mapped account that has no membership the practice it maps to.

    Idempotent: an account with a membership is left alone, so a second run
    inserts nothing. Run by the migrate step after every upgrade.
    """
    with engine.begin() as conn:
        inserted = conn.execute(_BACKFILL_SQL).rowcount or 0
        unmembered = int(conn.execute(_UNMEMBERED_SQL).scalar_one())
    return BackfillReport(inserted=inserted, unmembered=unmembered)
