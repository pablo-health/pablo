# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Server-side portal sign-in state — the shapes, and an in-memory store.

The invite token is signed and self-expiring, but three properties cannot
live in a stateless token, and each one is state:

* SINGLE-USE (a token is valid until first redeemed) and step-up ATTEMPT
  limiting, keyed on the invite token's ``jti`` — :class:`InviteChallenge`.
* REVOCATION: a minted session token is otherwise valid until it expires, no
  matter what the clinician does — :class:`PortalSessionRecord`.

This module is deliberately pure — protocols, frozen dataclasses and
dict-backed implementations — so the service and its unit tests import no
settings, no SQLAlchemy and no database. The Postgres implementations of
both protocols live in :mod:`app.portal.db_store`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Protocol


@dataclass(frozen=True)
class InviteChallenge:
    jti: str
    patient_id: str
    tenant: str
    otp_hash: str
    expires_at: int  # unix seconds
    attempts: int = 0
    consumed: bool = False


class PortalAuthStore(Protocol):
    def put_challenge(self, challenge: InviteChallenge) -> None: ...
    def get_challenge(self, jti: str) -> InviteChallenge | None: ...
    def increment_attempts(self, jti: str) -> int:
        """Bump the attempt counter; return the new total."""
        ...

    def mark_consumed(self, jti: str) -> None: ...
    def consume_outstanding(self, patient_id: str) -> int:
        """Burn every unconsumed challenge for one patient; return the count.

        The clinician kill switch's first half: revoking portal access while
        an invite is in flight has to stop that invite too, or the patient
        redeems it a minute later and is back in.
        """
        ...

    def has_outstanding(self, patient_id: str) -> bool:
        """Is there a live, unconsumed, unexpired invite for this patient?"""
        ...

    def has_unconsumed_challenge(self, patient_id: str) -> bool:
        """Is any invitation for this patient still unspent, expired or not?

        Deliberately not :meth:`has_outstanding`, which also requires the
        invitation to be unexpired. This one asks a different question —
        whether access was ever granted and never withdrawn — and an
        invitation that timed out unredeemed is the most ordinary reason a
        patient asks for a new link. Withdrawal consumes every outstanding
        challenge (see :meth:`consume_outstanding`), so a revoked patient
        answers ``False`` here.
        """
        ...


class InMemoryPortalAuthStore:
    """Dict-backed challenge store for unit tests and local runs."""

    def __init__(self) -> None:
        self._by_jti: dict[str, InviteChallenge] = {}

    def put_challenge(self, challenge: InviteChallenge) -> None:
        self._by_jti[challenge.jti] = challenge

    def get_challenge(self, jti: str) -> InviteChallenge | None:
        return self._by_jti.get(jti)

    def increment_attempts(self, jti: str) -> int:
        current = self._by_jti[jti]
        updated = replace(current, attempts=current.attempts + 1)
        self._by_jti[jti] = updated
        return updated.attempts

    def mark_consumed(self, jti: str) -> None:
        current = self._by_jti[jti]
        self._by_jti[jti] = replace(current, consumed=True)

    def consume_outstanding(self, patient_id: str) -> int:
        consumed = 0
        for jti, current in list(self._by_jti.items()):
            if current.patient_id == patient_id and not current.consumed:
                self._by_jti[jti] = replace(current, consumed=True)
                consumed += 1
        return consumed

    def has_outstanding(self, patient_id: str) -> bool:
        now = int(time.time())
        return any(
            c.patient_id == patient_id and not c.consumed and c.expires_at > now
            for c in self._by_jti.values()
        )

    def has_unconsumed_challenge(self, patient_id: str) -> bool:
        return any(c.patient_id == patient_id and not c.consumed for c in self._by_jti.values())


@dataclass(frozen=True)
class PortalSessionRecord:
    """One patient session's server-side state (unix seconds throughout).

    ``chain_started_at`` is the original redemption's timestamp, carried
    forward unchanged by every refresh. Rotation alone would let a session
    live forever an hour at a time; the chain start is what gives sliding
    renewal a ceiling.
    """

    jti: str
    patient_id: str
    issued_at: int
    expires_at: int
    chain_started_at: int
    revoked_at: int | None = None

    def is_live(self, *, now: int) -> bool:
        """Would this session authenticate right now?

        The single place "still valid" is decided, so the resolver, the
        refresh path and the revocation tests cannot drift apart on it.
        """
        return self.revoked_at is None and now < self.expires_at


class PortalSessionStore(Protocol):
    """The server-side revocation list, keyed on a session token's ``jti``.

    Nothing here interprets a token: callers verify the signature first and
    bring the ``jti``. Kept separate from :class:`PortalAuthStore` because
    the invite challenge and the session list have no operation in common
    and no reason to be implemented together.
    """

    def record(self, session: PortalSessionRecord) -> None: ...
    def get(self, jti: str) -> PortalSessionRecord | None: ...
    def revoke(self, jti: str, *, at: int) -> None: ...
    def revoke_all_for_patient(self, patient_id: str, *, at: int) -> int:
        """Revoke every live session for one patient; return how many."""
        ...

    def live_count_for_patient(self, patient_id: str, *, now: int) -> int:
        """How many of this patient's sessions would authenticate right now."""
        ...

    def has_unrevoked_session(self, patient_id: str) -> bool:
        """Does any session row for this patient stand unrevoked?

        Expiry is deliberately not part of it. A session that simply ran out
        is the normal end of a month of use and says nothing about whether
        the practice still wants this person to have access; a session that
        was REVOKED says exactly that. So this reads the revocation column
        and ignores the clock, which is what lets "has the practice withdrawn
        access?" be answered without a column that records it directly.
        """
        ...


class InMemoryPortalSessionStore:
    """Dict-backed session list for unit tests and local runs."""

    def __init__(self) -> None:
        self._by_jti: dict[str, PortalSessionRecord] = {}

    def record(self, session: PortalSessionRecord) -> None:
        self._by_jti[session.jti] = session

    def get(self, jti: str) -> PortalSessionRecord | None:
        return self._by_jti.get(jti)

    def revoke(self, jti: str, *, at: int) -> None:
        current = self._by_jti.get(jti)
        # Idempotent: an already-revoked session keeps its original
        # ``revoked_at``, so a later rotation can't rewrite when access
        # actually ended.
        if current is None or current.revoked_at is not None:
            return
        self._by_jti[jti] = replace(current, revoked_at=at)

    def revoke_all_for_patient(self, patient_id: str, *, at: int) -> int:
        revoked = 0
        for jti, current in list(self._by_jti.items()):
            if current.patient_id == patient_id and current.revoked_at is None:
                self._by_jti[jti] = replace(current, revoked_at=at)
                revoked += 1
        return revoked

    def live_count_for_patient(self, patient_id: str, *, now: int) -> int:
        return sum(
            1 for s in self._by_jti.values() if s.patient_id == patient_id and s.is_live(now=now)
        )

    def has_unrevoked_session(self, patient_id: str) -> bool:
        return any(
            s.patient_id == patient_id and s.revoked_at is None for s in self._by_jti.values()
        )
