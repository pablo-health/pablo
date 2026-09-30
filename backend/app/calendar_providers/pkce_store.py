# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Carry a PKCE code verifier across the two halves of an OAuth round trip.

The authorization request and the token exchange are separate HTTP requests,
served by whichever instance picks them up. ``google_auth_oauthlib`` generates
the verifier inside ``authorization_url()`` and keeps it on the ``Flow``
object, so it is gone by the time the exchange builds its own ``Flow`` —
Google then rejects the code with ``invalid_grant: Missing code verifier``.

The verifier is stored here instead, keyed by the nonce already inside the
signed state, and read back once. It cannot travel in the state: that value is
signed but not encrypted, and it rides in a URL that reaches access logs,
browser history and ``Referer`` headers — a verifier sitting beside the code it
protects would defeat the point of having one.

Reading deletes, so a state cannot be presented twice. ``verify_state`` is
stateless by design and cannot close that window on its own; this does, as a
side effect of the storage the verifier already needs.

It is kept in Redis. In a development environment with Redis off (a laptop,
the local end-to-end stack) it is kept in this process's memory instead, the
way the passkey challenge store falls back. Anywhere else, no Redis means a
connect can't start, and the clinician is told to try again later rather than
shown a bare error; the log says why. Either way PKCE is kept: a verifier
that can't be stored or found stops the round trip.
"""

from __future__ import annotations

import logging
import time
from threading import Lock

from ..api_errors import ServiceUnavailableError
from ..redis_client import get_redis_client

logger = logging.getLogger(__name__)

_KEY_PREFIX = "gcal:pkce:"

TTL_SECONDS = 600
"""Matches ``oauth_state.MAX_STATE_AGE_SECONDS`` — a verifier is useless once
the state it belongs to has aged out, and holding it longer only widens the
window in which it could be read."""


#: Round trips held at once in memory. Each is a few dozen bytes; the cap
#: only stops a flood of started-and-abandoned connects from growing forever.
MAX_IN_MEMORY = 10_000


class PkceStoreUnavailableError(RuntimeError):
    """A verifier can be neither kept nor found.

    Raised rather than degrading to a flow without PKCE. Dropping it silently
    would weaken the exchange with nothing in the logs to say so; connecting a
    calendar is infrequent and the therapist can simply try again.
    """


class PkceStoreDownError(ServiceUnavailableError, PkceStoreUnavailableError):
    """The store is configured and not answering: nothing about this round
    trip is wrong, and trying again later may work.

    An API error, so every route that starts or finishes a connect answers
    503 with a message the clinician can act on, rather than a bare 500.
    """

    default_message = "Connecting a calendar isn't available right now. Try again in a few minutes."


class _InMemoryVerifiers:
    """Read-once verifiers with a time to live, for a single instance."""

    def __init__(self) -> None:
        self._held: dict[str, tuple[str, float]] = {}
        self._lock = Lock()

    def put(self, nonce: str, verifier: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            if len(self._held) >= MAX_IN_MEMORY:
                raise PkceStoreDownError
            self._held[nonce] = (verifier, now + TTL_SECONDS)

    def take(self, nonce: str) -> str | None:
        with self._lock:
            held = self._held.pop(nonce, None)
        if held is None or time.monotonic() > held[1]:
            return None
        return held[0]

    def _prune(self, now: float) -> None:
        for nonce in [n for n, (_, expires) in self._held.items() if now > expires]:
            del self._held[nonce]


_memory = _InMemoryVerifiers()


def _key(nonce: str) -> str:
    return f"{_KEY_PREFIX}{nonce}"


def _memory_allowed() -> bool:
    from ..settings import get_settings

    if get_settings().is_development:
        return True
    # get_redis_client() answers None both when USE_REDIS is off and when a
    # configured Redis can't be reached, so the log names both.
    logger.error("Connecting a calendar needs Redis; it is off (USE_REDIS) or unreachable")
    return False


def remember_verifier(nonce: str, verifier: str) -> None:
    """Hold ``verifier`` for the exchange that follows this authorization."""
    redis = get_redis_client()
    if redis is None:
        if not _memory_allowed():
            raise PkceStoreDownError
        _memory.put(nonce, verifier)
        return
    try:
        redis.setex(_key(nonce), TTL_SECONDS, verifier)
    except Exception as exc:
        raise PkceStoreDownError from exc


def take_verifier(nonce: str) -> str:
    """Return the verifier for this round trip and delete it.

    Raises ``PkceStoreUnavailableError`` when there is nothing to return —
    an expired round trip, a replayed state, or a store that lost the value.
    The exchange must not proceed without it: Google would reject the code
    anyway, and a caller that fell back to no verifier would be quietly
    downgrading the flow.
    """
    redis = get_redis_client()
    if redis is None:
        if not _memory_allowed():
            raise PkceStoreDownError
        verifier: object = _memory.take(nonce)
    else:
        try:
            verifier = redis.getdel(_key(nonce))
        except Exception as exc:
            raise PkceStoreDownError from exc

    if not verifier:
        # No user-identifying detail: the nonce is random and unlinked.
        logger.warning("PKCE verifier missing for an OAuth callback; state expired or reused")
        raise PkceStoreUnavailableError("no PKCE verifier for this authorization")
    return str(verifier)
