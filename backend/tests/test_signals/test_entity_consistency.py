# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the entity consistency safety signal."""

import os

os.environ["ENVIRONMENT"] = "development"

import pytest
from app.services.signals.entity_consistency import (
    EntityConsistencySignal,
    _categorize_roles,
    _extract_dosages,
    _extract_dsm_codes,
    _extract_frequencies,
    _extract_medications,
    _extract_person_roles,
)
from app.services.verification_signals import SignalContext, SignalVerdict


@pytest.fixture
def signal() -> EntityConsistencySignal:
    return EntityConsistencySignal()


@pytest.fixture
def ctx() -> SignalContext:
    return SignalContext(claim_key="test.claim")


class TestEntityConsistencyName:
    def test_name(self, signal: EntityConsistencySignal) -> None:
        assert signal.name == "entity_consistency"


class TestEntityConsistencyFAIL:
    """Entity mismatches -> FAIL."""

    def test_dosage_mismatch(self, signal: EntityConsistencySignal, ctx: SignalContext) -> None:
        result = signal.check(
            "Sertraline 20mg prescribed",
            "I'm taking sertraline 40mg",
            ctx,
        )
        assert result.verdict == SignalVerdict.FAIL
        assert result.confidence == 0.1
        assert "dosage" in result.detail.lower()

    def test_medication_mismatch(self, signal: EntityConsistencySignal, ctx: SignalContext) -> None:
        result = signal.check(
            "Started Lexapro last week",
            "I've been on Zoloft for a month",
            ctx,
        )
        assert result.verdict == SignalVerdict.FAIL
        assert result.confidence == 0.1
        assert "medication" in result.detail.lower()

    def test_person_role_mismatch(
        self, signal: EntityConsistencySignal, ctx: SignalContext
    ) -> None:
        result = signal.check(
            "Therapist suggested cognitive restructuring",
            "Client requested to try a new approach",
            ctx,
        )
        assert result.verdict == SignalVerdict.FAIL
        assert result.confidence == 0.1
        assert "person role" in result.detail.lower()

    def test_dsm_code_mismatch(self, signal: EntityConsistencySignal, ctx: SignalContext) -> None:
        result = signal.check(
            "Diagnosis: F41.0 panic disorder",
            "Presenting with F32.1 major depression",
            ctx,
        )
        assert result.verdict == SignalVerdict.FAIL
        assert "DSM" in result.detail

    def test_frequency_mismatch(self, signal: EntityConsistencySignal, ctx: SignalContext) -> None:
        result = signal.check(
            "Taking medication twice daily",
            "I take it once weekly",
            ctx,
        )
        assert result.verdict == SignalVerdict.FAIL
        assert "frequenc" in result.detail.lower()

    def test_multiple_mismatches(self, signal: EntityConsistencySignal, ctx: SignalContext) -> None:
        result = signal.check(
            "Lexapro 20mg daily",
            "Zoloft 50mg weekly",
            ctx,
        )
        assert result.verdict == SignalVerdict.FAIL


class TestEntityConsistencyUNCERTAIN:
    """Matching or absent entities -> UNCERTAIN."""

    def test_matching_entities(self, signal: EntityConsistencySignal, ctx: SignalContext) -> None:
        result = signal.check(
            "Taking sertraline 50mg daily",
            "I take sertraline 50mg every day",
            ctx,
        )
        assert result.verdict == SignalVerdict.UNCERTAIN
        assert result.confidence == 0.5

    def test_no_entities_in_either_text(
        self, signal: EntityConsistencySignal, ctx: SignalContext
    ) -> None:
        result = signal.check(
            "Client reports improved mood",
            "I've been feeling better lately",
            ctx,
        )
        assert result.verdict == SignalVerdict.UNCERTAIN
        assert result.confidence == 0.5

    def test_brand_and_generic_same_med(
        self, signal: EntityConsistencySignal, ctx: SignalContext
    ) -> None:
        result = signal.check(
            "Prescribed Zoloft 100mg",
            "Started sertraline 100mg last week",
            ctx,
        )
        assert result.verdict == SignalVerdict.UNCERTAIN

    def test_entities_only_in_one_text(
        self, signal: EntityConsistencySignal, ctx: SignalContext
    ) -> None:
        result = signal.check(
            "Sertraline 50mg",
            "Feeling better lately",
            ctx,
        )
        assert result.verdict == SignalVerdict.UNCERTAIN


class TestEntityConsistencyNeverPASS:
    """Safety signal must never return PASS."""

    def test_perfect_match_still_uncertain(
        self, signal: EntityConsistencySignal, ctx: SignalContext
    ) -> None:
        result = signal.check(
            "Sertraline 50mg twice daily for F41.0",
            "Taking sertraline 50mg twice daily for F41.0",
            ctx,
        )
        assert result.verdict != SignalVerdict.PASS

    def test_no_entities_never_pass(
        self, signal: EntityConsistencySignal, ctx: SignalContext
    ) -> None:
        result = signal.check("Sweating", "I was sweating", ctx)
        assert result.verdict != SignalVerdict.PASS


class TestMedicationExtraction:
    """Tests for _extract_medications helper."""

    def test_extracts_generic_names(self) -> None:
        meds = _extract_medications("Taking sertraline and lorazepam")
        assert meds == {"sertraline", "lorazepam"}

    def test_normalizes_brand_to_generic(self) -> None:
        meds = _extract_medications("Started Lexapro last week")
        assert meds == {"escitalopram"}

    def test_case_insensitive(self) -> None:
        meds = _extract_medications("ZOLOFT and XANAX")
        assert meds == {"sertraline", "alprazolam"}

    def test_no_medications(self) -> None:
        meds = _extract_medications("Client reports feeling better")
        assert meds == set()


class TestDosageExtraction:
    """Tests for _extract_dosages helper."""

    def test_extracts_mg_dosage(self) -> None:
        dosages = _extract_dosages("Taking 50mg daily")
        assert "50mg" in dosages

    def test_extracts_decimal_dosage(self) -> None:
        dosages = _extract_dosages("Prescribed 0.5mg")
        assert "0.5mg" in dosages

    def test_extracts_mcg(self) -> None:
        dosages = _extract_dosages("Folic acid 400mcg")
        assert "400mcg" in dosages

    def test_no_dosages(self) -> None:
        dosages = _extract_dosages("Feeling much better")
        assert dosages == set()


class TestFrequencyExtraction:
    """Tests for _extract_frequencies helper."""

    def test_extracts_numeric_frequency(self) -> None:
        freqs = _extract_frequencies("3 times a week")
        assert "3/week" in freqs

    def test_extracts_word_frequency(self) -> None:
        freqs = _extract_frequencies("Twice daily dosing")
        assert "2/day" in freqs

    def test_no_frequency(self) -> None:
        freqs = _extract_frequencies("Client arrived on time")
        assert freqs == set()


class TestDSMCodeExtraction:
    """Tests for _extract_dsm_codes helper."""

    def test_extracts_single_code(self) -> None:
        codes = _extract_dsm_codes("Diagnosis F41.0")
        assert "F41.0" in codes

    def test_extracts_multiple_codes(self) -> None:
        codes = _extract_dsm_codes("Comorbid F41.0 and F32.1")
        assert codes == {"F41.0", "F32.1"}

    def test_no_codes(self) -> None:
        codes = _extract_dsm_codes("General anxiety symptoms")
        assert codes == set()


class TestPersonRoleExtraction:
    """Tests for _extract_person_roles and _categorize_roles helpers."""

    def test_extracts_therapist(self) -> None:
        roles = _extract_person_roles("Therapist recommended CBT")
        assert "therapist" in roles

    def test_extracts_client(self) -> None:
        roles = _extract_person_roles("Client expressed concern")
        assert "client" in roles

    def test_categorizes_provider_vs_client(self) -> None:
        assert _categorize_roles({"therapist", "psychiatrist"}) == {"provider"}
        assert _categorize_roles({"client"}) == {"client"}

    def test_both_roles_in_same_text(self) -> None:
        roles = _extract_person_roles("Therapist and client discussed goals")
        categories = _categorize_roles(roles)
        assert categories == {"provider", "client"}
