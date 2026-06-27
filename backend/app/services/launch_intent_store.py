# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Short-lived, single-use launch-intent store for the companion handoff.

When a therapist clicks "Start Session" on the web dashboard, the backend
mints an opaque ``intent_id`` (128-bit random) and returns it once. The
desktop companion later presents that id at ``/launch/redeem``. The store
never persists the raw id — only its SHA-256 hash — so an intercepted
store dump is worthless, and redemption is single-use and atomic.

Two implementations mirror the auth-code store:
- InMemoryLaunchIntentStore: process-local, for single-instance / self-hosted.
- RedisLaunchIntentStore: shared across instances, for multi-instance Cloud Run.

Intents are bound to the issuing ``user_id`` and an ``appointment_id``;
redemption re-verifies the redeeming token's user_id against the stored
binding (the route layer does that check). TTL is 180 seconds.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from threading import Lock
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import text

from ..utcnow import utc_now

if TYPE_CHECKING:
    import redis
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Launch-intent TTL: 180 seconds (companion-thin-client.md, Resolved
# decisions). Tight enough that an intercepted id has a narrow replay
# window; forgiving enough for OS consent prompts during handoff.
LAUNCH_INTENT_TTL_SECONDS = 180


def _hash_intent(intent_id: str) -> str:
    """SHA-256 hash of the raw intent id, used as the lookup key.

    The stored value is itself a hash, never the secret, so a DB/Redis
    equality lookup on this column does not leak the raw id.
    """
    return hashlib.sha256(intent_id.encode()).hexdigest()


@dataclass(frozen=True)
class RedeemedIntent:
    """The binding revealed by a successful single-use claim."""

    user_id: str
    appointment_id: str


class LaunchIntentStore(Protocol):
    """Protocol for launch-intent stores."""

    def create(self, user_id: str, appointment_id: str) -> str:
        """Mint a new intent, store its hash, return the raw id (once)."""
        ...

    def redeem(self, intent_id: str) -> RedeemedIntent | None:
        """Atomically consume the intent. Returns its binding or None.

        ``None`` covers unknown, expired, and already-consumed ids
        indistinguishably — the caller maps all of these to one generic
        failure so the endpoint is not an existence oracle.
        """
        ...


class InMemoryLaunchIntentStore:
    """Thread-safe in-memory store for single-use launch intents."""

    @dataclass(frozen=True)
    class _Record:
        user_id: str
        appointment_id: str
        created_at: float

    def __init__(
        self,
        ttl_seconds: int = LAUNCH_INTENT_TTL_SECONDS,
        max_pending: int = 10_000,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_pending = max_pending
        self._records: dict[str, InMemoryLaunchIntentStore._Record] = {}
        self._lock = Lock()

    def create(self, user_id: str, appointment_id: str) -> str:
        # 128-bit random, URL-safe (22 chars, no padding).
        intent_id = secrets.token_urlsafe(16)
        intent_hash = _hash_intent(intent_id)
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            if len(self._records) >= self.max_pending:
                raise RuntimeError("Too many pending launch intents")
            self._records[intent_hash] = self._Record(
                user_id=user_id,
                appointment_id=appointment_id,
                created_at=now,
            )
        return intent_id

    def redeem(self, intent_id: str) -> RedeemedIntent | None:
        intent_hash = _hash_intent(intent_id)
        now = time.monotonic()
        with self._lock:
            # pop() makes the claim single-use and atomic under the lock.
            record = self._records.pop(intent_hash, None)
        if record is None:
            return None
        if now - record.created_at > self.ttl_seconds:
            return None
        return RedeemedIntent(
            user_id=record.user_id,
            appointment_id=record.appointment_id,
        )

    def _prune(self, now: float) -> None:
        expired = [h for h, r in self._records.items() if now - r.created_at > self.ttl_seconds]
        for h in expired:
            del self._records[h]


class RedisLaunchIntentStore:
    """Redis-backed store for single-use launch intents.

    Records are stored under the intent hash with a TTL backstop.
    Redemption uses an atomic GET + DELETE pipeline so that, under a
    multi-instance race, exactly one redeem wins.
    """

    KEY_PREFIX = "launchintent:"

    def __init__(
        self,
        redis_client: redis.Redis,
        ttl_seconds: int = LAUNCH_INTENT_TTL_SECONDS,
    ) -> None:
        self._redis = redis_client
        self.ttl_seconds = ttl_seconds

    def create(self, user_id: str, appointment_id: str) -> str:
        intent_id = secrets.token_urlsafe(16)
        intent_hash = _hash_intent(intent_id)
        data = json.dumps(
            {
                "user_id": user_id,
                "appointment_id": appointment_id,
                "created_at": time.time(),
            }
        )
        self._redis.setex(f"{self.KEY_PREFIX}{intent_hash}", self.ttl_seconds, data)
        return intent_id

    def redeem(self, intent_id: str) -> RedeemedIntent | None:
        intent_hash = _hash_intent(intent_id)
        key = f"{self.KEY_PREFIX}{intent_hash}"
        # GET + DELETE — if two instances race, only one gets the value.
        pipe = self._redis.pipeline()
        pipe.get(key)
        pipe.delete(key)
        raw, _ = pipe.execute()
        if raw is None:
            return None
        data = json.loads(raw)
        # The TTL on the key is the expiry backstop; if the key was still
        # present it is within its 180s window.
        return RedeemedIntent(
            user_id=data["user_id"],
            appointment_id=data["appointment_id"],
        )


class PostgresLaunchIntentStore:
    """Postgres-backed store using ``platform.launch_intents``.

    Durable and multi-instance-safe: redemption is a single atomic
    ``UPDATE ... WHERE consumed_at IS NULL AND expires_at > now()
    RETURNING`` so exactly one redeem can win even across Cloud Run
    instances. The raw intent id never lands in a column — only its
    SHA-256 hash (``intent_hash``) does.
    """

    def __init__(self, session: Session, ttl_seconds: int = LAUNCH_INTENT_TTL_SECONDS) -> None:
        self._session = session
        self.ttl_seconds = ttl_seconds

    def create(self, user_id: str, appointment_id: str) -> str:
        intent_id = secrets.token_urlsafe(16)
        intent_hash = _hash_intent(intent_id)
        now = utc_now()
        expires_at = now + timedelta(seconds=self.ttl_seconds)
        self._session.execute(
            text(
                """
                INSERT INTO platform.launch_intents
                    (intent_hash, user_id, appointment_id, created_at, expires_at, consumed_at)
                VALUES (:intent_hash, :user_id, :appointment_id, :created_at, :expires_at, NULL)
                """
            ),
            {
                "intent_hash": intent_hash,
                "user_id": user_id,
                "appointment_id": appointment_id,
                "created_at": now,
                "expires_at": expires_at,
            },
        )
        self._session.flush()
        return intent_id

    def redeem(self, intent_id: str) -> RedeemedIntent | None:
        intent_hash = _hash_intent(intent_id)
        # Atomic single-use claim: only an unconsumed, unexpired row is
        # updated, and it returns its binding exactly once.
        row = self._session.execute(
            text(
                """
                UPDATE platform.launch_intents
                   SET consumed_at = :now
                 WHERE intent_hash = :intent_hash
                   AND consumed_at IS NULL
                   AND expires_at > :now
             RETURNING user_id, appointment_id
                """
            ),
            {"intent_hash": intent_hash, "now": utc_now()},
        ).first()
        self._session.flush()
        if row is None:
            return None
        # ``user_id`` is a native uuid column → psycopg2 returns a
        # uuid.UUID. The route compares it against the str user id from
        # the token, so coerce to str here for a meaningful equality.
        return RedeemedIntent(user_id=str(row[0]), appointment_id=row[1])


def _build_store() -> LaunchIntentStore:
    """Pick a store backend per request.

    Prefers the durable Postgres table when a request-scoped DB session
    is in scope; falls back to Redis (if configured) then a process-local
    in-memory store (dev / tests with no DB session).
    """
    try:
        from ..db import get_db_session

        session = get_db_session()
        return PostgresLaunchIntentStore(session)
    except RuntimeError:
        pass

    from ..redis_client import get_redis_client

    client = get_redis_client()
    if client is not None:
        logger.info("Using Redis-backed launch intent store")
        return RedisLaunchIntentStore(client)

    logger.info("Using in-memory launch intent store")
    return _ensure_memory_store()


# Process-local fallback store. Shared across requests so an intent
# minted on one request can be redeemed on a later one when neither
# Postgres nor Redis is available (dev / unit tests).
_memory_store: InMemoryLaunchIntentStore | None = None


def _ensure_memory_store() -> InMemoryLaunchIntentStore:
    global _memory_store  # noqa: PLW0603
    if _memory_store is None:
        _memory_store = InMemoryLaunchIntentStore()
    return _memory_store


def create_launch_intent(user_id: str, appointment_id: str) -> str:
    return _build_store().create(user_id, appointment_id)


def redeem_launch_intent(intent_id: str) -> RedeemedIntent | None:
    return _build_store().redeem(intent_id)
