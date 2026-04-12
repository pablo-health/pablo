# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""UTC timestamp utility.

Single source of truth for ISO 8601 timestamps with Zulu suffix.
Replaces the `datetime.now(UTC).isoformat().replace("+00:00", "Z")` pattern
that was duplicated across the codebase.
"""

from datetime import UTC, datetime


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string with 'Z' suffix."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
