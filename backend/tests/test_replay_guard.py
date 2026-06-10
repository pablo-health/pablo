# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the shared replay guard (services/replay_guard.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import redis
from app import redis_client
from app.services.replay_guard import (
    InMemoryReplayGuard,
    RedisReplayGuard,
    get_replay_guard,
)

if TYPE_CHECKING:
    import pytest


class _FakeRedis:
    """Minimal stand-in implementing SET NX EX semantics."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def set(self, name: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and name in self.store:
            return None  # redis-py returns None when NX blocks the write
        self.store[name] = value
        if ex is not None:
            self.ttls[name] = ex
        return True


class _BrokenRedis:
    def set(self, *args: object, **kwargs: object) -> bool:
        raise redis.ConnectionError("redis is down")


class TestInMemoryReplayGuard:
    def test_fresh_key_accepted(self) -> None:
        guard = InMemoryReplayGuard(ttl_seconds=300)
        assert guard.check_and_add("jti-1") is True

    def test_replayed_key_rejected(self) -> None:
        guard = InMemoryReplayGuard(ttl_seconds=300)
        assert guard.check_and_add("jti-1") is True
        assert guard.check_and_add("jti-1") is False

    def test_key_fresh_again_after_ttl(self) -> None:
        guard = InMemoryReplayGuard(ttl_seconds=300)
        assert guard.check_and_add("jti-1", now=1000.0) is True
        assert guard.check_and_add("jti-1", now=1301.0) is True

    def test_replay_within_ttl_rejected(self) -> None:
        guard = InMemoryReplayGuard(ttl_seconds=300)
        assert guard.check_and_add("jti-1", now=1000.0) is True
        assert guard.check_and_add("jti-1", now=1299.0) is False

    def test_lru_cap_bounds_growth(self) -> None:
        guard = InMemoryReplayGuard(ttl_seconds=300, max_entries=3)
        now = 1000.0
        for i in range(5):
            assert guard.check_and_add(f"jti-{i}", now=now + i) is True
        # Oldest entries were evicted by the cap, so they read as fresh.
        assert guard.check_and_add("jti-0", now=now + 5) is True
        # Newest entry is still tracked.
        assert guard.check_and_add("jti-4", now=now + 5) is False


class TestRedisReplayGuard:
    def test_fresh_key_accepted_and_namespaced(self) -> None:
        fake = _FakeRedis()
        guard = RedisReplayGuard(fake, namespace="dpop:jti", ttl_seconds=300)  # type: ignore[arg-type]
        assert guard.check_and_add("jti-1") is True
        assert "dpop:jti:jti-1" in fake.store
        assert fake.ttls["dpop:jti:jti-1"] == 300

    def test_replayed_key_rejected(self) -> None:
        fake = _FakeRedis()
        guard = RedisReplayGuard(fake, namespace="dpop:jti", ttl_seconds=300)  # type: ignore[arg-type]
        assert guard.check_and_add("jti-1") is True
        assert guard.check_and_add("jti-1") is False

    def test_namespaces_are_isolated(self) -> None:
        fake = _FakeRedis()
        a = RedisReplayGuard(fake, namespace="a", ttl_seconds=300)  # type: ignore[arg-type]
        b = RedisReplayGuard(fake, namespace="b", ttl_seconds=300)  # type: ignore[arg-type]
        assert a.check_and_add("jti-1") is True
        assert b.check_and_add("jti-1") is True

    def test_redis_error_degrades_to_in_process_guard(self) -> None:
        guard = RedisReplayGuard(_BrokenRedis(), namespace="dpop:jti", ttl_seconds=300)  # type: ignore[arg-type]
        # Still functional, and still replay-protected within the process.
        assert guard.check_and_add("jti-1") is True
        assert guard.check_and_add("jti-1") is False


class TestGetReplayGuard:
    def test_in_memory_when_redis_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(redis_client, "get_redis_client", lambda: None)
        guard = get_replay_guard("dpop:jti", ttl_seconds=300)
        assert isinstance(guard, InMemoryReplayGuard)

    def test_redis_backed_when_client_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeRedis()
        monkeypatch.setattr(redis_client, "get_redis_client", lambda: fake)
        guard = get_replay_guard("dpop:jti", ttl_seconds=300)
        assert isinstance(guard, RedisReplayGuard)
