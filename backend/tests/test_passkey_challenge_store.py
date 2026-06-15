# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Single-use semantics of the in-memory passkey challenge store (H5)."""

from __future__ import annotations

from app.services.passkey_challenge_store import InMemoryPasskeyChallengeStore

CHALLENGE = b"\x01\x02\x03\x04" * 8


class TestSingleUse:
    def test_create_then_consume_returns_binding(self) -> None:
        store = InMemoryPasskeyChallengeStore()
        store.create("register", "user-1", CHALLENGE)
        consumed = store.consume("register", CHALLENGE)
        assert consumed is not None
        assert consumed.user_id == "user-1"

    def test_second_consume_returns_none(self) -> None:
        store = InMemoryPasskeyChallengeStore()
        store.create("register", "user-1", CHALLENGE)
        assert store.consume("register", CHALLENGE) is not None
        assert store.consume("register", CHALLENGE) is None

    def test_wrong_ceremony_does_not_match(self) -> None:
        store = InMemoryPasskeyChallengeStore()
        store.create("register", "user-1", CHALLENGE)
        # An authenticate-finish must not consume a register challenge.
        assert store.consume("authenticate", CHALLENGE) is None

    def test_unknown_challenge_returns_none(self) -> None:
        store = InMemoryPasskeyChallengeStore()
        assert store.consume("authenticate", CHALLENGE) is None

    def test_usernameless_binding_is_none(self) -> None:
        store = InMemoryPasskeyChallengeStore()
        store.create("authenticate", None, CHALLENGE)
        consumed = store.consume("authenticate", CHALLENGE)
        assert consumed is not None
        assert consumed.user_id is None

    def test_expired_challenge_returns_none(self) -> None:
        # ttl of -1s expires any positive elapsed time deterministically.
        store = InMemoryPasskeyChallengeStore(ttl_seconds=-1)
        store.create("register", "user-1", CHALLENGE)
        assert store.consume("register", CHALLENGE) is None
