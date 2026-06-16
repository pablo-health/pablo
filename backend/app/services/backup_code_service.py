# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""One-time account-recovery backup codes (Layer 1 of the recovery model).

Per ``docs/security/account-recovery-procedure.md`` and
``authentication-mfa-policy.md`` §6.4: a set of high-entropy codes is issued
at first-passkey enrollment, generated with the stdlib ``secrets`` module,
**hashed at rest** (never stored or logged in plaintext), and **single-use**.
The plaintext is shown to the user exactly once at issuance.

A redeemed code is a *second* factor (combined with a first factor at login),
never a standalone session — see ``passkeys-vs-totp.md`` Residual #4. This
module owns generation/hashing/redemption; wiring into enrollment and the
login second-factor chooser lands in PABLO-gqp / the chooser bead.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from typing import TYPE_CHECKING

from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..repositories.passkey_backup_code import PasskeyBackupCodeRepository

logger = logging.getLogger(__name__)

# Crockford-style alphabet minus visually ambiguous characters (0/O, 1/I/L) so a
# user can read a code off a screen or paper without transcription errors.
_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
_GROUPS = 2
_GROUP_LEN = 5  # 10 chars x log2(30) ~= 49 bits of entropy per code
_DEFAULT_COUNT = 10


def _generate_code() -> str:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(_GROUPS * _GROUP_LEN))
    return "-".join(raw[i : i + _GROUP_LEN] for i in range(0, len(raw), _GROUP_LEN))


def normalize_code(code: str) -> str:
    """Canonicalize user input: uppercase, strip dashes/spaces, alnum only.

    Lets a user type ``abcde fghjk``, ``ABCDE-FGHJK``, etc. and still match.
    """
    return "".join(ch for ch in code.upper() if ch.isalnum())


def hash_code(code: str) -> str:
    """One-way hash of a code's canonical form. Codes are high-entropy, so a
    fast hash is sufficient (same rationale as the WebAuthn challenge store)."""
    return hashlib.sha256(normalize_code(code).encode("utf-8")).hexdigest()


class BackupCodeService:
    def __init__(self, codes: PasskeyBackupCodeRepository) -> None:
        self._codes = codes

    def issue(self, user_id: str, *, count: int = _DEFAULT_COUNT) -> list[str]:
        """Generate a fresh set (replacing any unused prior codes) and return the
        plaintext to show the user **once**. Only hashes are persisted."""
        self._codes.delete_unused(user_id)
        plaintext: list[str] = []
        seen: set[str] = set()
        while len(plaintext) < count:
            code = _generate_code()
            if code not in seen:  # avoid an in-batch duplicate (vanishingly rare)
                seen.add(code)
                plaintext.append(code)
        self._codes.add_codes(user_id, [hash_code(c) for c in plaintext], utc_now())
        logger.info("backup_codes_issued user_id=%s count=%d", user_id, count)
        return plaintext

    def redeem(self, user_id: str, code: str) -> bool:
        """Spend one code. Returns whether it was a valid, unused, owned code."""
        ok = self._codes.consume(user_id, hash_code(code))
        logger.info("backup_code_redeemed user_id=%s ok=%s", user_id, ok)
        return ok

    def remaining(self, user_id: str) -> int:
        """Count of unused codes left (for the manage UI / low-codes nudge)."""
        return self._codes.count_unused(user_id)


def get_backup_code_service() -> BackupCodeService:
    """Wire the service against the request-scoped Postgres session.

    The in-memory repository is for unit tests only; outside development a
    missing DB session is an error (never silently fall back in production).
    """
    from ..settings import get_settings

    codes: PasskeyBackupCodeRepository
    try:
        from ..db import get_db_session

        session = get_db_session()
    except RuntimeError:
        if not get_settings().is_development:
            raise
        from ..repositories.passkey_backup_code import InMemoryPasskeyBackupCodeRepository

        codes = InMemoryPasskeyBackupCodeRepository()
    else:
        from ..repositories.postgres.passkey_backup_code import (
            PostgresPasskeyBackupCodeRepository,
        )

        codes = PostgresPasskeyBackupCodeRepository(session)

    return BackupCodeService(codes)
