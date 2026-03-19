# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Common validation helpers for models."""

from datetime import datetime

# Phone number validation constants
MIN_PHONE_DIGITS = 10  # Standard US phone number length


def validate_iso_date(value: str | None, field_name: str) -> str | None:
    """
    Validate that a date string is in ISO 8601 format.

    Args:
        value: The date string to validate
        field_name: Name of the field being validated (for error messages)

    Returns:
        The validated date string

    Raises:
        ValueError: If the date is not in ISO 8601 format
    """
    if value is not None and value != "":
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as err:
            raise ValueError(f"{field_name} must be ISO 8601 format") from err
    return value


def validate_email(value: str | None) -> str | None:
    """
    Validate email format if provided.

    Args:
        value: The email string to validate

    Returns:
        The validated email string (stripped) or None

    Raises:
        ValueError: If the email format is invalid
    """
    if value is None or value.strip() == "":
        return None

    email = value.strip()
    if "@" not in email:
        raise ValueError("Invalid email format: missing '@'")

    local, _, domain = email.partition("@")
    if not local or not domain:
        raise ValueError("Invalid email format")

    if "." not in domain:
        raise ValueError("Invalid email format: domain missing '.'")

    return email


def validate_phone(value: str | None) -> str | None:
    """
    Validate phone format if provided.

    Args:
        value: The phone string to validate

    Returns:
        The validated phone string (stripped) or None

    Raises:
        ValueError: If the phone number is too short
    """
    if value is None or value.strip() == "":
        return None

    phone = value.strip()
    digits = "".join(c for c in phone if c.isdigit())

    if len(digits) < MIN_PHONE_DIGITS:
        raise ValueError(f"Phone number must contain at least {MIN_PHONE_DIGITS} digits")

    return phone


def validate_status(value: str) -> str:
    """
    Validate patient status.

    Args:
        value: The status string to validate

    Returns:
        The validated status

    Raises:
        ValueError: If the status is not one of the allowed values
    """
    valid_statuses = ["active", "inactive", "on_hold"]
    if value not in valid_statuses:
        raise ValueError(f"Status must be one of: {', '.join(valid_statuses)}")
    return value
