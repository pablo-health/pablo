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
    Base,
    PayerRow,
)
from app.db.platform_models import (
    PARTICIPATION_STATUSES,
    PayerParticipationRow,
    PlatformBase,
)

#: The clinician's credential record, which is platform-scoped. Named
#: explicitly rather than derived from a prefix so that renaming one is a
#: decision somebody makes here, not a silent drop in coverage.
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
        "credential_confirmations",
        "credential_service_locations",
        "credential_bank_accounts",
    }
)

#: Her payer relationships, which followed the credential record to platform.
#: Separate from CREDENTIAL_TABLES because the practice_id rule reads
#: differently here: ``payer_participations`` carries one to resolve a payer
#: rather than a document, which is the one place that column has a second job.
PARTICIPATION_TABLES: frozenset[str] = frozenset(
    {
        "payer_authorizations",
        "payer_participations",
        "payer_participation_events",
        "contracted_rates",
    }
)


def _platform_table(name: str):  # type: ignore[no-untyped-def]
    """The platform table of that name, whatever schema key it is filed under."""
    return next(t for t in PlatformBase.metadata.tables.values() if t.name == name)


class TestPlatformScoped:
    """The scoping decision, asserted rather than remembered — and it reversed.

    This file used to assert the opposite, and explained why: the document vault
    and ``clinician_profiles`` are per-tenant, so a platform record forks the
    identity core, and the accepted cost was that a credential record does not
    follow a clinician between practices.

    That reasoning was sound for what it knew. What changed is that Pablo runs
    credentialing as a concierge service: the operator has to read these across
    practices to file an application on somebody's behalf, and held per-tenant
    that is a scan of every schema in the database to answer a question about
    one person. The cost the old decision accepted turned out to be the feature.

    ``panel_applications`` moved first, for the same reason; these are the rest
    of the same surface. The identity core did not in fact fork — what forked is
    the document reference, which loses its foreign key and is tracked to be put
    back (PABLO-g7oe).
    """

    def test_every_table_is_in_the_platform_metadata(self) -> None:
        present = {t.name for t in PlatformBase.metadata.tables.values()}
        missing = sorted(CREDENTIAL_TABLES - present)
        assert not missing, f"not declared on PlatformBase: {missing}"

    def test_none_are_left_in_the_practice_metadata(self) -> None:
        """Two homes for one record is the state worth making impossible."""
        left = sorted(CREDENTIAL_TABLES & set(Base.metadata.tables))
        assert not left, f"still declared per-tenant as well: {left}"

    def test_every_table_carries_user_id(self) -> None:
        """Which is what gives each one its owner policy.

        The row predicate is ``user_id = app.current_user_id``, the same one the
        practice schemas use. A table here without it would be force-RLS'd with
        nothing to match on — a silent deny-all, failing far from its cause.
        """
        without = sorted(
            name
            for name in CREDENTIAL_TABLES
            if "user_id" not in {c.name for c in _platform_table(name).columns}
        )
        assert not without, f"no user_id, so no row policy: {without}"

    def test_practice_id_is_only_on_the_tables_that_point_at_a_document(self) -> None:
        """The column has one job, and only three tables give it one.

        ``practice_id`` says which practice schema resolves a ``document_id``,
        because the vault stayed per-tenant and a platform table cannot
        reference one. A table with no document has nothing to resolve.

        Asserted both ways round on purpose. Adding it everywhere for symmetry
        would put the practice back into the identity of a record whose whole
        argument is that it belongs to the clinician — her degree was not filed
        under a practice, and once she works at two the question has no answer.
        A column with no job invites a query that scopes by it and a reader who
        believes that scoping means something.
        """
        for name in sorted(CREDENTIAL_TABLES):
            columns = {c.name for c in _platform_table(name).columns}
            has_document = "document_id" in columns
            has_practice = "practice_id" in columns
            assert has_practice == has_document, (
                f"{name}: document_id={has_document} but practice_id={has_practice}. "
                "The column exists to resolve a document and for nothing else — "
                "carry both or neither."
            )


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
    def test_a_document_reference_carries_no_foreign_key(self) -> None:
        """Not an oversight — a platform table cannot reference a per-tenant one.

        ``compliance_documents`` stayed behind, so the constraint had to go and
        the column stayed. This asserts the absence deliberately, because an
        absent foreign key looks exactly like a forgotten one: the next person
        to notice should find this test rather than "fix" it.

        It comes back when the compliance cluster follows (PABLO-g7oe), and this
        test inverts again at that point.
        """
        for name in sorted(CREDENTIAL_TABLES):
            table = _platform_table(name)
            if "document_id" not in {c.name for c in table.columns}:
                continue
            targets = {
                str(fk.column)
                for fk in table.c.document_id.foreign_keys  # type: ignore[attr-defined]
            }
            assert not targets, (
                f"{name}.document_id has a foreign key to {sorted(targets)}; a platform "
                "table cannot reference a per-tenant one, so this cannot work"
            )

    def test_no_table_stores_file_bytes(self) -> None:
        """A credential table references a document; it never holds one."""
        byteish = {
            f"{name}.{column.name}"
            for name in sorted(CREDENTIAL_TABLES)
            for column in _platform_table(name).columns
            if column.type.__class__.__name__ in {"LargeBinary", "BLOB", "BYTEA"}
        }
        assert not byteish, f"file bytes belong in the vault, not here: {sorted(byteish)}"


class TestParticipationShape:
    def test_unique_on_user_practice_and_payer(self) -> None:
        """The practice belongs in the key, and did not used to.

        ``(user_id, payer_id)`` identified one participation per clinician per
        payer while both sides lived in the same practice schema. From
        ``platform`` it identifies nothing: the same insurer is a different
        uuid in every practice's ``payers`` table. Widening it is also the
        truer statement — panel participation is contracted per billing entity,
        so a clinician working in two practices holds two statuses against the
        same insurer, which the old key could not express.
        """
        constraints = {
            tuple(c.name for c in constraint.columns)
            for constraint in PayerParticipationRow.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("user_id", "practice_id", "payer_id") in constraints
        assert ("user_id", "payer_id") not in constraints, (
            "the two-column key would let one practice's row block another's"
        )

    def test_payer_id_carries_no_foreign_key_and_practice_id_resolves_it(self) -> None:
        """``payers`` stays per-tenant, so the reference cannot be enforced.

        It holds the practice's own electronic enrollment state with an
        insurer, which two practices hold differently for the same company. A
        platform table cannot reference a per-tenant one, so the constraint is
        gone and ``practice_id`` is what says which schema to resolve
        ``payer_id`` in — the shape ``panel_applications`` already uses.
        """
        payer_id = PayerParticipationRow.__table__.c.payer_id
        assert not payer_id.foreign_keys, (
            "a platform table cannot reference the per-tenant payers table"
        )
        assert "practice_id" in PayerParticipationRow.__table__.c, (
            "without practice_id, payer_id names a row in no particular schema"
        )

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
        names = {c.name for c in _platform_table("payer_participation_events").columns}
        assert {"user_id", "participation_id"} <= names

    def test_only_the_rate_carries_practice_id_among_the_children(self) -> None:
        """``practice_id`` earns its place by resolving a document, or it goes.

        ``contracted_rates`` points at the fee schedule in the practice's
        vault, so it needs to know which vault. The events point at nothing,
        so they do not carry the column — and a ``practice_id`` with no
        reference to resolve invites a query that scopes by it and a reader who
        believes that scoping means something.
        """
        events = {c.name for c in _platform_table("payer_participation_events").columns}
        rates = {c.name for c in _platform_table("contracted_rates").columns}
        assert "practice_id" not in events
        assert {"practice_id", "source_document_id"} <= rates


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
        names = {c.name for c in _platform_table("credential_employment").columns}
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
