# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for prescribing-encounter integrity primitives + the addenda model."""

from __future__ import annotations

from datetime import date

import pytest
from app.db.models import Base, PrescribingEncounterAddendumRow, PrescribingEncounterRow
from app.prescribing import (
    EncounterImmutableError,
    assert_encounter_mutable,
    chain_digest,
    content_digest,
)

# --------------------------------------------------------------------------
# content_digest
# --------------------------------------------------------------------------


def test_content_digest_is_deterministic_and_order_independent() -> None:
    a = {"schedule": "II", "refills": 0, "days_supply": 30}
    b = {"days_supply": 30, "refills": 0, "schedule": "II"}  # different key order
    assert content_digest(a) == content_digest(b)
    # A SHA-256 hex digest is 64 chars.
    assert len(content_digest(a)) == 64


def test_content_digest_changes_when_content_changes() -> None:
    base = {"schedule": "II", "refills": 0}
    tampered = {"schedule": "II", "refills": 2}
    assert content_digest(base) != content_digest(tampered)


def test_content_digest_handles_non_json_values() -> None:
    # Dates / other non-JSON-native values are stringified, not an error.
    digest = content_digest({"encountered_at": date(2026, 6, 7), "schedule": "II"})
    assert len(digest) == 64


# --------------------------------------------------------------------------
# chain_digest
# --------------------------------------------------------------------------


def test_chain_digest_links_and_is_deterministic() -> None:
    genesis = content_digest({"encounter": 1})
    first = chain_digest(None, genesis)
    again = chain_digest(None, genesis)
    assert first == again
    assert len(first) == 64
    # Linking the next entry onto the chain depends on the prior link.
    second_entry = content_digest({"addendum": 1})
    linked = chain_digest(first, second_entry)
    assert linked != chain_digest(None, second_entry)


def test_chain_digest_breaks_if_a_prior_link_changes() -> None:
    # Reordering / altering history changes every subsequent chain digest.
    e1, e2 = content_digest({"a": 1}), content_digest({"b": 2})
    link1 = chain_digest(None, e1)
    link2 = chain_digest(link1, e2)
    # If e1 were tampered, link1 (and therefore link2) no longer match.
    tampered_link1 = chain_digest(None, content_digest({"a": 999}))
    assert chain_digest(tampered_link1, e2) != link2


# --------------------------------------------------------------------------
# immutability rule
# --------------------------------------------------------------------------


def test_open_encounter_is_mutable() -> None:
    assert_encounter_mutable("open")  # does not raise


@pytest.mark.parametrize("status", ["finalized", "voided"])
def test_closed_encounter_is_immutable(status: str) -> None:
    with pytest.raises(EncounterImmutableError, match="immutable"):
        assert_encounter_mutable(status)


# --------------------------------------------------------------------------
# model contract
# --------------------------------------------------------------------------


def test_encounter_has_integrity_digest_column() -> None:
    assert "integrity_digest" in PrescribingEncounterRow.__table__.columns


def test_addendum_table_is_append_only_and_chained() -> None:
    cols = {c.name for c in PrescribingEncounterAddendumRow.__table__.columns}
    assert {"encounter_id", "patient_id", "label", "text", "digest", "prev_digest"} <= cols
    # Append-only: no mutation / soft-delete columns.
    assert "updated_at" not in cols
    assert "deleted_at" not in cols
    assert "prescribing_encounter_addenda" in Base.metadata.tables


def test_addendum_foreign_keys() -> None:
    targets = {
        fk.target_fullname
        for col in PrescribingEncounterAddendumRow.__table__.columns
        for fk in col.foreign_keys
    }
    assert "prescribing_encounters.id" in targets
    assert "patients.id" in targets
