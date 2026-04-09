# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Short-lived, single-use authorization code store for native app auth.

Replaces raw token passing in URLs with an opaque code that the native
app exchanges for tokens via a backend call (RFC 8252 pattern).

Two implementations:
- InMemoryAuthCodeStore: process-local, for single-instance / self-hosted
- RedisAuthCodeStore: shared across instances, for multi-instance Cloud Run
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingAuthCode:
    id_token: str
    refresh_token: str
    redirect_uri: str
    created_at: float


class AuthCodeStore(Protocol):
    """Protocol for auth code stores."""

    def create(self, id_token: str, refresh_token: str, redirect_uri: str) -> str: ...
    def exchange(self, code: str) -> PendingAuthCode | None: ...


class InMemoryAuthCodeStore:
    """Thread-safe in-memory store for one-time authorization codes."""

    def __init__(self, ttl_seconds: int = 60, max_pending: int = 1000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_pending = max_pending
        self._codes: dict[str, PendingAuthCode] = {}
        self._lock = Lock()

    def create(self, id_token: str, refresh_token: str, redirect_uri: str) -> str:
        code = secrets.token_urlsafe(32)
        now = time.monotonic()

        with self._lock:
            self._prune(now)
            if len(self._codes) >= self.max_pending:
                raise RuntimeError("Too many pending authorization codes")
            self._codes[code] = PendingAuthCode(
                id_token=id_token,
                refresh_token=refresh_token,
                redirect_uri=redirect_uri,
                created_at=now,
            )
        return code

    def exchange(self, code: str) -> PendingAuthCode | None:
        now = time.monotonic()
        with self._lock:
            entry = self._codes.pop(code, None)
        if entry is None:
            return None
        if now - entry.created_at > self.ttl_seconds:
            return None
        return entry

    def _prune(self, now: float) -> None:
        expired = [k for k, v in self._codes.items() if now - v.created_at > self.ttl_seconds]
        for k in expired:
            del self._codes[k]


class RedisAuthCodeStore:
    """Redis-backed store for one-time authorization codes.

    Codes are stored as JSON with a TTL. Exchange uses GET + DELETE
    to ensure single-use semantics.
    """

    KEY_PREFIX = "authcode:"

    def __init__(self, redis_client: redis.Redis, ttl_seconds: int = 60) -> None:
        self._redis = redis_client
        self.ttl_seconds = ttl_seconds

    def create(self, id_token: str, refresh_token: str, redirect_uri: str) -> str:
        code = secrets.token_urlsafe(32)
        data = json.dumps(
            {
                "id_token": id_token,
                "refresh_token": refresh_token,
                "redirect_uri": redirect_uri,
                "created_at": time.time(),
            }
        )
        self._redis.setex(f"{self.KEY_PREFIX}{code}", self.ttl_seconds, data)
        return code

    def exchange(self, code: str) -> PendingAuthCode | None:
        key = f"{self.KEY_PREFIX}{code}"
        # GET + DELETE — if two instances race, only one gets the value
        pipe = self._redis.pipeline()
        pipe.get(key)
        pipe.delete(key)
        raw, _ = pipe.execute()

        if raw is None:
            return None

        data = json.loads(raw)
        return PendingAuthCode(
            id_token=data["id_token"],
            refresh_token=data["refresh_token"],
            redirect_uri=data["redirect_uri"],
            created_at=data["created_at"],
        )


def _get_store() -> AuthCodeStore:
    """Create the appropriate store based on settings."""
    from ..redis_client import get_redis_client

    client = get_redis_client()
    if client is not None:
        logger.info("Using Redis-backed auth code store")
        return RedisAuthCodeStore(client)
    logger.info("Using in-memory auth code store")
    return InMemoryAuthCodeStore()


_store: AuthCodeStore | None = None


def _ensure_store() -> AuthCodeStore:
    global _store  # noqa: PLW0603
    if _store is None:
        _store = _get_store()
    return _store


def create_auth_code(id_token: str, refresh_token: str, redirect_uri: str) -> str:
    return _ensure_store().create(id_token, refresh_token, redirect_uri)


def exchange_auth_code(code: str) -> PendingAuthCode | None:
    return _ensure_store().exchange(code)
