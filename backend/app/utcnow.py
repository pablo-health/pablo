# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""UTC timestamp utility."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(UTC)


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string with 'Z' suffix.

    Kept for non-DB usage (API responses, logging). For database columns,
    use utc_now() which returns a proper datetime for TIMESTAMP WITH TIME ZONE.
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
