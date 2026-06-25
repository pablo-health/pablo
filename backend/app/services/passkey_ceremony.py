# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pure decoders for the WebAuthn client credential payload.

No I/O and no state — these read the JSON the browser returns from a
ceremony and normalise the fields the service persists. Kept separate from
the orchestration so the parsing rules are testable in isolation.
"""

from __future__ import annotations

import json
from typing import Any

from webauthn.helpers import base64url_to_bytes

from .passkey_errors import PasskeyCeremonyError

_ZERO_AAGUID = "00000000-0000-0000-0000-000000000000"


def extract_challenge(credential: dict[str, Any]) -> bytes:
    """Recover the challenge bytes from the response's signed clientDataJSON.

    The challenge is the single-use lookup key: hashing it and consuming the
    matching row proves we issued it (SHA-256 preimage resistance is why the
    client returning the challenge is safe).
    """
    try:
        client_data = json.loads(base64url_to_bytes(credential["response"]["clientDataJSON"]))
        return base64url_to_bytes(client_data["challenge"])
    except (KeyError, TypeError, ValueError) as err:
        raise PasskeyCeremonyError("clientDataJSON") from err


def read_transports(credential: dict[str, Any]) -> list[str] | None:
    response = credential.get("response")
    transports = response.get("transports") if isinstance(response, dict) else None
    if isinstance(transports, list) and all(isinstance(t, str) for t in transports):
        return transports or None
    return None


def normalize_aaguid(aaguid: str | None) -> str | None:
    # All-zero AAGUID is the privacy-preserving sentinel → store NULL.
    if not aaguid or aaguid == _ZERO_AAGUID:
        return None
    return aaguid
