# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Passkey ceremony failure modes and the HTTP status each maps to.

These live apart from the service so a route handler can translate a
ceremony outcome to a status code without importing the ``py_webauthn`` /
``firebase_admin`` machinery the service drags in.
"""

from __future__ import annotations


class PasskeyEnrollmentError(Exception):
    """Adding another passkey needs an already-MFA-satisfied session."""


class PasskeyCeremonyError(Exception):
    """Malformed / expired / replayed ceremony input → 400."""


class PasskeyAssertionError(Exception):
    """Assertion failed verification or no usable credential → 401."""


class PasskeyLastHardwareKeyError(Exception):
    """Refuse to revoke an admin's only hardware key under enforcement → 409.

    Removing it would lock the admin out of every hardware-gated route. The
    user must enrol a second hardware key first (the >=2-key floor)."""
