# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Short-lived, single-use authorization code store for native app auth.

Replaces raw token passing in URLs with an opaque code that the native
app exchanges for tokens via a backend call (RFC 8252 pattern).

NOTE: This is a process-local in-memory store. For multi-instance
deployments (e.g. Cloud Run with min_instances > 1), replace with a
Redis-backed implementation to ensure codes can be created on one
instance and exchanged on another.
"""

import secrets
import time
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class PendingAuthCode:
    id_token: str
    refresh_token: str
    redirect_uri: str
    created_at: float


class AuthCodeStore:
    """Thread-safe in-memory store for one-time authorization codes."""

    def __init__(self, ttl_seconds: int = 60, max_pending: int = 1000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_pending = max_pending
        self._codes: dict[str, PendingAuthCode] = {}
        self._lock = Lock()

    def create(self, id_token: str, refresh_token: str, redirect_uri: str) -> str:
        """Store tokens under a new one-time code and return the code."""
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
        """Consume a code, returning tokens if valid and not expired."""
        now = time.monotonic()
        with self._lock:
            entry = self._codes.pop(code, None)
        if entry is None:
            return None
        if now - entry.created_at > self.ttl_seconds:
            return None
        return entry

    def _prune(self, now: float) -> None:
        """Remove expired entries (called under lock)."""
        expired = [k for k, v in self._codes.items() if now - v.created_at > self.ttl_seconds]
        for k in expired:
            del self._codes[k]


_store = AuthCodeStore()


def create_auth_code(id_token: str, refresh_token: str, redirect_uri: str) -> str:
    return _store.create(id_token, refresh_token, redirect_uri)


def exchange_auth_code(code: str) -> PendingAuthCode | None:
    return _store.exchange(code)
