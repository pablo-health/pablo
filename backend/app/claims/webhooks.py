# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The clearinghouse's event deliveries: verifying them and reading them.

The vendor signs every delivery the Standard Webhooks way. Three headers:
``webhook-id`` (the delivery's id, stable across the vendor's retries — the
value deliveries are deduped on), ``webhook-timestamp`` (Unix seconds) and
``webhook-signature`` carrying one or more space-separated ``v1,<base64>``
entries. The signature is an HMAC-SHA256 over
``"<webhook-id>.<webhook-timestamp>.<raw body>"`` under the event
destination's secret — the secret's raw bytes, or its base64 decoding when
it carries the scheme's ``whsec_`` prefix — base64-encoded. A delivery
missing any of the three headers, or whose timestamp is outside the
tolerance, is refused before the body is read.

The event body is small: a ``type`` (the vendor's newer deliveries spell
it ``detail-type`` and may suffix a version) — ``transaction.processed``
is the one that matters, ``event.ping`` is the vendor's test — and the
transaction it is about, either as ``resource.id`` or as the transaction
document itself under ``detail``.
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
_VERSION_SUFFIX = ".v"


def candidate_secrets(settings: Settings) -> list[str]:
    """The current and previous destination secrets, whichever are configured."""
    secrets = [
        settings.clearinghouse_webhook_secret.get_secret_value(),
        settings.clearinghouse_webhook_secret_previous.get_secret_value(),
    ]
    return [secret for secret in secrets if secret]


def signing_key(secret: str) -> bytes:
    """The bytes the HMAC runs under: decoded when the secret is ``whsec_``-prefixed."""
    if secret.startswith(_ENCODED_KEY_MARKER):
        with contextlib.suppress(binascii.Error, ValueError):
            return base64.b64decode(secret[len(_ENCODED_KEY_MARKER) :], validate=True)
    return secret.encode()


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
    if not signature_header or not timestamp_header or not message_id or not secrets:
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

    signed = f"{message_id}.{timestamp}.".encode() + body
    for secret in secrets:
        digest = hmac.new(signing_key(secret), signed, hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode()
        if any(hmac.compare_digest(expected, candidate) for candidate in presented):
            return True
    return False


@dataclass(frozen=True)
class WebhookEvent:
    """The parts of a delivery this receiver reads.

    ``id`` is what the delivery is deduped on: the ``webhook-id`` header,
    which the vendor keeps stable across its retries.
    """

    id: str
    type: str
    transaction_id: str | None


def _event_type(payload: dict[str, Any]) -> str:
    raw = str(payload.get("type") or payload.get("detail-type") or "")
    head, marker, version = raw.rpartition(_VERSION_SUFFIX)
    return head if marker and version.isdigit() else raw


def _transaction_id(payload: dict[str, Any]) -> str | None:
    resource = payload.get("resource")
    if isinstance(resource, dict) and resource.get("type") == "transaction":
        return str(resource.get("id") or "") or None
    detail = payload.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("transactionId") or "") or None
    return None


def parse_event(payload: Any, *, delivery_id: str) -> WebhookEvent | None:
    """The event in ``payload``, or ``None`` if it carries no type."""
    if not isinstance(payload, dict):
        return None
    event_type = _event_type(payload)
    if not event_type:
        return None
    return WebhookEvent(id=delivery_id, type=event_type, transaction_id=_transaction_id(payload))
