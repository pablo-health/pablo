# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Signed ``state`` for a calendar provider's OAuth round trip.

The authorization request goes out with a ``state`` value; the provider
hands the same value back with the authorization code. Ours carries the
user it was minted for and when, under an HMAC, so the callback can
require that the code arriving belongs to the person whose session is
about to spend it. A value the caller did not mint — or minted for
somebody else, or ten minutes ago — is not accepted.

Stateless by design: everything needed to check a value is inside it, so
nothing has to be stored between the two halves of the round trip.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets

from ..utcnow import utc_now

MAX_STATE_AGE_SECONDS = 600
"""How long a minted value stays acceptable. Long enough to sign in and
work through a consent screen, short enough to bound replay."""

_CLOCK_SKEW_SECONDS = 60
"""Tolerance for a value that appears to have been minted slightly in the
future, which is a clock difference rather than a forgery."""

_NONCE_BYTES = 16


class OAuthStateError(ValueError):
    """The state returned with an authorization code is not one we minted."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(key: bytes, body: str) -> str:
    return _b64encode(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())


def mint_state(key: bytes, user_id: str) -> str:
    """Mint a state value binding this authorization request to one user.

    The nonce makes two requests from the same user in the same second
    distinguishable, so a value cannot be guessed from its inputs.
    """
    payload = {
        "u": user_id,
        "n": secrets.token_urlsafe(_NONCE_BYTES),
        "t": int(utc_now().timestamp()),
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{body}.{_sign(key, body)}"


def verify_state(
    key: bytes,
    state: str,
    user_id: str,
    *,
    max_age_seconds: int = MAX_STATE_AGE_SECONDS,
) -> None:
    """Accept a state value, or raise OAuthStateError saying nothing useful.

    Raises before the caller has done anything with the authorization code
    that came alongside it.
    """
    if not state:
        raise OAuthStateError("missing state")

    body, _, signature = state.partition(".")
    if not body or not signature:
        raise OAuthStateError("malformed state")
    if not hmac.compare_digest(signature, _sign(key, body)):
        raise OAuthStateError("state signature does not verify")

    try:
        payload = json.loads(_b64decode(body))
        bound_user = str(payload["u"])
        issued_at = int(payload["t"])
    except Exception as exc:
        raise OAuthStateError("unreadable state") from exc

    if not hmac.compare_digest(bound_user, user_id):
        raise OAuthStateError("state was minted for a different user")

    age = int(utc_now().timestamp()) - issued_at
    if age > max_age_seconds or age < -_CLOCK_SKEW_SECONDS:
        raise OAuthStateError("state has expired")
