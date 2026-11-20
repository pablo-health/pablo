# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice an account belongs to (``platform.practice_members``).

An account's membership decides which practice its signed-in requests reach
(``app.auth.service._resolve_practice``). The email mapping decides only for
an account with no membership yet: its first sign-in resolves through the
mapping and claims that practice here, and from then on the account decides.

Writers, one row per account, none of which overwrites a row that exists:

* :func:`claim`, on a request that resolved through the mapping with an
  address its sign-in token verified, for an account with no row yet;
* :func:`backfill`, run by the migrate step for accounts that were mapped
  before this table existed;
* an invitation redeemed (not written anywhere yet).

And two removers: :func:`remove_for_practice`, for whatever deactivates a
practice, so a membership does not outlive it; and revoking an allowlist grant
(``app.repositories.postgres.allowlist``), which removes the membership that
grant led to.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

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


@dataclass(frozen=True)
class Membership:
    """An account's membership, read with what resolution needs beside it."""

    practice_id: str
    schema_name: str
    practice_active: bool
    #: The practice the request's address maps to, if any. Read only to log a
    #: disagreement; it never changes the answer.
    mapped_practice_id: str | None


# One statement: the membership, its practice, and whatever the address maps
# to. The practice join is inner because the foreign key guarantees the row.
_LOOKUP_SQL = text(
    """
    SELECT pm.practice_id, p.schema_name, p.is_active, m.practice_id AS mapped_practice_id
    FROM platform.practice_members pm
    JOIN platform.practices p ON p.id = pm.practice_id
    LEFT JOIN platform.email_tenant_mappings m ON m.email = :email
    WHERE pm.user_id = CAST(:user_id AS uuid)
    """
)


def lookup(user_id: str, email: str | None) -> Membership | None:
    """The account's membership, or ``None`` when it has none.

    ``email`` is used only to report what the address maps to
    (``Membership.mapped_practice_id``).
    """
    from . import create_standalone_session

    with create_standalone_session() as db:
        row = db.execute(_LOOKUP_SQL, {"user_id": user_id, "email": email or ""}).first()
    if row is None:
        return None
    practice_id, schema_name, is_active, mapped_practice_id = row
    return Membership(
        practice_id=str(practice_id),
        schema_name=str(schema_name),
        practice_active=bool(is_active),
        mapped_practice_id=None if mapped_practice_id is None else str(mapped_practice_id),
    )


# One statement, so the grant is checked at the moment of writing rather than
# when the request read it: a revoke that commits between the two leaves no
# mapping row, and nothing is inserted. The practice must still be active for
# the same reason a deactivation removes its rows (``remove_for_practice``).
#
# ``FOR SHARE OF p`` makes that check wait for a deactivation in flight. A plain
# join reads ``is_active`` from the statement's snapshot, so a claim racing a
# deactivation that has run but not committed still sees the practice active,
# inserts at once, and leaves a membership in a practice that is now inactive
# (``remove_for_practice`` ran before the row existed). The share lock conflicts
# with the row lock the deactivation holds (``remove_for_practice`` takes it
# whether or not the caller's UPDATE has been flushed): the claim waits for it to commit, then reads
# the committed row, finds it inactive and inserts nothing. Held until the claim
# commits, on one practice row.
#
# ``OF m`` does the same for a revoke in flight. A revoke deletes the mapping and
# then the memberships its address claimed, in one transaction
# (``PostgresAllowlistRepository.remove``). Without a lock on the mapping row, a
# claim landing between the two reads the still-committed mapping, inserts, and
# leaves a membership the revoke never saw. The membership decides, so the
# revoked account would keep entering the practice. With the lock, the claim
# waits for the revoke to commit, rechecks, finds no mapping and inserts
# nothing. A claim that locks first makes the revoke's ``DELETE`` wait instead,
# and the revoke's membership read, a later statement, then sees the claimed
# row and removes it.
#
# The order ``OF m, p`` is deliberate: Postgres takes the row locks in the order
# the list names them. Mapping first means a claim that meets a deleted mapping
# waits on it while holding nothing, so a writer that deletes a practice's
# mappings and then updates the practice row (offboarding does) is only ever
# waited on. With ``OF p, m`` the claim would hold the practice row while it
# waits on the mapping, the writer's practice update would wait on the claim,
# and the two would deadlock.
_CLAIM_SQL = text(
    """
    INSERT INTO platform.practice_members
        (user_id, practice_id, joined_at, joined_via, invited_by, claimed_email)
    SELECT CAST(:user_id AS uuid), m.practice_id, now(), 'email_claim', NULL, m.email
    FROM platform.email_tenant_mappings m
    JOIN platform.practices p ON p.id = m.practice_id AND p.is_active
    WHERE m.email = :email AND m.practice_id = :practice_id
    FOR SHARE OF m, p
    ON CONFLICT (user_id) DO NOTHING
    """
)


#: The foreign key from ``practice_members.user_id`` to ``users.id``. A claim
#: that trips it names an account whose row is not committed yet: the request
#: that creates an account writes its ``users`` row on the request session, and
#: the claim runs on its own session, which cannot see that row until the
#: request commits.
_USER_FK = "practice_members_user_id_fkey"

#: ``Session.info`` key: the claims waiting for that session to commit.
_DEFERRED_CLAIMS_KEY = "practice_members_deferred_claims"
#: ``Session.info`` key: whether this session's commit listeners are installed.
_LISTENING_KEY = "practice_members_listening"


def claim(user_id: str, practice_id: str, email: str, *, may_defer: bool = True) -> bool:
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

    One failure is expected rather than logged as one: on the request that
    creates the account, its ``users`` row is not committed yet, so the insert
    trips the account foreign key. That claim is tried again once the request
    session commits (:func:`_claim_after_commit`), so the account's first
    request still records its membership. A request that rolls back created no
    account: a claim still queued tries again on the session's next commit, if
    any, finds no account, and stays quiet. ``may_defer`` is False for that
    second try, so it is never queued twice.
    """
    from . import create_standalone_session

    if not is_account_id(user_id) or not email:
        return False
    params = {"user_id": user_id, "practice_id": practice_id, "email": email.lower()}
    try:
        with create_standalone_session() as db:
            result = db.execute(_CLAIM_SQL, params)
            db.commit()
    except IntegrityError as exc:
        if _constraint_name(exc) != _USER_FK:
            _log_claim_failed(exc)
            return False
        deferred = may_defer and _claim_after_commit(user_id, practice_id, email)
        logger.debug(
            "practice_members: account %s is not committed yet; %s",
            user_id,
            "claiming when the request commits" if deferred else "the next request claims",
        )
        return False
    except Exception as exc:
        _log_claim_failed(exc)
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


def _log_claim_failed(exc: Exception) -> None:
    # Type only: a constraint message can carry row values.
    logger.warning(
        "practice_members: claim failed (%s); routing is unaffected",
        type(exc).__name__,
    )


def _constraint_name(exc: IntegrityError) -> str | None:
    """The constraint the database named, from the driver's diagnostics."""
    diag = getattr(exc.orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    return name if isinstance(name, str) else None


def _claim_after_commit(user_id: str, practice_id: str, email: str) -> bool:
    """Try the claim again once the request session commits. Whether it is queued.

    The account's ``users`` row can only be uncommitted on the request session:
    a session displaced from that role is committed when it is displaced
    (``publish_request_session``). Each claim is queued once per session, so
    the two claim sites a first request passes through retry it once.
    """
    from . import _request_session

    session = _request_session.get()
    if session is None:
        return False
    if not session.info.get(_LISTENING_KEY):
        # Installed once and left in place: a listener cannot be removed while
        # its own event is being dispatched, and a request's session is closed
        # with the request. It drains only what is queued.
        event.listen(session, "after_commit", _run_deferred_claims)
        session.info[_LISTENING_KEY] = True
    session.info.setdefault(_DEFERRED_CLAIMS_KEY, set()).add((user_id, practice_id, email))
    return True


def _run_deferred_claims(session: Session) -> None:
    pending: set[tuple[str, str, str]] = session.info.pop(_DEFERRED_CLAIMS_KEY, None) or set()
    for user_id, practice_id, email in sorted(pending):
        claim(user_id, practice_id, email, may_defer=False)


def remove_for_practice(db: Session | Connection, practice_id: str) -> int:
    """Delete every membership in ``practice_id``. Returns how many rows went.

    Called by whatever sets ``practices.is_active`` false, on the same session
    or connection, so the rows go in the same transaction as the deactivation.
    A membership must not outlive its practice: once memberships decide where
    an account signs in, one left behind would hold the account to a practice
    that no longer serves it. A later sign-in through a live mapping claims
    again.

    It locks the practice row before it deletes, so the order of the caller's
    ``UPDATE`` does not matter. An ORM deactivation (``row.is_active = False``)
    is not flushed by a ``text()`` statement and reaches the database only at
    commit, after this ``DELETE``; without the lock nothing would hold the row
    in between, and a claim would read the practice active and insert a
    membership the ``DELETE`` had already passed. With it, a claim that comes
    later waits on the lock (``_CLAIM_SQL``'s ``FOR SHARE OF p``), and a claim
    already in flight is waited for, so the ``DELETE``, a new statement, sees
    its row and removes it.
    """
    db.execute(
        text("SELECT 1 FROM platform.practices WHERE id = :practice_id FOR NO KEY UPDATE"),
        {"practice_id": practice_id},
    )
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
# created. It ran first while memberships were recorded and not yet routed by,
# so a row written from a stale address showed up as a disagreement
# (``MEMBERSHIP_DISAGREES_EVENT``) before anything depended on it. Now it only
# reaches accounts mapped since then that have not signed in.
#
# ``FOR SHARE OF m, p``, in that order, for the reasons ``_CLAIM_SQL`` takes it:
# a deactivation or a revoke committing during the backfill is waited for, not
# read past, and an offboarding that deletes mappings before it updates the
# practice cannot deadlock it.
_BACKFILL_SQL = text(
    """
    INSERT INTO platform.practice_members
        (user_id, practice_id, joined_at, joined_via, invited_by, claimed_email)
    SELECT u.id, m.practice_id, now(), 'backfill', NULL, m.email
    FROM platform.users u
    JOIN platform.email_tenant_mappings m ON m.email = lower(u.email)
    JOIN platform.practices p ON p.id = m.practice_id AND p.is_active
    FOR SHARE OF m, p
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
