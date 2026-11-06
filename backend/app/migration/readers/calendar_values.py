# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""How the SimplePractice calendar feed writes values: booleans as strings,
times with their offset, lists as JSON text, a client's name as display text.
Pure: no database, no model."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

FIRST_AND_LAST = 2


def flag(value: Any) -> bool:
    """The feed's ``"true"`` / ``"false"`` strings (and a real bool) as bool."""
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() == "true"


def when(value: Any) -> datetime:
    """An ISO-8601 stamp with its offset, kept as the feed wrote it."""
    if not isinstance(value, str):
        raise ValueError("a time is missing")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("a time carries no offset")
    return parsed


def digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def revoked_admin_ids(value: Any) -> tuple[str, ...]:
    """``revokedAdminIds`` arrives as a JSON list serialised into a string."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return ()
    if not isinstance(value, list):
        return ()
    return tuple(str(v) for v in value)


def split_name(display_name: str) -> tuple[str, str]:
    """A display name as first and last name: everything before the last
    word, and the last word."""
    parts = display_name.split()
    if len(parts) < FIRST_AND_LAST:
        return display_name.strip(), ""
    return " ".join(parts[:-1]), parts[-1]


__all__ = ["digest", "flag", "revoked_admin_ids", "split_name", "when"]
