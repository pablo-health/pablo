# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What the tiered intake asks, of whom, and where the answers land. No database.

The question set is a design decision expressed as data, and the things most
easily broken by a well-meaning edit are the ones the design is load-bearing
about: that Tier 0 never asks, that Tier 1 can be finished and left, that the
supervision fork actually changes the question set, and that every answer has a
column to land in. Each is asserted here rather than remembered.
"""

from __future__ import annotations

from app.credentialing import intake
from app.credentialing.field_map import FIELD_MAP_PATH, render
from app.credentialing.intake import (
    INTAKE_FIELDS,
    Applicability,
    CaqhSection,
    FieldKind,
    Tier,
)
from app.db.models import Base

#: The ordinary applicant: independently licensed, does not prescribe. The
#: counts in the design are quoted for her, so the tests quote them for her too.
_ORDINARY: dict[str, bool] = {"supervised": False, "prescriber": False}


def _for(tier: Tier, **who: bool) -> tuple[intake.IntakeField, ...]:
    return intake.applicable(intake.fields_for_tier(tier), **(who or _ORDINARY))


def _questions(fields: tuple[intake.IntakeField, ...]) -> tuple[intake.IntakeField, ...]:
    return tuple(f for f in fields if f.kind is not FieldKind.UPLOAD)


def _uploads(fields: tuple[intake.IntakeField, ...]) -> tuple[intake.IntakeField, ...]:
    return tuple(f for f in fields if f.kind is FieldKind.UPLOAD)


class TestTierZeroNeverAsks:
    """Tier 0 presents values; it does not collect them."""

    def test_every_confirm_field_names_where_its_value_came_from(self) -> None:
        """A pre-filled value with no provenance reads as the software guessing.

        ``source`` is what lets the surface say "from the NPPES registry", and a
        Tier-0 field without one has nothing to render but an unexplained value
        in a box — which is the empty-input state this tier exists to avoid.
        """
        unsourced = [f.key for f in _for(Tier.CONFIRM) if f.source is None]
        assert not unsourced, f"Tier 0 fields with no source: {unsourced}"

    def test_no_confirm_field_is_an_upload(self) -> None:
        assert not _uploads(_for(Tier.CONFIRM))

    def test_the_confirmations_are_roughly_the_fourteen_the_design_counts(self) -> None:
        assert len(_for(Tier.CONFIRM)) == 13
        assert len(_for(Tier.CONFIRM, supervised=False, prescriber=True)) == 14


class TestTierOneIsClaimsReadyAndStoppable:
    def test_it_asks_the_eight_questions_plus_the_pulled_forward_one(self) -> None:
        """Eight claims-ready questions, and CAQH ID deliberately early."""
        fields = _for(Tier.CLAIMS_READY)
        assert len(_questions(fields)) == 9
        assert len(_uploads(fields)) == 2

    def test_the_caqh_id_question_is_asked_in_tier_one(self) -> None:
        """Pulled forward on purpose: one field, and it changes the later plan."""
        assert "caqh_id" in {f.key for f in _for(Tier.CLAIMS_READY)}

    def test_finishing_tier_one_and_stopping_is_a_finish(self) -> None:
        """The whole point of the tiering, asserted.

        Answering everything Tier 1 requires and touching nothing in Tier 2
        leaves a clinician the billing pipeline can work with. Nothing derived
        from that state may read as an error or an incomplete record.
        """
        answered = {f.key for f in _for(Tier.CLAIMS_READY) if f.required}
        assert intake.claims_ready(answered)

        by_tier = {c.tier: c for c in intake.completion(answered)}
        assert by_tier[Tier.CLAIMS_READY].complete
        assert not by_tier[Tier.CREDENTIALING].complete
        assert by_tier[Tier.CREDENTIALING].answered == 0

    def test_progress_is_reported_per_tier_and_never_as_one_number(self) -> None:
        """A single percentage would render Tier-1-and-stop as roughly half done.

        That is the nag the design rules out, so ``completion`` returns one
        entry per tier and there is no combined figure for a caller to reach
        for by accident.
        """
        assert {c.tier for c in intake.completion(set())} == set(Tier)
        assert not hasattr(intake, "overall_completion")

    def test_every_tier_one_field_lands_somewhere_billing_already_needs(self) -> None:
        """Tier 1 is claims-ready data, not credentialing data asked early.

        Each of its targets is a table the billing pipeline reads: the payer
        participation that decides claim-versus-superbill, the bank account
        that receives EFT, the licences and locations a claim is filed under.
        """
        credentialing_only = {
            "credential_education",
            "credential_training",
            "credential_references",
        }
        targets = {f.target.split(".", 1)[0] for f in _for(Tier.CLAIMS_READY)}
        assert not targets & credentialing_only


class TestSupervisionFork:
    def test_selecting_supervised_changes_the_question_set(self) -> None:
        independent = {f.key for f in _for(Tier.CLAIMS_READY)}
        supervised = {f.key for f in _for(Tier.CLAIMS_READY, supervised=True, prescriber=False)}
        assert independent != supervised
        assert "supervisor" in supervised
        assert "supervisor" not in independent

    def test_the_fork_question_itself_is_asked_of_everyone(self) -> None:
        """It cannot be behind the fork it switches."""
        fork = next(f for f in INTAKE_FIELDS if f.key == "supervision_status")
        assert fork.applies_to is Applicability.ALL
        assert fork.tier is Tier.CLAIMS_READY

    def test_the_fork_is_asked_before_anything_it_changes(self) -> None:
        """Ordering, not just membership: branching late means asking twice."""
        tier_one = _for(Tier.CLAIMS_READY, supervised=True, prescriber=False)
        keys = [f.key for f in tier_one]
        forked = [f.key for f in tier_one if f.applies_to is not Applicability.ALL]
        assert forked
        assert all(keys.index("supervision_status") < keys.index(k) for k in forked)

    def test_a_field_for_the_other_branch_is_not_quietly_accepted(self) -> None:
        """``applicable`` narrows rather than merely de-requiring."""
        supervised_only = {
            f.key for f in _for(Tier.CLAIMS_READY, supervised=True, prescriber=False)
        }
        assert "supervisor" not in {f.key for f in _for(Tier.CLAIMS_READY)}
        assert supervised_only - {f.key for f in _for(Tier.CLAIMS_READY)} == {"supervisor"}


class TestTierTwo:
    def test_it_asks_the_fourteen_questions_and_four_uploads(self) -> None:
        fields = _for(Tier.CREDENTIALING)
        assert len(_questions(fields)) == 14
        assert len(_uploads(fields)) == 4

    def test_a_prescriber_is_asked_for_one_more_document(self) -> None:
        prescriber = _for(Tier.CREDENTIALING, supervised=False, prescriber=True)
        assert {f.key for f in _uploads(prescriber)} - {
            f.key for f in _uploads(_for(Tier.CREDENTIALING))
        } == {"dea_certificate"}

    def test_every_disclosure_answer_is_written_through_the_versioning_helper(self) -> None:
        """An unversioned key silently changes what she attested to.

        ``credential_disclosures`` pins an answer to a wording, and only
        ``app.credentialing.disclosures`` sets both halves. A disclosure field
        that wrote a row directly would store a ``true`` whose question can
        later be reworded underneath it.
        """
        disclosures = [f for f in INTAKE_FIELDS if f.target == "credential_disclosures"]
        assert disclosures
        assert all(f.audited_writer == "app.credentialing.disclosures" for f in disclosures)

    def test_the_encrypted_fields_are_written_through_the_audited_path(self) -> None:
        """SSN, DOB, tax id and bank details have exactly one way in."""
        sensitive = {"ssn", "date_of_birth", "tax_id", "bank_account"}
        for field in (f for f in INTAKE_FIELDS if f.key in sensitive):
            assert field.audited_writer == "app.credentialing.government_ids", field.key


class TestTheWholeSet:
    def test_the_ordinary_applicant_answers_about_twenty_two_questions(self) -> None:
        """The promise the design makes out loud, held to a number.

        Tier 0 is excluded because it asks nothing — the design counts its
        fourteen separately, as confirmations.
        """
        everything = intake.applicable(INTAKE_FIELDS, **_ORDINARY)
        asked = tuple(f for f in everything if f.tier is not Tier.CONFIRM)
        assert len(_questions(asked)) == 23
        assert len(_uploads(asked)) == 6

    def test_keys_are_unique(self) -> None:
        keys = [f.key for f in INTAKE_FIELDS]
        assert len(keys) == len(set(keys))

    def test_every_target_resolves_against_the_schema(self) -> None:
        """The guard that makes ``target`` worth trusting.

        A target naming a column that does not exist is a route that will fail
        at write time, and the mapping doc generated from it would document a
        place nothing lands. ``table.column`` is a scalar, a bare ``table`` a
        repeating group.
        """
        tables = Base.metadata.tables
        unresolved = []
        for field in INTAKE_FIELDS:
            if "." in field.target:
                table, column = field.target.split(".", 1)
                ok = table in tables and column in tables[table].c
            else:
                ok = field.target in tables
            if not ok:
                unresolved.append((field.key, field.target))
        assert not unresolved, f"targets with nowhere to land: {unresolved}"

    def test_a_bare_table_target_is_one_that_takes_a_row_per_answer(self) -> None:
        """The ``table`` versus ``table.column`` distinction, held to its meaning.

        A bare table means "one row per answer" — the repeating groups, and the
        disclosures, which are row-per-question so each answer can be pinned to
        the wording it was given. Everything else names the column it lands in,
        which is what lets a route write it without a second lookup.
        """
        for field in INTAKE_FIELDS:
            names_a_column = "." in field.target
            row_per_answer = field.kind is FieldKind.COLLECTION or (
                field.audited_writer == "app.credentialing.disclosures"
            )
            if names_a_column or field.kind is FieldKind.UPLOAD:
                continue
            assert row_per_answer, f"{field.key} targets a bare table but writes no row"


class TestCaqhShape:
    """The record has to export into the portal rather than be translated for it."""

    def test_the_sections_are_the_eleven_the_provider_guide_names_in_its_order(self) -> None:
        """Verbatim from the provider user guide p31, via docs/reference/."""
        assert [s.value for s in CaqhSection] == [
            "personal_information",
            "professional_ids",
            "education_and_professional_training",
            "specialties",
            "practice_locations",
            "hospital_affiliations",
            "credential_contacts",
            "professional_liability_insurance",
            "employment_information",
            "professional_references",
            "disclosure",
        ]

    def test_every_section_the_intake_fills_is_one_of_them(self) -> None:
        assert {f.section for f in INTAKE_FIELDS} <= set(CaqhSection)

    def test_the_committed_field_map_matches_the_question_set(self) -> None:
        """AC 6's field-by-field correspondence, kept honest.

        The doc is generated, so a question set that changed without it is a
        doc that describes a surface nobody ships. Regenerate with
        ``poetry run python backend/scripts/regen_intake_field_map.py``.
        """
        assert FIELD_MAP_PATH.read_text(encoding="utf-8") == render(), (
            "docs/reference/caqh-intake-field-map.md is stale — regenerate it"
        )
