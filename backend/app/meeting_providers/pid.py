# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The opaque handle a room URL carries instead of an appointment id.

A doxy.me room URL is given to the patient, travels through their mail or
their messages, and lands in the vendor's access log. Anything in it is
effectively public, so what it carries has to be a value that says nothing
and opens nothing: not the appointment id, not the patient id, not a
truncation of either.

So the handle is an HMAC over the appointment id under a key derived from the
deployment's own secret, truncated to a length that is still far too wide to
walk. It is one-way — holding a handle tells you nothing about the
appointment — and it is stable, so the same appointment always produces the
same handle and a vendor callback can be matched to it.

Matching is a lookup, not a computation: the handle is stored on the
appointment when the room is made, and a callback finds the row by it. This
module is what produces the value and what proves, in a test, that it is not
the id wearing a hat.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from ..services.token_encryption import derive_subkey

_PURPOSE = "telehealth-room-handle"

HANDLE_BYTES = 12
"""96 bits of the digest. Wide enough that guessing one is not a strategy,
short enough that the URL a patient is sent still looks like a URL."""


def _key() -> bytes:
    return derive_subkey(_PURPOSE)


def room_handle(appointment_id: str) -> str:
    """The handle for one appointment. Same input, same output, always."""
    digest = hmac.new(_key(), appointment_id.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:HANDLE_BYTES]).rstrip(b"=").decode("ascii")


def handle_matches(appointment_id: str, handle: str) -> bool:
    """Whether a handle is the one this appointment would produce.

    Constant-time, because it is checked against a value that arrived from
    outside. Used to confirm a match rather than to find one — finding is the
    indexed lookup on the stored handle.
    """
    return hmac.compare_digest(room_handle(appointment_id), handle)
