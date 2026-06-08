# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for prescribing-encounter finalization + addendum verification.

The finalize gate, signature stamping, and the digest-verification helpers are
exercised here with in-memory rows (``finalize_encounter`` only ``flush()``es,
so a stub session suffices). The DB-touching ``append_addendum`` path — which
queries the live chain — is covered against real Postgres in
``tests_integration/database/test_prescribing_finalization_db.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.db.models import (
    PrescribingChecklistItemRow,
    PrescribingEncounterAddendumRow,
    PrescribingEncounterRow,
    PrescriptionRow,
)
from app.prescribing.finalization import (
    FinalizationBlockedError,
    MissingAttestationError,
    _addendum_content,
    finalize_encounter,
    set_clinical_reasoning,
    verify_addenda_chain,
    verify_encounter_integrity,
)
from app.prescribing.integrity import EncounterImmutableError, chain_digest, content_digest


class _StubSession:
    """A session whose only used method is the no-op ``flush``."""

    def flush(self) -> None:
        return None


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _encounter(**overrides: object) -> PrescribingEncounterRow:
    base: dict[str, object] = {
        "id": "enc-1",
        "patient_id": "pat-1",
        "prescriber_user_id": "u-1",
        "prescriber_type": "prescriber",
        "prescriber_dea": "BX1234567",
        "state": "MI",
        "modality": "audio_video",
        "prior_in_person": False,
        "status": "open",
    }
    base.update(overrides)
    return PrescribingEncounterRow(**base)


def _rx(**overrides: object) -> PrescriptionRow:
    base: dict[str, object] = {
        "id": "rx-1",
        "encounter_id": "enc-1",
        "patient_id": "pat-1",
        "schedule": "II",
        "drug_class": "stimulant",
        "quantity": 30,
        "days_supply": 30,
        "refills": 0,
    }
    base.update(overrides)
    return PrescriptionRow(**base)


def _item(item_id: str, status: str, flag_behavior: str) -> PrescribingChecklistItemRow:
    return PrescribingChecklistItemRow(
        id=f"item-{item_id}",
        encounter_id="enc-1",
        patient_id="pat-1",
        item_id=item_id,
        requirement_level="required",
        flag_behavior=flag_behavior,
        status=status,
        ruleset_version="MI-RX-2026.06",
    )


# --------------------------------------------------------------------------
# finalize_encounter
# --------------------------------------------------------------------------


def test_finalize_blocked_by_missing_hard_stop() -> None:
    encounter = _encounter()
    checklist = [
        _item("mi_dual_dea", "missing", "hard_stop"),
        _item("mi_soft", "missing", "soft_warn"),
    ]
    with pytest.raises(FinalizationBlockedError) as exc:
        finalize_encounter(
            _StubSession(),  # type: ignore[arg-type]
            encounter,
            [_rx()],
            checklist,
            signed_by="u-1",
            attestation_statement="I attest.",
            now=_now(),
        )
    # The offending hard-stop item is named; the soft warning is not blocking.
    assert exc.value.item_ids == ["mi_dual_dea"]
    assert encounter.status == "open"  # unchanged — nothing was signed


def test_finalize_requires_attestation_statement() -> None:
    with pytest.raises(MissingAttestationError):
        finalize_encounter(
            _StubSession(),  # type: ignore[arg-type]
            _encounter(),
            [_rx()],
            [_item("mi_dual_dea", "satisfied", "hard_stop")],
            signed_by="u-1",
            attestation_statement="   ",  # blank
            now=_now(),
        )


def test_finalize_on_closed_encounter_raises() -> None:
    with pytest.raises(EncounterImmutableError):
        finalize_encounter(
            _StubSession(),  # type: ignore[arg-type]
            _encounter(status="finalized"),
            [_rx()],
            [],
            signed_by="u-1",
            attestation_statement="I attest.",
            now=_now(),
        )


def test_finalize_signs_and_stamps_digest() -> None:
    encounter = _encounter()
    now = _now()
    checklist = [
        _item("mi_dual_dea", "satisfied", "hard_stop"),
        _item("mi_soft", "missing", "soft_warn"),  # soft warning does not block
    ]
    result = finalize_encounter(
        _StubSession(),  # type: ignore[arg-type]
        encounter,
        [_rx()],
        checklist,
        signed_by="u-1",
        attestation_statement="  I reviewed and attest.  ",
        now=now,
    )
    assert result.status == "finalized"
    assert result.finalized_by == "u-1"
    assert result.finalized_at == now
    assert result.attestation_statement == "I reviewed and attest."  # trimmed
    assert result.integrity_digest is not None
    assert len(result.integrity_digest) == 64


# --------------------------------------------------------------------------
# set_clinical_reasoning
# --------------------------------------------------------------------------


def test_set_clinical_reasoning_on_open_then_clear() -> None:
    encounter = _encounter()
    set_clinical_reasoning(
        _StubSession(), encounter, "  Stimulant chosen after SSRI trial.  ", now=_now()
    )  # type: ignore[arg-type]
    assert encounter.clinical_reasoning == "Stimulant chosen after SSRI trial."  # trimmed
    # Blank clears it back to None.
    set_clinical_reasoning(_StubSession(), encounter, "   ", now=_now())  # type: ignore[arg-type]
    assert encounter.clinical_reasoning is None


def test_set_clinical_reasoning_on_closed_raises() -> None:
    with pytest.raises(EncounterImmutableError):
        set_clinical_reasoning(
            _StubSession(),  # type: ignore[arg-type]
            _encounter(status="finalized"),
            "too late",
            now=_now(),
        )


def test_finalize_digest_covers_reasoning() -> None:
    encounter = _encounter()
    rxs = [_rx()]
    checklist = [_item("mi_dual_dea", "satisfied", "hard_stop")]
    set_clinical_reasoning(_StubSession(), encounter, "Reviewed prior trials.", now=_now())  # type: ignore[arg-type]
    finalize_encounter(
        _StubSession(),  # type: ignore[arg-type]
        encounter,
        rxs,
        checklist,
        signed_by="u-1",
        attestation_statement="I attest.",
        now=_now(),
    )
    assert verify_encounter_integrity(encounter, rxs, checklist) is True
    # Editing the (frozen) reasoning after signing breaks the digest.
    encounter.clinical_reasoning = "Reworded reasoning."
    assert verify_encounter_integrity(encounter, rxs, checklist) is False


# --------------------------------------------------------------------------
# verify_encounter_integrity
# --------------------------------------------------------------------------


def test_verify_encounter_integrity_true_then_tamper_detected() -> None:
    encounter = _encounter()
    rxs = [_rx()]
    checklist = [_item("mi_dual_dea", "satisfied", "hard_stop")]
    finalize_encounter(
        _StubSession(),  # type: ignore[arg-type]
        encounter,
        rxs,
        checklist,
        signed_by="u-1",
        attestation_statement="I attest.",
        now=_now(),
    )
    assert verify_encounter_integrity(encounter, rxs, checklist) is True

    # An after-the-fact edit to the frozen record no longer matches the digest.
    rxs[0].days_supply = 60
    assert verify_encounter_integrity(encounter, rxs, checklist) is False


def test_verify_encounter_integrity_false_when_not_finalized() -> None:
    encounter = _encounter()  # no integrity_digest
    assert verify_encounter_integrity(encounter, [_rx()], []) is False


# --------------------------------------------------------------------------
# verify_addenda_chain
# --------------------------------------------------------------------------


def _addendum(
    encounter_id: str, prev: str | None, *, label: str, text: str, author: str, created_at: datetime
) -> PrescribingEncounterAddendumRow:
    entry_digest = content_digest(
        _addendum_content(
            encounter_id, label=label, text=text, author=author, created_at=created_at
        )
    )
    return PrescribingEncounterAddendumRow(
        id=f"add-{label}",
        encounter_id=encounter_id,
        patient_id="pat-1",
        label=label,
        text=text,
        digest=chain_digest(prev, entry_digest),
        prev_digest=prev,
        created_by=author,
        created_at=created_at,
    )


def test_verify_addenda_chain_intact() -> None:
    encounter = _encounter(integrity_digest="a" * 64)
    now = _now()
    a1 = _addendum(
        encounter.id,
        encounter.integrity_digest,
        label="typo",
        text="dose was 10mg",
        author="u-1",
        created_at=now,
    )
    a2 = _addendum(
        encounter.id,
        a1.digest,
        label="clarify",
        text="indication ADHD",
        author="u-1",
        created_at=now,
    )
    assert verify_addenda_chain(encounter, [a1, a2]) is True
    # No addenda is trivially intact.
    assert verify_addenda_chain(encounter, []) is True


def test_verify_addenda_chain_detects_edited_text() -> None:
    encounter = _encounter(integrity_digest="a" * 64)
    now = _now()
    a1 = _addendum(
        encounter.id,
        encounter.integrity_digest,
        label="typo",
        text="dose was 10mg",
        author="u-1",
        created_at=now,
    )
    a1.text = "dose was 80mg"  # edited after the digest was computed
    assert verify_addenda_chain(encounter, [a1]) is False


def test_verify_addenda_chain_detects_reorder() -> None:
    encounter = _encounter(integrity_digest="a" * 64)
    now = _now()
    a1 = _addendum(
        encounter.id,
        encounter.integrity_digest,
        label="one",
        text="first",
        author="u-1",
        created_at=now,
    )
    a2 = _addendum(
        encounter.id, a1.digest, label="two", text="second", author="u-1", created_at=now
    )
    # Swapped order breaks the prev_digest linkage.
    assert verify_addenda_chain(encounter, [a2, a1]) is False
