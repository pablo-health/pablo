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
"""

from __future__ import annotations

import logging

from ..redis_client import get_redis_client

logger = logging.getLogger(__name__)

_KEY_PREFIX = "gcal:pkce:"

TTL_SECONDS = 600
"""Matches ``oauth_state.MAX_STATE_AGE_SECONDS`` — a verifier is useless once
the state it belongs to has aged out, and holding it longer only widens the
window in which it could be read."""


class PkceStoreUnavailableError(RuntimeError):
    """Redis is not reachable, so a verifier can be neither kept nor found.

    Raised rather than degrading to a flow without PKCE. Dropping it silently
    would weaken the exchange with nothing in the logs to say so; connecting a
    calendar is infrequent and the therapist can simply try again.
    """


def _key(nonce: str) -> str:
    return f"{_KEY_PREFIX}{nonce}"


def _client() -> object:
    redis = get_redis_client()
    if redis is None:
        raise PkceStoreUnavailableError("Redis is required to complete an OAuth round trip")
    return redis


def remember_verifier(nonce: str, verifier: str) -> None:
    """Hold ``verifier`` for the exchange that follows this authorization."""
    try:
        _client().setex(_key(nonce), TTL_SECONDS, verifier)  # type: ignore[attr-defined]
    except PkceStoreUnavailableError:
        raise
    except Exception as exc:
        raise PkceStoreUnavailableError("could not store the PKCE verifier") from exc


def take_verifier(nonce: str) -> str:
    """Return the verifier for this round trip and delete it.

    Raises ``PkceStoreUnavailableError`` when there is nothing to return —
    an expired round trip, a replayed state, or a store that lost the value.
    The exchange must not proceed without it: Google would reject the code
    anyway, and a caller that fell back to no verifier would be quietly
    downgrading the flow.
    """
    try:
        verifier = _client().getdel(_key(nonce))  # type: ignore[attr-defined]
    except PkceStoreUnavailableError:
        raise
    except Exception as exc:
        raise PkceStoreUnavailableError("could not read the PKCE verifier") from exc

    if not verifier:
        # No user-identifying detail: the nonce is random and unlinked.
        logger.warning("PKCE verifier missing for an OAuth callback; state expired or reused")
        raise PkceStoreUnavailableError("no PKCE verifier for this authorization")
    return str(verifier)
