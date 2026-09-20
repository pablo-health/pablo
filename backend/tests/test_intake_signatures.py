# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The evidence digest and the consent statement it names.

Both are small pure modules, and both are the kind of small pure module
whose value is entirely in not changing. A digest that moves stops matching
every signature taken before it moved; a consent statement that is edited
in place changes what somebody already agreed to. So the tests here are
mostly pins.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.intake.consent_statement import (
    CONSENT_STATEMENTS,
    CURRENT_CONSENT_STATEMENT_VERSION,
    consent_statement,
)
from app.intake.signatures import EVIDENCE_FIELDS, evidence_digest, evidence_of

_SIGNED_AT = datetime(2026, 9, 20, 14, 30, tzinfo=UTC)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "11111111-1111-4111-8111-111111111111",
        "assignment_id": "22222222-2222-4222-8222-222222222222",
        "patient_id": "33333333-3333-4333-8333-333333333333",
        "item_id": "44444444-4444-4444-8444-444444444444",
        "document_version_id": "55555555-5555-4555-8555-555555555555",
        "document_digest": "a" * 64,
        "signer_role": "patient",
        "signer_typed_name": "Ada Lovelace",
        "consent_statement_version": "1",
        "signed_at": _SIGNED_AT,
        "auth_strength": "stepped_up",
        "session_id": "sess-1",
        "ip": "203.0.113.7",
        "user_agent": "Mozilla/5.0",
        "created_at": _SIGNED_AT,
        "superseded_at": None,
    }
    row.update(overrides)
    return row


class TestConsentStatement:
    def test_the_current_version_has_wording(self) -> None:
        assert consent_statement() == CONSENT_STATEMENTS[CURRENT_CONSENT_STATEMENT_VERSION]
        assert consent_statement().strip() != ""

    def test_a_stored_version_reads_back_as_its_own_wording(self) -> None:
        """What makes recording the version worth doing at all."""
        assert consent_statement("1") == CONSENT_STATEMENTS["1"]

    def test_an_unshipped_version_is_a_failure_rather_than_a_guess(self) -> None:
        """Answering with today's wording would report the wrong sentence."""
        with pytest.raises(KeyError):
            consent_statement("99")

    def test_no_statutory_signature_class_is_claimed(self) -> None:
        """The wording says what happened, never what it is worth in law.

        A claim about a signature's legal class depends on where a practice
        operates and on facts this code cannot check, so no version of the
        sentence may make one.
        """
        forbidden = (
            "legally binding",
            "legal signature",
            "wet signature",
            "esign",
            "e-sign",
            "ueta",
            "uniform electronic transactions",
            "notariz",
            "under penalty of perjury",
        )
        for version, wording in CONSENT_STATEMENTS.items():
            lowered = wording.lower()
            for claim in forbidden:
                assert claim not in lowered, f"version {version} claims {claim!r}"


class TestEvidenceDigest:
    def test_the_same_row_always_digests_the_same(self) -> None:
        assert evidence_digest(_row()) == evidence_digest(_row())

    def test_the_digest_is_lowercase_hex_sha256(self) -> None:
        digest = evidence_digest(_row())
        assert len(digest) == 64
        assert digest == digest.lower()
        assert all(char in "0123456789abcdef" for char in digest)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("assignment_id", "99999999-9999-4999-8999-999999999999"),
            ("auth_strength", "single_factor"),
            ("consent_statement_version", "2"),
            ("document_digest", "b" * 64),
            ("document_version_id", "99999999-9999-4999-8999-999999999999"),
            ("ip", "198.51.100.9"),
            ("item_id", "99999999-9999-4999-8999-999999999999"),
            ("patient_id", "99999999-9999-4999-8999-999999999999"),
            ("session_id", "sess-2"),
            ("signed_at", datetime(2026, 9, 20, 14, 31, tzinfo=UTC)),
            ("signer_role", "guardian"),
            ("signer_typed_name", "Ada Lovelace "),
            ("user_agent", "curl/8"),
        ],
    )
    def test_every_evidence_field_moves_the_digest(self, field: str, value: object) -> None:
        """A field that cannot move it is not evidence, whatever the list says."""
        assert evidence_digest(_row(**{field: value})) != evidence_digest(_row())

    def test_a_field_outside_the_list_does_not_move_it(self) -> None:
        """The row's own id and its superseding are not part of the agreement."""
        assert evidence_digest(_row(id="other")) == evidence_digest(_row())
        assert evidence_digest(_row(superseded_at=_SIGNED_AT)) == evidence_digest(_row())

    def test_a_missing_optional_column_digests_as_null(self) -> None:
        """A repository that omits a NULL column and one that stores it agree."""
        without = _row()
        del without["session_id"]
        assert evidence_digest(without) == evidence_digest(_row(session_id=None))

    def test_the_payload_is_strings_and_nulls_only(self) -> None:
        """Nothing is left for a future encoder change to render differently."""
        payload = evidence_of(_row())
        assert set(payload) == set(EVIDENCE_FIELDS)
        assert all(value is None or isinstance(value, str) for value in payload.values())
        assert payload["signed_at"] == _SIGNED_AT.isoformat()

    def test_the_field_list_is_the_one_the_shipped_digest_was_taken_over(self) -> None:
        """A pin, because widening the list breaks every stored digest.

        Adding a column to the table does not add it here: an existing row's
        digest was computed over exactly these fields, so a wider list would
        make every signature already taken fail to recompute.
        """
        assert EVIDENCE_FIELDS == (
            "assignment_id",
            "auth_strength",
            "consent_statement_version",
            "document_digest",
            "document_version_id",
            "ip",
            "item_id",
            "patient_id",
            "session_id",
            "signed_at",
            "signer_role",
            "signer_typed_name",
            "user_agent",
        )
