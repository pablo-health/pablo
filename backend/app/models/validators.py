# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Common validation helpers for models."""

from datetime import datetime

# Phone number validation constants
MIN_PHONE_DIGITS = 10  # Standard US phone number length

# A visit may carry at most this many CPT modifiers.
MAX_VISIT_MODIFIERS = 4


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


def validate_visit_modifiers(value: list[str] | None) -> list[str] | None:
    """Validate the visit's CPT modifier list is within the allowed count.

    Args:
        value: The modifier codes to validate (e.g. ["95", "GT"])

    Returns:
        The validated modifier list

    Raises:
        ValueError: If more than MAX_VISIT_MODIFIERS are supplied
    """
    if value is not None and len(value) > MAX_VISIT_MODIFIERS:
        raise ValueError(f"At most {MAX_VISIT_MODIFIERS} modifiers are allowed")
    return value


def validate_visit_diagnosis_codes(value: list[str] | None) -> list[str] | None:
    """Validate every code against the bundled ICD-10-CM reference file.

    Order is preserved (the first code is the primary diagnosis) — this
    only rejects unknown codes, it never reorders or dedupes.

    Args:
        value: The ordered ICD-10 diagnosis codes to validate

    Returns:
        The validated code list, in the order given

    Raises:
        ValueError: If a code is not in the bundled ICD-10-CM catalog
    """
    if value is None:
        return None
    # Deferred: app.diagnostics.catalog imports app.db.platform_models, which
    # imports app.models.enums -- importing it at module load time would
    # reach back into app.models mid-init and deadlock the import.
    from ..diagnostics.catalog import known_icd10_codes  # noqa: PLC0415

    known = known_icd10_codes()
    for code in value:
        if code not in known:
            raise ValueError(f"Unknown ICD-10 diagnosis code: {code!r}")
    return value
