# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the native app authorization code store."""

import time
from unittest.mock import patch

import pytest
from app.services.auth_code_store import InMemoryAuthCodeStore

FAKE_TOKEN = "test-token-value"
FAKE_REFRESH = "test-refresh-value"
FAKE_URI = "pablohealth://cb"


class TestAuthCodeStore:
    def setup_method(self) -> None:
        self.store = InMemoryAuthCodeStore(ttl_seconds=5)

    def test_create_returns_nonempty_code(self) -> None:
        code = self.store.create(FAKE_TOKEN, FAKE_REFRESH, FAKE_URI)
        assert isinstance(code, str)
        assert len(code) > 0

    def test_exchange_returns_tokens(self) -> None:
        code = self.store.create(FAKE_TOKEN, FAKE_REFRESH, FAKE_URI)
        entry = self.store.exchange(code)
        assert entry is not None
        assert entry.id_token == FAKE_TOKEN
        assert entry.refresh_token == FAKE_REFRESH
        assert entry.redirect_uri == FAKE_URI

    def test_exchange_single_use(self) -> None:
        code = self.store.create(FAKE_TOKEN, FAKE_REFRESH, FAKE_URI)
        assert self.store.exchange(code) is not None
        assert self.store.exchange(code) is None

    def test_exchange_invalid_code(self) -> None:
        assert self.store.exchange("nonexistent") is None

    def test_exchange_expired_code(self) -> None:
        store = InMemoryAuthCodeStore(ttl_seconds=0)
        code = store.create(FAKE_TOKEN, FAKE_REFRESH, FAKE_URI)
        # Code expired immediately (TTL=0)
        assert store.exchange(code) is None

    def test_prune_removes_expired(self) -> None:
        store = InMemoryAuthCodeStore(ttl_seconds=1)
        store.create(FAKE_TOKEN, FAKE_REFRESH, FAKE_URI)

        with patch("app.services.auth_code_store.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + 10
            # Creating a new code triggers pruning
            store.create("tok2", "ref2", FAKE_URI)
            # Only the new code should remain
            assert len(store._codes) == 1

    def test_max_pending_limit(self) -> None:
        store = InMemoryAuthCodeStore(ttl_seconds=60, max_pending=3)
        store.create("a", "b", FAKE_URI)
        store.create("c", "d", FAKE_URI)
        store.create("e", "f", FAKE_URI)
        with pytest.raises(RuntimeError):
            store.create("g", "h", FAKE_URI)
