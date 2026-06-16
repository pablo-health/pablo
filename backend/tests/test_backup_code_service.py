# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Backup-code generation, hashing, and single-use redemption (PABLO-e82).

Verifies the security-critical invariants from authentication-mfa-policy.md
§6.4 / the recovery procedure: codes are high-entropy, only hashes are
stored, redemption is single-use and owner-scoped, and regeneration replaces
the unused set without resurrecting spent codes.
"""

from __future__ import annotations

from app.repositories.passkey_backup_code import InMemoryPasskeyBackupCodeRepository
from app.services.backup_code_service import (
    BackupCodeService,
    hash_code,
    normalize_code,
)

USER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


def _service() -> tuple[BackupCodeService, InMemoryPasskeyBackupCodeRepository]:
    repo = InMemoryPasskeyBackupCodeRepository()
    return BackupCodeService(repo), repo


class TestIssue:
    def test_issues_distinct_plaintext_codes(self) -> None:
        service, _repo = _service()
        codes = service.issue(USER, count=10)
        assert len(codes) == 10
        assert len(set(codes)) == 10  # all distinct
        assert service.remaining(USER) == 10

    def test_only_hashes_are_stored_never_plaintext(self) -> None:
        service, repo = _service()
        codes = service.issue(USER, count=3)
        stored = set(repo._rows.keys())
        # No plaintext (or its normalized form) appears in storage; only hashes.
        for code in codes:
            assert code not in stored
            assert normalize_code(code) not in stored
            assert hash_code(code) in stored

    def test_regenerate_replaces_unused_set(self) -> None:
        service, _repo = _service()
        first = service.issue(USER, count=10)
        second = service.issue(USER, count=10)
        assert service.remaining(USER) == 10  # not 20 — old unused were dropped
        # An old (now-revoked) code no longer works; a new one does.
        assert service.redeem(USER, first[0]) is False
        assert service.redeem(USER, second[0]) is True


class TestRedeem:
    def test_valid_code_is_single_use(self) -> None:
        service, _repo = _service()
        codes = service.issue(USER, count=5)
        assert service.redeem(USER, codes[0]) is True
        assert service.redeem(USER, codes[0]) is False  # already spent
        assert service.remaining(USER) == 4

    def test_redeem_is_owner_scoped(self) -> None:
        service, _repo = _service()
        codes = service.issue(USER, count=5)
        assert service.redeem(OTHER, codes[0]) is False
        assert service.remaining(USER) == 5  # untouched

    def test_redeem_accepts_normalized_variants(self) -> None:
        service, _repo = _service()
        codes = service.issue(USER, count=5)
        # User retypes it lowercase, without the dash, with stray spaces.
        messy = "  " + codes[0].lower().replace("-", " ") + "  "
        assert service.redeem(USER, messy) is True

    def test_unknown_code_is_rejected(self) -> None:
        service, _repo = _service()
        service.issue(USER, count=5)
        assert service.redeem(USER, "ZZZZZ-ZZZZZ") is False


class TestHelpers:
    def test_normalize_strips_and_uppercases(self) -> None:
        assert normalize_code("ab cde-fghjk") == "ABCDEFGHJK"

    def test_hash_is_stable_and_normalization_insensitive(self) -> None:
        assert hash_code("ABCDE-FGHJK") == hash_code("abcde fghjk")
        assert len(hash_code("ABCDE-FGHJK")) == 64  # sha256 hex
