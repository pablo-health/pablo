# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Schema-shape guards for the credential record. No database.

These assert the decisions that are easy to undo by accident six months from
now — a second DEA column, a carve-out column on participation, a document
reference that stores bytes instead of pointing at the vault. Each one is cheap
and runs in the unit suite; the behaviour they protect is proved against real
Postgres in ``tests_integration/database/test_credential_record_db.py``.
"""

from __future__ import annotations

import re
from datetime import date

from app.credentialing import employment
from app.db.models import (
    PARTICIPATION_STATUSES,
    Base,
    PayerParticipationRow,
    PayerRow,
)
from app.db.platform_models import PlatformBase

#: Every table this change introduced. Named explicitly rather than derived from
#: a prefix so that renaming one is a decision somebody makes here, not a silent
#: drop in coverage.
CREDENTIAL_TABLES: frozenset[str] = frozenset(
    {
        "credential_government_ids",
        "credential_licenses",
        "credential_liability_policies",
        "credential_education",
        "credential_training",
        "credential_employment",
        "credential_references",
        "credential_disclosures",
        "credential_service_locations",
        "credential_bank_accounts",
        "payer_participations",
        "payer_participation_events",
    }
)


class TestTenantScoped:
    def test_every_table_is_in_the_practice_metadata(self) -> None:
        missing = sorted(CREDENTIAL_TABLES - set(Base.metadata.tables))
        assert not missing, f"not declared on the practice Base: {missing}"

    def test_no_table_landed_in_the_platform_schema(self) -> None:
        """The scoping decision, asserted rather than remembered.

        An earlier draft of this work put the credential record in the platform
        schema so a credentialing account could exist with no practice. That was
        reversed: the document vault and ``clinician_profiles`` are both
        practice-schema, so a platform record would have forked the identity
        core. The accepted cost is that a credential record does not follow a
        clinician between practices.
        """
        leaked = sorted(CREDENTIAL_TABLES & set(PlatformBase.metadata.tables))
        assert not leaked, f"credential tables must not be platform-scoped: {leaked}"

    def test_every_table_carries_user_id(self) -> None:
        """Which is what gives each one the direct-ownership RLS policy.

        A table here without ``user_id`` but with an ``id`` would be force-RLS'd
        with no policy — a silent deny-all that fails every provisioning test
        far from its cause.
        """
        without = sorted(
            name
            for name in CREDENTIAL_TABLES
            if "user_id" not in {c.name for c in Base.metadata.tables[name].columns}
        )
        assert not without, f"no user_id, so no row policy: {without}"


class TestNoSecondDeaColumn:
    def test_dea_is_only_on_clinician_profiles(self) -> None:
        """THERAPY-g79v.5.4 landed the DEA number. There is one, and one only."""
        found = {
            f"{table_name}.{column.name}"
            for table_name, table in Base.metadata.tables.items()
            for column in table.columns
            if re.search(r"\bdea\b|dea_", column.name, re.IGNORECASE)
        }
        assert found == {"clinician_profiles.dea_number"}, (
            f"expected exactly one DEA column; found {sorted(found)}. "
            "Extend clinician_profiles rather than forking the identity core."
        )


class TestDocumentsLiveInTheVault:
    def test_every_document_reference_is_a_vault_foreign_key(self) -> None:
        for name in sorted(CREDENTIAL_TABLES):
            table = Base.metadata.tables[name]
            if "document_id" not in {c.name for c in table.columns}:
                continue
            targets = {
                str(fk.column)
                for fk in table.c.document_id.foreign_keys  # type: ignore[attr-defined]
            }
            assert targets == {"compliance_documents.id"}, (
                f"{name}.document_id points at {sorted(targets)}, not the existing compliance vault"
            )

    def test_no_table_stores_file_bytes(self) -> None:
        """A credential table references a document; it never holds one."""
        byteish = {
            f"{name}.{column.name}"
            for name in sorted(CREDENTIAL_TABLES)
            for column in Base.metadata.tables[name].columns
            if column.type.__class__.__name__ in {"LargeBinary", "BLOB", "BYTEA"}
        }
        assert not byteish, f"file bytes belong in the vault, not here: {sorted(byteish)}"


class TestParticipationShape:
    def test_unique_on_user_and_payer(self) -> None:
        constraints = {
            tuple(c.name for c in constraint.columns)
            for constraint in PayerParticipationRow.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("user_id", "payer_id") in constraints

    def test_foreign_keys_to_payers(self) -> None:
        targets = {str(fk.column) for fk in PayerParticipationRow.__table__.c.payer_id.foreign_keys}
        assert targets == {"payers.id"}

    def test_no_carve_out_or_state_column_of_its_own(self) -> None:
        """Both are already properties of the payer row; duplicating them drifts.

        A carve-out is its own ``payers`` row with ``is_carveout`` and
        ``carveout_of``; state is a property of the payer (BCBS of Michigan).
        """
        names = {c.name for c in PayerParticipationRow.__table__.columns}
        assert not (names & {"is_carveout", "carveout_of", "carve_out_entity", "state"}), (
            f"participation must not restate the payer's own facts: {sorted(names)}"
        )

    def test_status_set_is_the_full_state_machine(self) -> None:
        assert set(PARTICIPATION_STATUSES) == {
            "out_of_network",
            "application_submitted",
            "credentialed",
            "contracted",
            "in_network",
            "single_case_agreement",
            "denied",
            "terminated",
        }

    def test_events_carry_user_id_for_isolation_without_a_join(self) -> None:
        names = {c.name for c in Base.metadata.tables["payer_participation_events"].columns}
        assert {"user_id", "participation_id"} <= names


class TestTheNameCollisionIsDocumented:
    """AC 12. The one bug these two names will otherwise cause.

    ``payers.enrollment_status`` / ``payer_enrollments`` are EDI transaction
    enrolment; ``payer_participations.status`` is panel participation. Both
    docstrings have to say so, because the next person to read one of them will
    be looking for the other.
    """

    def test_payers_docstring_disclaims_panel_status(self) -> None:
        doc = (PayerRow.__doc__ or "").lower()
        assert "payer_participations" in doc
        assert "panel" in doc

    def test_participation_docstring_disclaims_edi_enrolment(self) -> None:
        doc = (PayerParticipationRow.__doc__ or "").lower()
        assert "payer_enrollments" in doc
        assert "enrollment_status" in doc


class TestEmploymentGapsAreDerived:
    """AC 9. Gaps come from the dates, so they cannot disagree with them."""

    def test_no_stored_gap_flag(self) -> None:
        names = {c.name for c in Base.metadata.tables["credential_employment"].columns}
        assert "has_gap" not in names
        assert "preceding_gap_explanation" in names

    def test_three_positions_with_one_gap(self) -> None:
        found = employment.gaps_in(
            [
                employment.Position("a", date(2018, 1, 1), date(2020, 6, 30)),
                # Eight months uncovered.
                employment.Position("b", date(2021, 3, 1), date(2023, 1, 31)),
                # Two days: under the threshold, so not a gap.
                employment.Position("c", date(2023, 2, 2), None),
            ]
        )
        assert [(g.after_id, g.before_id, g.days) for g in found] == [("a", "b", 244)]
        assert found[0].source is employment.GapSource.BREAK
        assert found[0].explained is False

    def test_an_explanation_on_the_later_row_marks_the_gap_explained(self) -> None:
        found = employment.gaps_in(
            [
                employment.Position("a", date(2018, 1, 1), date(2020, 6, 30)),
                employment.Position(
                    "b",
                    date(2021, 3, 1),
                    None,
                    preceding_gap_explanation="Parental leave.",
                ),
            ]
        )
        assert len(found) == 1
        assert found[0].explained is True

    def test_overlapping_positions_do_not_invent_a_gap(self) -> None:
        """A private practice alongside an agency post is ordinary, not a gap.

        The long row is listed first and the short one starts later and ends
        earlier. Comparing each row against only its predecessor would report a
        gap between the short row's end and nothing at all.
        """
        found = employment.gaps_in(
            [
                employment.Position("practice", date(2019, 1, 1), date(2024, 12, 31)),
                employment.Position("agency", date(2021, 6, 1), date(2022, 5, 31)),
                employment.Position("next", date(2025, 1, 15), None),
            ]
        )
        assert found == []

    def test_an_open_ended_position_ends_the_timeline(self) -> None:
        found = employment.gaps_in(
            [
                employment.Position("current", date(2015, 1, 1), None),
                employment.Position("side", date(2020, 1, 1), date(2020, 2, 1)),
            ]
        )
        assert found == []


class TestTheThresholdIsThreeMonths:
    """Verified against the portal's own guide, p138, not against a paraphrase.

    The first version of this module used 30 days, which is three times too
    strict and asks a clinician to account for breaks nobody enquired about.
    """

    def test_the_default_is_ninety_days(self) -> None:
        assert employment.CAQH_GAP_THRESHOLD_DAYS == 90

    def test_a_sixty_day_break_is_not_a_gap_by_default(self) -> None:
        history = [
            employment.Position("a", date(2024, 1, 1), date(2024, 3, 1)),
            employment.Position("b", date(2024, 4, 30), None),
        ]
        assert employment.gaps_in(history) == []

    def test_the_same_break_is_a_gap_at_a_stricter_threshold(self) -> None:
        """Non-vacuous: the break is real, the default simply does not ask."""
        history = [
            employment.Position("a", date(2024, 1, 1), date(2024, 3, 1)),
            employment.Position("b", date(2024, 4, 30), None),
        ]
        found = employment.gaps_in(history, threshold_days=30)
        assert [g.days for g in found] == [60]

    def test_zero_reports_every_break_however_short(self) -> None:
        """For an organization requiring "all gaps in work history" (p138)."""
        history = [
            employment.Position("a", date(2024, 1, 1), date(2024, 3, 1)),
            employment.Position("b", date(2024, 3, 5), None),
        ]
        assert [g.days for g in employment.gaps_in(history, threshold_days=0)] == [4]


class TestAcademicPeriodsBecomeExplainedGaps:
    """The defect that mattered: we would have asked her to explain her residency.

    The portal creates a gap record for each education and training period and
    pre-fills it "Academic/Training leave" (p136-137). So a training period both
    COVERS the timeline and EMITS its own explained gap.
    """

    #: Fixed so the ten-year window is deterministic rather than dated.
    TODAY = date(2026, 9, 12)

    def _fellowship_history(self) -> tuple[list, list]:
        return (
            [
                employment.Position("before", date(2016, 7, 1), date(2020, 6, 30)),
                employment.Position("after", date(2022, 7, 1), None),
            ],
            [employment.AcademicPeriod("fellowship", date(2020, 7, 1), date(2022, 6, 30))],
        )

    def test_a_fellowship_is_not_an_unexplained_hole(self) -> None:
        positions, academic = self._fellowship_history()
        found = employment.gaps_in(positions, academic, today=self.TODAY)
        assert [g.explained for g in found] == [True]

    def test_the_fellowship_appears_as_a_gap_rather_than_as_nothing(self) -> None:
        """Suppressing it would export a profile missing a record the portal wants."""
        positions, academic = self._fellowship_history()
        found = employment.gaps_in(positions, academic, today=self.TODAY)
        assert len(found) == 1
        gap = found[0]
        assert gap.source is employment.GapSource.ACADEMIC_TRAINING
        assert (gap.start, gap.end) == (date(2020, 7, 1), date(2022, 6, 30))
        assert gap.explanation == employment.ACADEMIC_GAP_EXPLANATION
        assert gap.source_id == "fellowship"

    def test_without_the_academic_period_the_same_history_has_a_real_gap(self) -> None:
        """Non-vacuous: proves the fellowship is what accounts for the period."""
        positions, _ = self._fellowship_history()
        found = employment.gaps_in(positions, [], today=self.TODAY)
        assert [(g.source, g.explained) for g in found] == [(employment.GapSource.BREAK, False)]

    def test_her_own_explanation_beats_the_pre_filled_one(self) -> None:
        positions = [
            employment.Position("before", date(2016, 7, 1), date(2020, 6, 30)),
            employment.Position(
                "after",
                date(2022, 7, 1),
                None,
                preceding_gap_explanation="Fellowship, plus six months' caregiving.",
            ),
        ]
        academic = [employment.AcademicPeriod("fellowship", date(2020, 7, 1), date(2022, 6, 30))]
        found = employment.gaps_in(positions, academic, today=self.TODAY)
        assert found[0].explanation == "Fellowship, plus six months' caregiving."

    def test_a_period_missing_either_date_produces_no_gap(self) -> None:
        """ "if the record includes both Start Date and End Date" (p136)."""
        positions = [employment.Position("only", date(2015, 1, 1), None)]
        assert (
            employment.gaps_in(
                positions,
                [
                    employment.AcademicPeriod("no-end", date(2020, 1, 1), None),
                    employment.AcademicPeriod("no-start", None, date(2022, 1, 1)),
                ],
                today=self.TODAY,
            )
            == []
        )

    def test_the_ten_year_window_excludes_an_older_period(self) -> None:
        positions = [employment.Position("only", date(1990, 1, 1), None)]
        old = [employment.AcademicPeriod("school", date(2011, 9, 1), date(2015, 6, 30))]
        assert employment.gaps_in(positions, old, today=self.TODAY) == []

    def test_the_window_boundary_either_side(self) -> None:
        """Ten years from the current YEAR, so 2016-01-01 is the horizon in 2026."""
        positions = [employment.Position("only", date(1990, 1, 1), None)]
        inside = employment.gaps_in(
            positions,
            [employment.AcademicPeriod("in", date(2012, 1, 1), date(2016, 1, 1))],
            today=self.TODAY,
        )
        outside = employment.gaps_in(
            positions,
            [employment.AcademicPeriod("out", date(2012, 1, 1), date(2015, 12, 31))],
            today=self.TODAY,
        )
        assert [g.source_id for g in inside] == ["in"]
        assert outside == []

    def test_a_true_gap_is_still_reported_alongside_an_academic_one(self) -> None:
        """AC 9. The fix must not have suppressed genuine holes."""
        positions = [
            employment.Position("first", date(2016, 1, 1), date(2018, 1, 1)),
            # Nothing accounts for 2018-2020.
            employment.Position("second", date(2020, 6, 1), date(2020, 8, 31)),
            employment.Position("third", date(2022, 9, 1), None),
        ]
        academic = [employment.AcademicPeriod("residency", date(2020, 9, 1), date(2022, 8, 31))]
        found = employment.gaps_in(positions, academic, today=self.TODAY)
        by_source = [(g.source, g.explained) for g in found]
        assert (employment.GapSource.BREAK, False) in by_source
        assert (employment.GapSource.ACADEMIC_TRAINING, True) in by_source
        unexplained = [g for g in found if not g.explained]
        assert [(g.start, g.end) for g in unexplained] == [(date(2018, 1, 1), date(2020, 6, 1))]
