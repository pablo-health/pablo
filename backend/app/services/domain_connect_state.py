# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The ``state`` a Domain Connect apply link carries, and checking it on return.

The DNS provider sends the practice back with the ``state`` it was given (and
an ``error`` if it made no change). Ours binds the practice, the domain and
the template under an HMAC with an expiry, so the return can be tied to the
link this deployment issued — not to anything the provider, or someone
editing the URL, says. Stateless: nothing is stored between the two halves.

Coming back is not evidence that anything changed. All a valid ``state``
does is let the page run the ordinary DNS check for that practice; the check
decides what is there.

The key is a subkey of the deployment's existing encryption secret
(:func:`app.services.token_encryption.derive_subkey`), separated by purpose.
The value is signed, not encrypted, and travels in URLs: it holds nothing
secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass

from ..utcnow import utc_now

MAX_AGE_SECONDS = 15 * 60
"""Long enough to sign in at a DNS provider and approve the change."""

_NONCE_BYTES = 12


class DomainConnectStateError(ValueError):
    """A returned ``state`` this deployment did not issue for this practice,
    or one that has expired."""


@dataclass(frozen=True)
class ConnectState:
    practice_id: str
    apex: str
    service_id: str


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(key: bytes, body: str) -> str:
    return _b64encode(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())


def mint_state(key: bytes, state: ConnectState) -> str:
    payload = {
        "p": state.practice_id,
        "a": state.apex,
        "s": state.service_id,
        "x": int(utc_now().timestamp()) + MAX_AGE_SECONDS,
        "n": secrets.token_urlsafe(_NONCE_BYTES),
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{_sign(key, body)}"


def verify_state(key: bytes, value: str, practice_id: str) -> ConnectState:
    """What *value* was issued for, if it was issued for *practice_id* and has
    not expired. Raises :class:`DomainConnectStateError` otherwise."""
    body, _, signature = value.partition(".")
    if not body or not signature:
        raise DomainConnectStateError("malformed state")
    if not hmac.compare_digest(signature, _sign(key, body)):
        raise DomainConnectStateError("state signature does not verify")
    try:
        payload = json.loads(_b64decode(body))
        state = ConnectState(
            practice_id=str(payload["p"]), apex=str(payload["a"]), service_id=str(payload["s"])
        )
        expires_at = int(payload["x"])
    except (ValueError, KeyError, TypeError) as e:
        raise DomainConnectStateError("unreadable state") from e
    if not hmac.compare_digest(state.practice_id, practice_id):
        raise DomainConnectStateError("state was issued for another practice")
    if int(utc_now().timestamp()) > expires_at:
        raise DomainConnectStateError("state has expired")
    return state
