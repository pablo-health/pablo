# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The clearinghouse's event deliveries: verifying them and reading them.

The vendor signs every delivery the Standard Webhooks way: a
``webhook-signature`` header carrying one or more ``v1,<base64>`` entries,
a ``webhook-timestamp`` (Unix seconds) and a ``webhook-id``. The signature
is an HMAC-SHA256 over ``"<timestamp>.<body>"`` under the event
destination's secret, base64-encoded.

Two details are deliberately covered both ways. The vendor's guide spells
the signed content as ``timestamp.body``; the standard it cites signs
``id.timestamp.body``. And the standard's secrets are ``whsec_``-prefixed
base64 while the guide treats the secret as an opaque string. Every
combination is a legitimate HMAC under the real secret, so
:func:`verify_signature` checks each of them in constant time and accepts
any match — no weaker than one form, and immune to which one the vendor
means this month. A stale timestamp is refused regardless.

The event body is small: an ``id`` (what deliveries are deduped on), a
``type`` — ``transaction.processed`` is the one that matters, ``event.ping``
is the vendor's test — and a ``resource`` naming the transaction to fetch.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..settings import Settings

#: How far out of date a delivery's timestamp may be before it is a replay.
SIGNATURE_TOLERANCE_SECONDS = 300

TRANSACTION_PROCESSED = "transaction.processed"
PING = "event.ping"

_SIGNATURE_VERSION = "v1"
#: The Standard Webhooks marker for a base64-encoded signing key.
_ENCODED_KEY_MARKER = "whsec_"


def candidate_secrets(settings: Settings) -> list[str]:
    """The current and previous destination secrets, whichever are configured."""
    secrets = [
        settings.clearinghouse_webhook_secret.get_secret_value(),
        settings.clearinghouse_webhook_secret_previous.get_secret_value(),
    ]
    return [secret for secret in secrets if secret]


def _keys(secret: str) -> list[bytes]:
    keys = [secret.encode()]
    if secret.startswith(_ENCODED_KEY_MARKER):
        with contextlib.suppress(binascii.Error, ValueError):
            keys.append(base64.b64decode(secret[len(_ENCODED_KEY_MARKER) :], validate=True))
    return keys


def _signed_payloads(body: bytes, timestamp: str, message_id: str | None) -> list[bytes]:
    payloads = [f"{timestamp}.".encode() + body]
    if message_id:
        payloads.append(f"{message_id}.{timestamp}.".encode() + body)
    return payloads


def _presented_signatures(header: str) -> list[str]:
    presented = []
    for entry in header.split():
        version, _, signature = entry.partition(",")
        if version == _SIGNATURE_VERSION and signature:
            presented.append(signature)
    return presented


def verify_signature(  # noqa: PLR0913 — the headers, keyword-only
    body: bytes,
    *,
    signature_header: str | None,
    timestamp_header: str | None,
    message_id: str | None,
    secrets: list[str],
    now: float | None = None,
) -> bool:
    """Constant-time check of a delivery's signature over its raw body.

    ``False`` on a missing or malformed header, a timestamp outside the
    tolerance, no configured secret, or no match.
    """
    if not signature_header or not timestamp_header or not secrets:
        return False
    try:
        timestamp = int(timestamp_header)
    except ValueError:
        return False
    current = time.time() if now is None else now
    if abs(current - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    presented = _presented_signatures(signature_header)
    if not presented:
        return False

    payloads = _signed_payloads(body, str(timestamp), message_id)
    for secret in secrets:
        for key in _keys(secret):
            for payload in payloads:
                expected = base64.b64encode(
                    hmac.new(key, payload, hashlib.sha256).digest()
                ).decode()
                if any(hmac.compare_digest(expected, candidate) for candidate in presented):
                    return True
    return False


@dataclass(frozen=True)
class WebhookEvent:
    """The parts of a delivery this receiver reads."""

    id: str
    type: str
    transaction_id: str | None


def parse_event(payload: Any) -> WebhookEvent | None:
    """The event in ``payload``, or ``None`` if it lacks an id or a type."""
    if not isinstance(payload, dict):
        return None
    event_id = str(payload.get("id") or "")
    event_type = str(payload.get("type") or "")
    if not event_id or not event_type:
        return None
    resource = payload.get("resource")
    transaction_id: str | None = None
    if isinstance(resource, dict) and resource.get("type") == "transaction":
        transaction_id = str(resource.get("id") or "") or None
    return WebhookEvent(id=event_id, type=event_type, transaction_id=transaction_id)
