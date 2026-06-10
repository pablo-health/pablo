# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Shared replay-detection guard (single-use keys with a TTL).

Answers one question atomically: "has this key been seen within the
last ``ttl_seconds``?" Used for nonce/jti caches where a value must be
accepted at most once per freshness window.

Two implementations, selected by :func:`get_replay_guard` the same way
``services/auth_code_store.py`` and ``rate_limit.py`` pick their
backends — Redis when configured, in-memory otherwise:

- ``RedisReplayGuard``: ``SET NX EX``, shared across instances, so a
  replayed key is rejected no matter which replica sees it. If Redis
  errors mid-flight it degrades to an in-process guard for that call
  rather than failing the request — replay protection narrows to
  per-process during the outage instead of taking the endpoint down.
- ``InMemoryReplayGuard``: thread-safe TTL'd LRU for single-instance /
  self-hosted deployments.

New replay/nonce caches should come from here rather than growing
another hand-rolled Redis-or-memory pair.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from threading import Lock
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ENTRIES = 10_000


class ReplayGuard(Protocol):
    """Protocol for replay guards."""

    def check_and_add(self, key: str, now: float | None = None) -> bool:
        """Record ``key``; True if fresh, False if seen within the TTL."""
        ...


class InMemoryReplayGuard:
    """Per-process LRU of seen keys with a TTL.

    ``check_and_add`` returns ``True`` if the key is fresh (and records
    it) or ``False`` if it was already seen within the TTL. Expired
    entries are evicted lazily on access plus an LRU cap as a hard
    backstop against unbounded growth from a flood of unique keys.
    """

    def __init__(
        self,
        ttl_seconds: int,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._lock = Lock()

    def check_and_add(self, key: str, now: float | None = None) -> bool:
        ts = time.time() if now is None else now
        cutoff = ts - self._ttl
        with self._lock:
            # Evict expired entries from the front (oldest first).
            while self._seen:
                _, oldest_ts = next(iter(self._seen.items()))
                if oldest_ts <= cutoff:
                    self._seen.popitem(last=False)
                else:
                    break
            existing = self._seen.get(key)
            if existing is not None and existing > cutoff:
                return False
            self._seen[key] = ts
            self._seen.move_to_end(key)
            while len(self._seen) > self._max:
                self._seen.popitem(last=False)
            return True


class RedisReplayGuard:
    """Redis-backed replay guard shared across instances.

    ``SET NX EX`` is atomic: exactly one caller per key per TTL window
    sees ``True``. Keys are namespaced so independent guards can share
    one Redis. ``now`` is accepted for interface parity with the
    in-memory guard; Redis applies its own clock to the TTL.
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        namespace: str,
        ttl_seconds: int,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._redis = redis_client
        self._namespace = namespace
        self._ttl = ttl_seconds
        # Degraded-mode guard: keeps per-process replay protection when
        # Redis errors instead of failing the request (see module doc).
        self._fallback = InMemoryReplayGuard(ttl_seconds, max_entries)

    def check_and_add(self, key: str, now: float | None = None) -> bool:
        import redis as redis_mod

        try:
            return bool(self._redis.set(f"{self._namespace}:{key}", "1", nx=True, ex=self._ttl))
        except redis_mod.RedisError:
            logger.warning(
                "Redis unavailable for replay guard %s — using in-process fallback",
                self._namespace,
            )
            return self._fallback.check_and_add(key, now=now)


def get_replay_guard(
    namespace: str,
    ttl_seconds: int,
    max_entries: int = _DEFAULT_MAX_ENTRIES,
) -> ReplayGuard:
    """Create a replay guard: Redis-backed when configured, in-memory otherwise."""
    from ..redis_client import get_redis_client

    client = get_redis_client()
    if client is not None:
        logger.info("Using Redis-backed replay guard for %s", namespace)
        return RedisReplayGuard(client, namespace=namespace, ttl_seconds=ttl_seconds)
    return InMemoryReplayGuard(ttl_seconds=ttl_seconds, max_entries=max_entries)
