# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Repository abstraction for one-time account-recovery backup codes.

Backup codes live in the shared ``platform`` schema (no RLS), so the
``user_id`` filter on every read/write IS the access-control boundary —
identical to ``PasskeyCredentialRepository``. Only SHA-256 hashes are
stored; the plaintext is shown to the user once at issuance and never
persisted. See PABLO-e82 and docs/security/account-recovery-procedure.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from threading import Lock

from ..utcnow import utc_now


class PasskeyBackupCodeRepository(ABC):
    """Abstract store for a user's hashed, single-use backup codes."""

    @abstractmethod
    def add_codes(self, user_id: str, code_hashes: list[str], created_at: datetime) -> None:
        """Insert a freshly-generated set of code hashes for the user."""

    @abstractmethod
    def delete_unused(self, user_id: str) -> None:
        """Remove the user's still-unused codes (used before issuing a new set).

        Spent codes are kept (their ``consumed_at`` is an audit signal); only
        the unused remainder of a prior set is discarded on regeneration.
        """

    @abstractmethod
    def count_unused(self, user_id: str) -> int:
        """How many unused codes the user has left (for the manage UI)."""

    @abstractmethod
    def consume(self, user_id: str, code_hash: str) -> bool:
        """Atomically spend one unused code; return whether it matched.

        Scoped to ``user_id`` and to unused rows so a code is single-use and
        can only be redeemed by its owner. An unknown / already-spent / wrong
        owner hash returns ``False``.
        """


class InMemoryPasskeyBackupCodeRepository(PasskeyBackupCodeRepository):
    """In-memory implementation for tests and development."""

    def __init__(self) -> None:
        # code_hash -> (user_id, created_at, consumed_at)
        self._rows: dict[str, tuple[str, datetime, datetime | None]] = {}
        self._lock = Lock()

    def add_codes(self, user_id: str, code_hashes: list[str], created_at: datetime) -> None:
        with self._lock:
            for code_hash in code_hashes:
                self._rows[code_hash] = (user_id, created_at, None)

    def delete_unused(self, user_id: str) -> None:
        with self._lock:
            for code_hash in [
                h
                for h, (uid, _created, consumed) in self._rows.items()
                if uid == user_id and consumed is None
            ]:
                del self._rows[code_hash]

    def count_unused(self, user_id: str) -> int:
        with self._lock:
            return sum(
                1
                for uid, _created, consumed in self._rows.values()
                if uid == user_id and consumed is None
            )

    def consume(self, user_id: str, code_hash: str) -> bool:
        with self._lock:
            row = self._rows.get(code_hash)
            if row is None or row[0] != user_id or row[2] is not None:
                return False
            self._rows[code_hash] = (user_id, row[1], utc_now())
            return True
