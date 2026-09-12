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
        from datetime import date  # noqa: PLC0415

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
        assert found[0].explained is False

    def test_an_explanation_on_the_later_row_marks_the_gap_explained(self) -> None:
        from datetime import date  # noqa: PLC0415

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
        from datetime import date  # noqa: PLC0415

        found = employment.gaps_in(
            [
                employment.Position("practice", date(2019, 1, 1), date(2024, 12, 31)),
                employment.Position("agency", date(2021, 6, 1), date(2022, 5, 31)),
                employment.Position("next", date(2025, 1, 15), None),
            ]
        )
        assert found == []

    def test_an_open_ended_position_ends_the_timeline(self) -> None:
        from datetime import date  # noqa: PLC0415

        found = employment.gaps_in(
            [
                employment.Position("current", date(2015, 1, 1), None),
                employment.Position("side", date(2020, 1, 1), date(2020, 2, 1)),
            ]
        )
        assert found == []
