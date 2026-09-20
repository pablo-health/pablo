# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Postgres-backed portal sign-in state.

The database half of :mod:`app.portal.store`'s in-memory implementation. Two
stores, both bound to an already-tenant-scoped ``Session``: the caller sets
``search_path`` from the signature-verified token's tenant claim before
constructing either, and neither ever names a schema itself.

**The tenant never comes out of a row.** ``InviteChallenge`` carries a
``tenant`` field, but these tables have no tenant column: the row lives in
the practice's schema and that IS the scope. So the tenant on a
reconstructed challenge comes from the store's own binding, which came from
a verified signature — a row can never claim to belong to a different
practice than the one it was read from.

Nothing here is logged. The hash, the jti and the patient id all stay inside
the process.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select, update

from ..db.models import PortalInviteChallengeRow, PortalSessionRow
from .store import InviteChallenge, PortalSessionRecord

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult
    from sqlalchemy.orm import Session


def _to_epoch(moment: datetime) -> int:
    """TIMESTAMPTZ to the unix seconds the pure core speaks in."""
    return int(moment.timestamp())


def _from_epoch(seconds: int) -> datetime:
    """Unix seconds to an aware UTC datetime for the TIMESTAMPTZ column."""
    return datetime.fromtimestamp(seconds, tz=UTC)


class DbPortalAuthStore:
    """:class:`~app.portal.store.PortalAuthStore` over the challenge table.

    Every method flushes rather than commits: an invitation is issued inside
    the clinician's request transaction, and a redemption's
    ``mark_consumed`` must land in the same transaction as the session row
    it authorizes. The route owns the commit.
    """

    def __init__(self, session: Session, *, tenant: str) -> None:
        self._session = session
        self._tenant = tenant

    def put_challenge(self, challenge: InviteChallenge) -> None:
        self._session.add(
            PortalInviteChallengeRow(
                jti=challenge.jti,
                patient_id=challenge.patient_id,
                otp_hash=challenge.otp_hash,
                created_at=datetime.now(tz=UTC),
                expires_at=_from_epoch(challenge.expires_at),
                attempts=challenge.attempts,
                consumed=challenge.consumed,
            )
        )
        self._session.flush()

    def get_challenge(self, jti: str) -> InviteChallenge | None:
        row = self._session.get(PortalInviteChallengeRow, jti)
        if row is None:
            return None
        return InviteChallenge(
            jti=row.jti,
            patient_id=row.patient_id,
            # From the binding, never from the row — see the module docstring.
            tenant=self._tenant,
            otp_hash=row.otp_hash,
            expires_at=_to_epoch(row.expires_at),
            attempts=row.attempts,
            consumed=row.consumed,
        )

    def increment_attempts(self, jti: str) -> int:
        """Bump the attempt counter; return the new total.

        Incremented in SQL rather than read-modify-write: two wrong codes
        arriving together would otherwise each read the same count and each
        write count+1, costing the attacker one attempt for two guesses.
        """
        new_total = self._session.execute(
            update(PortalInviteChallengeRow)
            .where(PortalInviteChallengeRow.jti == jti)
            .values(attempts=PortalInviteChallengeRow.attempts + 1)
            .returning(PortalInviteChallengeRow.attempts)
        ).scalar_one()
        self._session.flush()
        return int(new_total)

    def mark_consumed(self, jti: str) -> None:
        self._session.execute(
            update(PortalInviteChallengeRow)
            .where(PortalInviteChallengeRow.jti == jti)
            .values(consumed=True)
        )
        self._session.flush()

    def consume_outstanding(self, patient_id: str) -> int:
        """Burn every unconsumed challenge for one patient; return the count.

        The clinician kill switch's first half: revoking portal access while
        an invitation is in flight has to stop that invitation too, or the
        patient redeems it a minute later and is back in.
        """
        # cast: Session.execute is typed Result[Any]; an UPDATE returns a
        # CursorResult, which is what carries rowcount (same as
        # repositories/postgres/appointment.py).
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(PortalInviteChallengeRow)
                .where(
                    PortalInviteChallengeRow.patient_id == patient_id,
                    PortalInviteChallengeRow.consumed.is_(False),
                )
                .values(consumed=True)
            ),
        )
        self._session.flush()
        return result.rowcount or 0

    def has_outstanding(self, patient_id: str) -> bool:
        """Is there a live, unconsumed, unexpired invitation for this patient?"""
        now = datetime.now(tz=UTC)
        jti = self._session.execute(
            select(PortalInviteChallengeRow.jti).where(
                PortalInviteChallengeRow.patient_id == patient_id,
                PortalInviteChallengeRow.consumed.is_(False),
                PortalInviteChallengeRow.expires_at > now,
            )
        ).first()
        return jti is not None

    def has_unconsumed_challenge(self, patient_id: str) -> bool:
        """Is any invitation for this patient still unspent, expired or not?

        No expiry predicate, unlike :meth:`has_outstanding` — see the
        protocol for why the two questions differ.
        """
        jti = self._session.execute(
            select(PortalInviteChallengeRow.jti).where(
                PortalInviteChallengeRow.patient_id == patient_id,
                PortalInviteChallengeRow.consumed.is_(False),
            )
        ).first()
        return jti is not None


class DbPortalSessionStore:
    """The server-side revocation list over the session table.

    A session token's ``jti`` is only a credential while its row says so.
    Nothing here interprets a token — the caller verifies the signature
    first and brings the ``jti``.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(self, session: PortalSessionRecord) -> None:
        self._session.add(
            PortalSessionRow(
                jti=session.jti,
                patient_id=session.patient_id,
                issued_at=_from_epoch(session.issued_at),
                expires_at=_from_epoch(session.expires_at),
                chain_started_at=_from_epoch(session.chain_started_at),
                revoked_at=None if session.revoked_at is None else _from_epoch(session.revoked_at),
            )
        )
        self._session.flush()

    def get(self, jti: str) -> PortalSessionRecord | None:
        row = self._session.get(PortalSessionRow, jti)
        if row is None:
            return None
        return PortalSessionRecord(
            jti=row.jti,
            patient_id=row.patient_id,
            issued_at=_to_epoch(row.issued_at),
            expires_at=_to_epoch(row.expires_at),
            chain_started_at=_to_epoch(row.chain_started_at),
            revoked_at=None if row.revoked_at is None else _to_epoch(row.revoked_at),
        )

    def revoke(self, jti: str, *, at: int) -> None:
        """Retire one session.

        Idempotent: an already-revoked row keeps its original
        ``revoked_at``, so a rotation cannot rewrite when access actually
        ended.
        """
        self._session.execute(
            update(PortalSessionRow)
            .where(
                PortalSessionRow.jti == jti,
                PortalSessionRow.revoked_at.is_(None),
            )
            .values(revoked_at=_from_epoch(at))
        )
        self._session.flush()

    def revoke_all_for_patient(self, patient_id: str, *, at: int) -> int:
        """Revoke every live session for one patient; return how many.

        The clinician kill switch's second half, and what makes "a leaked
        link must not grant access" hold after the step-up gate has already
        been cleared.
        """
        # cast: see ``DbPortalAuthStore.consume_outstanding``.
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(PortalSessionRow)
                .where(
                    PortalSessionRow.patient_id == patient_id,
                    PortalSessionRow.revoked_at.is_(None),
                )
                .values(revoked_at=_from_epoch(at))
            ),
        )
        self._session.flush()
        return result.rowcount or 0

    def live_count_for_patient(self, patient_id: str, *, now: int) -> int:
        """How many sessions for this patient would authenticate right now."""
        rows = self._session.execute(
            select(PortalSessionRow.expires_at).where(
                PortalSessionRow.patient_id == patient_id,
                PortalSessionRow.revoked_at.is_(None),
                PortalSessionRow.expires_at > _from_epoch(now),
            )
        ).all()
        return len(rows)

    def has_unrevoked_session(self, patient_id: str) -> bool:
        """Does any session row for this patient stand unrevoked?

        No expiry predicate, deliberately — see the protocol for why an
        expired session and a revoked one answer differently here.
        """
        jti = self._session.execute(
            select(PortalSessionRow.jti).where(
                PortalSessionRow.patient_id == patient_id,
                PortalSessionRow.revoked_at.is_(None),
            )
        ).first()
        return jti is not None
