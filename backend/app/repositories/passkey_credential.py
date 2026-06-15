# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Repository abstraction for the passkey credential registry.

Passkey credentials live in the shared ``platform`` schema (no RLS), so
the ``user_id`` filter on every read IS the access-control boundary —
identical to ``CompanionDeviceRepository``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import datetime
from threading import Lock

from ..models.passkey import PasskeyCredential
from ..utcnow import utc_now


class PasskeyCredentialRepository(ABC):
    """Abstract store for enrolled WebAuthn passkeys."""

    @abstractmethod
    def add(self, credential: PasskeyCredential) -> None:
        """Insert a newly-enrolled credential. ``credential_id`` is the PK."""

    @abstractmethod
    def get_active(self, credential_id: str) -> PasskeyCredential | None:
        """Return a non-revoked credential by id, or None.

        The assertion hot path filters ``revoked_at IS NULL`` here so a
        soft-revoked passkey can never satisfy a login (hardening H12).
        """

    @abstractmethod
    def list_for_user(self, user_id: str) -> list[PasskeyCredential]:
        """Return the user's active (non-revoked) credentials.

        Used for ``excludeCredentials`` (block double-enrolment), the
        enrolment step-up gate (does the user already have a factor?),
        and the manage UI.
        """

    @abstractmethod
    def update_after_assertion(
        self,
        credential_id: str,
        *,
        sign_count: int,
        backup_state: bool,
        last_used_at: datetime | None = None,
    ) -> None:
        """Persist the post-assertion counter, BS flag, and last-used time."""

    @abstractmethod
    def revoke(self, credential_id: str, *, user_id: str) -> bool:
        """Soft-revoke a credential the user owns; return whether one matched.

        Scoped to ``user_id`` so a session can only remove its own factors.
        Already-revoked or unknown credentials return ``False`` (idempotent).
        """


class InMemoryPasskeyCredentialRepository(PasskeyCredentialRepository):
    """In-memory implementation for tests and development."""

    def __init__(self) -> None:
        self._rows: dict[str, PasskeyCredential] = {}
        self._lock = Lock()

    def add(self, credential: PasskeyCredential) -> None:
        with self._lock:
            if credential.credential_id in self._rows:
                raise ValueError(f"credential already enrolled: {credential.credential_id}")
            self._rows[credential.credential_id] = credential

    def get_active(self, credential_id: str) -> PasskeyCredential | None:
        with self._lock:
            row = self._rows.get(credential_id)
        if row is None or row.revoked_at is not None:
            return None
        return row

    def list_for_user(self, user_id: str) -> list[PasskeyCredential]:
        with self._lock:
            return [
                row
                for row in self._rows.values()
                if row.user_id == user_id and row.revoked_at is None
            ]

    def update_after_assertion(
        self,
        credential_id: str,
        *,
        sign_count: int,
        backup_state: bool,
        last_used_at: datetime | None = None,
    ) -> None:
        ts = last_used_at or utc_now()
        with self._lock:
            existing = self._rows.get(credential_id)
            if existing is None or existing.revoked_at is not None:
                return
            self._rows[credential_id] = PasskeyCredential(
                credential_id=existing.credential_id,
                user_id=existing.user_id,
                public_key=existing.public_key,
                sign_count=sign_count,
                transports=existing.transports,
                aaguid=existing.aaguid,
                backup_eligible=existing.backup_eligible,
                backup_state=backup_state,
                device_label=existing.device_label,
                created_at=existing.created_at,
                last_used_at=ts,
                revoked_at=existing.revoked_at,
            )

    def revoke(self, credential_id: str, *, user_id: str) -> bool:
        with self._lock:
            existing = self._rows.get(credential_id)
            if (
                existing is None
                or existing.user_id != user_id
                or existing.revoked_at is not None
            ):
                return False
            self._rows[credential_id] = replace(existing, revoked_at=utc_now())
            return True
