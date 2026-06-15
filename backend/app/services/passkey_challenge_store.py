# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Single-use store for in-flight WebAuthn ceremony challenges.

A challenge is the CSPRNG nonce ``py_webauthn`` puts in the begin
options. We never persist the raw challenge — only its SHA-256 hash —
so a store dump is worthless; the raw value leaves the server exactly
once, inside the begin response, and comes back inside the client's
signed ``clientDataJSON`` at finish. On finish we hash the returned
challenge and atomically consume the matching row, which proves *we*
issued it, it is unconsumed, unexpired, and for the expected ceremony.

Modeled on ``services.launch_intent_store``: a Postgres-backed durable
store (production, multi-instance safe) with Redis and in-memory
fallbacks for environments without a request-scoped DB session.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from threading import Lock
from typing import TYPE_CHECKING, Literal, Protocol

from sqlalchemy import text

from ..utcnow import utc_now

if TYPE_CHECKING:
    import redis
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

Ceremony = Literal["register", "authenticate"]

# Short, authoritative expiry — a captured assertion has a narrow replay
# window. Re-checked server-side on finish. Mirrors LaunchIntentRow's
# short-TTL order of magnitude (300s ceiling per the build spec).
PASSKEY_CHALLENGE_TTL_SECONDS = 300


def _hash_challenge(challenge: bytes) -> str:
    """SHA-256 of the raw challenge bytes — the single-use lookup key."""
    return hashlib.sha256(challenge).hexdigest()


@dataclass(frozen=True)
class ConsumedChallenge:
    """The binding revealed by a successful single-use claim.

    ``user_id`` is the user the challenge was bound to at begin time —
    set for registration, ``None`` for a usernameless authentication
    ceremony (the asserting credential determines the user instead).
    """

    user_id: str | None


class PasskeyChallengeStore(Protocol):
    """Protocol for single-use passkey-challenge stores."""

    def create(self, ceremony: Ceremony, user_id: str | None, challenge: bytes) -> None:
        """Persist the SHA-256 of a freshly-issued ceremony challenge."""
        ...

    def consume(self, ceremony: Ceremony, challenge: bytes) -> ConsumedChallenge | None:
        """Atomically claim a pending challenge for ``ceremony``.

        Returns its binding, or ``None`` for unknown / expired /
        already-consumed / wrong-ceremony — indistinguishably, so the
        caller maps all to one generic failure.
        """
        ...


class InMemoryPasskeyChallengeStore:
    """Thread-safe in-memory store (single-instance / tests)."""

    @dataclass(frozen=True)
    class _Record:
        ceremony: Ceremony
        user_id: str | None
        created_at: float

    def __init__(self, ttl_seconds: int = PASSKEY_CHALLENGE_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._records: dict[str, InMemoryPasskeyChallengeStore._Record] = {}
        self._lock = Lock()

    def create(self, ceremony: Ceremony, user_id: str | None, challenge: bytes) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._records[_hash_challenge(challenge)] = self._Record(
                ceremony=ceremony, user_id=user_id, created_at=now
            )

    def consume(self, ceremony: Ceremony, challenge: bytes) -> ConsumedChallenge | None:
        now = time.monotonic()
        with self._lock:
            record = self._records.pop(_hash_challenge(challenge), None)
        if record is None or record.ceremony != ceremony:
            return None
        if now - record.created_at > self.ttl_seconds:
            return None
        return ConsumedChallenge(user_id=record.user_id)

    def _prune(self, now: float) -> None:
        expired = [h for h, r in self._records.items() if now - r.created_at > self.ttl_seconds]
        for h in expired:
            del self._records[h]


class RedisPasskeyChallengeStore:
    """Redis-backed store; GET+DELETE pipeline makes the claim atomic."""

    KEY_PREFIX = "passkeychallenge:"

    def __init__(
        self, redis_client: redis.Redis, ttl_seconds: int = PASSKEY_CHALLENGE_TTL_SECONDS
    ) -> None:
        self._redis = redis_client
        self.ttl_seconds = ttl_seconds

    def create(self, ceremony: Ceremony, user_id: str | None, challenge: bytes) -> None:
        data = json.dumps({"ceremony": ceremony, "user_id": user_id})
        self._redis.setex(
            f"{self.KEY_PREFIX}{_hash_challenge(challenge)}", self.ttl_seconds, data
        )

    def consume(self, ceremony: Ceremony, challenge: bytes) -> ConsumedChallenge | None:
        key = f"{self.KEY_PREFIX}{_hash_challenge(challenge)}"
        pipe = self._redis.pipeline()
        pipe.get(key)
        pipe.delete(key)
        raw, _ = pipe.execute()
        if raw is None:
            return None
        data = json.loads(raw)
        if data.get("ceremony") != ceremony:
            return None
        return ConsumedChallenge(user_id=data.get("user_id"))


class PostgresPasskeyChallengeStore:
    """Durable store using ``platform.passkey_challenges``.

    Consumption is a single atomic ``UPDATE ... WHERE consumed_at IS NULL
    AND expires_at > now()`` so exactly one finish can win even across
    Cloud Run instances. Only the challenge hash ever lands in a column.
    """

    def __init__(self, session: Session, ttl_seconds: int = PASSKEY_CHALLENGE_TTL_SECONDS) -> None:
        self._session = session
        self.ttl_seconds = ttl_seconds

    def create(self, ceremony: Ceremony, user_id: str | None, challenge: bytes) -> None:
        now = utc_now()
        self._session.execute(
            text(
                """
                INSERT INTO platform.passkey_challenges
                    (challenge_hash, ceremony, user_id, created_at, expires_at, consumed_at)
                VALUES (:challenge_hash, :ceremony, :user_id, :created_at, :expires_at, NULL)
                """
            ),
            {
                "challenge_hash": _hash_challenge(challenge),
                "ceremony": ceremony,
                "user_id": user_id,
                "created_at": now,
                "expires_at": now + timedelta(seconds=self.ttl_seconds),
            },
        )
        self._session.flush()

    def consume(self, ceremony: Ceremony, challenge: bytes) -> ConsumedChallenge | None:
        row = self._session.execute(
            text(
                """
                UPDATE platform.passkey_challenges
                   SET consumed_at = :now
                 WHERE challenge_hash = :challenge_hash
                   AND ceremony = :ceremony
                   AND consumed_at IS NULL
                   AND expires_at > :now
             RETURNING user_id
                """
            ),
            {
                "challenge_hash": _hash_challenge(challenge),
                "ceremony": ceremony,
                "now": utc_now(),
            },
        ).first()
        self._session.flush()
        if row is None:
            return None
        return ConsumedChallenge(user_id=str(row[0]) if row[0] is not None else None)


# Process-local fallback, shared across requests so a challenge minted on
# one request can be consumed on a later one when neither Postgres nor
# Redis is available (dev / unit tests).
_memory_store: InMemoryPasskeyChallengeStore | None = None


def _ensure_memory_store() -> InMemoryPasskeyChallengeStore:
    global _memory_store  # noqa: PLW0603
    if _memory_store is None:
        _memory_store = InMemoryPasskeyChallengeStore()
    return _memory_store


def build_challenge_store() -> PasskeyChallengeStore:
    """Pick a store backend per request (Postgres → Redis → in-memory)."""
    try:
        from ..db import get_db_session

        return PostgresPasskeyChallengeStore(get_db_session())
    except RuntimeError:
        pass

    from ..redis_client import get_redis_client

    client = get_redis_client()
    if client is not None:
        return RedisPasskeyChallengeStore(client)

    return _ensure_memory_store()
