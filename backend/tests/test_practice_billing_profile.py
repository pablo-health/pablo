# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice's billing identity: the legal entity a claim is filed as.

Bug classes covered:
  * an unconfigured practice's read creating a row;
  * the tax id round-tripping in the clear anywhere in the response;
  * a partial update clobbering fields the caller never mentioned;
  * an update that omits `tax_id` overwriting the previously encrypted value.
"""

from __future__ import annotations

import base64
import os
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from app.db.models import PracticeBillingProfileRow
from app.services.practice_billing_profile import (
    load_billing_profile,
    load_billing_tax_id,
    update_billing_profile,
)
from app.services.token_encryption import decrypt_tokens
from app.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """The tax id is encrypted with the same helper as OAuth calendar tokens."""
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _empty_session() -> Any:
    """A session holding no billing profile row, as a fresh practice would."""
    session = MagicMock()
    session.get.return_value = None
    return session


class TestAnUnconfiguredPracticeReadsAsEmpty:
    def test_every_field_is_none(self) -> None:
        profile = load_billing_profile(_empty_session())

        assert profile["legal_name"] is None
        assert profile["tax_id_last4"] is None
        assert profile["billing_npi"] is None

    def test_reading_does_not_create_a_row(self) -> None:
        session = _empty_session()

        load_billing_profile(session)

        session.add.assert_not_called()


class TestSavingATaxId:
    def test_response_carries_last4_only(self) -> None:
        merged = update_billing_profile(_empty_session(), {"tax_id": "12-3456789"})

        assert merged["tax_id_last4"] == "6789"
        assert "tax_id" not in merged
        assert "tax_id_encrypted" not in merged

    def test_stored_value_is_encrypted_not_plaintext(self) -> None:
        session = _empty_session()

        update_billing_profile(session, {"tax_id": "12-3456789"})

        row = session.add.call_args[0][0]
        assert row.tax_id_encrypted is not None
        assert "3456789" not in row.tax_id_encrypted
        assert decrypt_tokens(row.tax_id_encrypted) == {"tax_id": "12-3456789"}

    def test_omitting_tax_id_leaves_the_encrypted_value_untouched(self) -> None:
        existing = PracticeBillingProfileRow(
            id=1,
            legal_name="Old Name",
            tax_id_encrypted="unchanged-ciphertext",
            tax_id_last4="6789",
        )
        session = MagicMock()
        session.get.return_value = existing

        merged = update_billing_profile(session, {"legal_name": "New Name"})

        assert existing.tax_id_encrypted == "unchanged-ciphertext"
        assert merged["tax_id_last4"] == "6789"
        assert merged["legal_name"] == "New Name"


class TestPartialUpdatePreservesOtherFields:
    def test_updating_address_does_not_clear_legal_name(self) -> None:
        existing = PracticeBillingProfileRow(id=1, legal_name="Acme Therapy", city="Springfield")
        session = MagicMock()
        session.get.return_value = existing

        merged = update_billing_profile(session, {"city": "Shelbyville"})

        assert merged["legal_name"] == "Acme Therapy"
        assert merged["city"] == "Shelbyville"


class TestReadingTheFullTaxId:
    """The one reader of the encrypted value, for the documents that need it whole."""

    def test_an_unconfigured_practice_has_none(self) -> None:
        assert load_billing_tax_id(_empty_session()) is None

    def test_a_row_without_a_tax_id_has_none(self) -> None:
        session = MagicMock()
        session.get.return_value = PracticeBillingProfileRow(id=1, legal_name="Acme Therapy")

        assert load_billing_tax_id(session) is None

    def test_the_stored_value_round_trips_whole(self) -> None:
        session = _empty_session()
        update_billing_profile(session, {"tax_id": "12-3456789"})
        stored = session.add.call_args[0][0]
        session.get.return_value = stored

        assert load_billing_tax_id(session) == "12-3456789"
