# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice an account belongs to (``platform.practice_members``).

A signed-in request resolves its practice from the account first and from the
token's email second (``app.auth.service._resolve_practice``). Two keys
deciding one thing can disagree: an account whose address changed after its
first sign-in would be routed by the old address, and a clinician who joined
under a different address than the one they were mapped at would have no way
in. So the email mapping decides only once. The first sign-in it resolves
writes the account's row here, and from then on the row decides.

Three writers, one row per account:

* :func:`claim` on a first sign-in through the email mapping;
* an invitation redeemed (not here yet);
* :func:`backfill`, run by the migrate step for accounts that were mapped
  before this table existed.

None of them overwrites a row that exists. Moving an account between practices
is a deliberate act, not a side effect of signing in.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from ..utcnow import utc_now
from .platform_models import (
    EmailTenantMappingRow,
    PlatformUserRow,
    PracticeMemberRow,
    PracticeRow,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

#: ``event`` on the log line written when an account's membership and its
#: email mapping name different practices. Membership wins; the line exists so
#: the disagreement is visible. Ids only.
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
    """An account's membership, with what its address maps to beside it."""

    practice_id: str
    schema_name: str
    practice_active: bool
    #: The practice the email mapping names for the address given, or ``None``.
    mapped_practice_id: str | None


def lookup(user_id: str, email: str | None) -> Membership | None:
    """The account's membership, or ``None`` when it has none.

    One statement: the membership, its practice, and (for the disagreement
    check) whatever the address maps to.
    """
    from . import create_standalone_session

    stmt = (
        select(
            PracticeMemberRow.practice_id,
            PracticeRow.schema_name,
            PracticeRow.is_active,
            EmailTenantMappingRow.practice_id,
        )
        .join(PracticeRow, PracticeRow.id == PracticeMemberRow.practice_id)
        .outerjoin(EmailTenantMappingRow, EmailTenantMappingRow.email == (email or ""))
        .where(PracticeMemberRow.user_id == user_id)
    )
    with create_standalone_session() as db:
        row = db.execute(stmt).first()
    if row is None:
        return None
    practice_id, schema_name, is_active, mapped_practice_id = row
    return Membership(
        practice_id=practice_id,
        schema_name=schema_name,
        practice_active=bool(is_active),
        mapped_practice_id=mapped_practice_id,
    )


def claim(user_id: str, practice_id: str) -> bool:
    """Record the practice an account's first sign-in resolved to.

    Never overwrites: an account that already has a membership keeps it, and
    two first requests racing each other write one row. Returns whether this
    call wrote it.

    Opens its own session, like ``record_owner_on_sign_in``: the registry is not
    the request's schema, and the claim should land whether or not the request
    goes on to succeed. A failure is logged and swallowed. The request still
    resolves through the mapping, and the next one tries again.
    """
    from . import create_standalone_session

    if not is_account_id(user_id):
        return False
    stmt = (
        insert(PracticeMemberRow)
        .values(
            user_id=user_id,
            practice_id=practice_id,
            joined_at=utc_now(),
            joined_via="email_claim",
        )
        .on_conflict_do_nothing(index_elements=[PracticeMemberRow.user_id])
    )
    try:
        with create_standalone_session() as db:
            result = db.connection().execute(stmt)
            db.commit()
    except Exception as exc:
        # Type only: a constraint message can carry row values.
        logger.warning(
            "practice_members: first-sign-in claim failed (%s); resolving by mapping",
            type(exc).__name__,
        )
        return False
    claimed = bool(result.rowcount)
    if claimed:
        logger.info(
            "practice_members: account %s claimed practice %s on first sign-in",
            user_id,
            practice_id,
        )
    return claimed


@dataclass(frozen=True)
class BackfillReport:
    inserted: int
    #: Accounts whose address maps to a practice that exists and which still
    #: have no membership. Zero after a successful backfill. (A mapping to a
    #: practice that no longer exists resolves to nothing either way, so it is
    #: not counted.)
    unmembered: int


_BACKFILL_SQL = text(
    """
    INSERT INTO platform.practice_members (user_id, practice_id, joined_at, joined_via)
    SELECT u.id, m.practice_id, now(), 'backfill'
    FROM platform.users u
    JOIN platform.email_tenant_mappings m ON m.email = lower(u.email)
    JOIN platform.practices p ON p.id = m.practice_id
    ON CONFLICT (user_id) DO NOTHING
    """
)


def _unmembered_count(engine: Engine) -> int:
    stmt = (
        select(func.count())
        .select_from(PlatformUserRow)
        .join(
            EmailTenantMappingRow, EmailTenantMappingRow.email == func.lower(PlatformUserRow.email)
        )
        .join(PracticeRow, PracticeRow.id == EmailTenantMappingRow.practice_id)
        .outerjoin(PracticeMemberRow, PracticeMemberRow.user_id == PlatformUserRow.id)
        .where(PracticeMemberRow.user_id.is_(None))
    )
    with engine.connect() as conn:
        return int(conn.execute(stmt).scalar_one())


def backfill(engine: Engine) -> BackfillReport:
    """Give every mapped account that has no membership the practice it maps to.

    Idempotent: an account with a membership is left alone, so a second run
    inserts nothing. Run by the migrate step after every upgrade.
    """
    with engine.begin() as conn:
        inserted = conn.execute(_BACKFILL_SQL).rowcount or 0
    return BackfillReport(inserted=inserted, unmembered=_unmembered_count(engine))
