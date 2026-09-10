# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""In-memory stand-in for the store that carries a PKCE verifier.

Faked at the Redis boundary rather than at ``pkce_store``'s own functions, so
tests still run the real store — including that reading a verifier deletes it,
which is what makes an OAuth round trip single-use.
"""

from __future__ import annotations

from app.calendar_providers.oauth_state import mint_state, state_nonce
from app.calendar_providers.pkce_store import remember_verifier
from app.services.token_encryption import derive_subkey

TEST_VERIFIER = "test-code-verifier"

_STATE_PURPOSE = "google-calendar-oauth-state"


def authorized_state(user_id: str, *, verifier: str = TEST_VERIFIER) -> str:
    """A state as ``get_auth_url`` would have left behind.

    Minting alone is not enough any more: authorization also stores the
    verifier the exchange has to present, so a state without one stands for an
    authorization that never happened, and the exchange will refuse it.
    """
    state = mint_state(derive_subkey(_STATE_PURPOSE), user_id)
    remember_verifier(state_nonce(state), verifier)
    return state


class FakePkceRedis:
    """Just the two operations the PKCE store uses, held in memory."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def setex(self, key: str, _ttl: int, value: str) -> None:
        self.values[key] = value

    def getdel(self, key: str) -> str | None:
        return self.values.pop(key, None)
