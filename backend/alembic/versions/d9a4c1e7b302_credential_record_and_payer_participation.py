# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""the clinician's credential record, and her status on each payer's panel

``compliance_items`` already tracks when a licence expires. Nothing recorded
what the licence was, nor any of the biographical sections a payer application
asks for: degrees, training, employment with its gaps, references, disclosure
answers, service locations, where money lands.

Twelve tables in the practice schema, each carrying ``user_id`` so the
standard row-ownership policy applies. ``clinician_profiles`` keeps the
primary identity (licence, DEA, NPI, taxonomy) and these extend it rather than
forking it; documents stay in ``compliance_documents``, referenced by id.

The last two are the panel: ``payer_participations``, a state machine with an
effective date rather than a boolean — because credentialing and contracting
are two processes and finishing the first without the second is a trap that
lasts years — and ``payer_participation_events``, because the current status
cannot answer when a panel went quiet.

Unique on ``(user_id, payer_id)``: in a group practice each clinician holds
her own status against the same payer, which is why this is not a column on
``payers``. The behavioural carve-out needs nothing new — it is already
``payers.is_carveout`` plus ``carveout_of`` — and neither does state.

``payer_participations.status`` is NOT ``payers.enrollment_status`` or
``payer_enrollments``: those are the practice's electronic connection to a
payer (837/835/270), which moves independently.

SSN, date of birth, tax id and the bank numbers are stored AES-256-GCM
encrypted (``app.services.token_encryption``), with only last-four in the
clear. Reads of the encrypted values go through one audited path.

Revision ID: d9a4c1e7b302
Revises: b8f43d2e17c0
Create Date: 2026-09-11
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d9a4c1e7b302"
down_revision: str | Sequence[str] | None = "b8f43d2e17c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PARTICIPATION_STATUSES = (
    "'out_of_network', 'application_submitted', 'credentialed', 'contracted', "
    "'in_network', 'single_case_agreement', 'denied', 'terminated'"
)


def upgrade() -> None:
    op.create_table(
        "credential_government_ids",
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("ssn_encrypted", sa.Text(), nullable=True),
        sa.Column("ssn_last4", sa.String(length=4), nullable=True),
        sa.Column("dob_encrypted", sa.Text(), nullable=True),
        sa.Column("tax_id_type", sa.String(length=3), nullable=True),
        sa.Column("tax_id_encrypted", sa.Text(), nullable=True),
        sa.Column("tax_id_last4", sa.String(length=4), nullable=True),
        sa.Column("type2_npi", sa.String(length=20), nullable=True),
        sa.Column("business_structure", sa.String(length=40), nullable=True),
        sa.Column("sole_proprietor", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name="pk_credential_government_ids"),
        sa.CheckConstraint(
            "tax_id_type IS NULL OR tax_id_type IN ('ein', 'ssn')",
            name="ck_credential_government_ids_tax_id_type",
        ),
    )

    op.create_table(
        "credential_licenses",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("license_type", sa.String(length=50), nullable=False),
        sa.Column("license_number", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=2), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("verification_source", sa.String(length=8), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("document_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_credential_licenses"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["compliance_documents.id"],
            name="fk_credential_licenses_document_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive', 'expired', 'suspended', 'revoked')",
            name="ck_credential_licenses_status",
        ),
        sa.CheckConstraint(
            "verification_source IN ('self', 'nppes', 'board')",
            name="ck_credential_licenses_verification_source",
        ),
        sa.UniqueConstraint(
            "user_id",
            "state",
            "license_number",
            name="ux_credential_licenses_user_state_number",
        ),
    )
    op.create_index("ix_credential_licenses_user_id", "credential_licenses", ["user_id"])
    # Partial, so a clinician may hold many licences but only one primary.
    op.create_index(
        "ux_credential_licenses_one_primary",
        "credential_licenses",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )

    op.create_table(
        "credential_liability_policies",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("carrier_name", sa.String(length=255), nullable=False),
        sa.Column("policy_number", sa.String(length=100), nullable=True),
        sa.Column("per_occurrence_cents", sa.BigInteger(), nullable=True),
        sa.Column("aggregate_cents", sa.BigInteger(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_credential_liability_policies"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["compliance_documents.id"],
            name="fk_credential_liability_policies_document_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_credential_liability_policies_user_id",
        "credential_liability_policies",
        ["user_id"],
    )
    op.create_index(
        "ux_credential_liability_policies_one_current",
        "credential_liability_policies",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )

    op.create_table(
        "credential_education",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("institution", sa.String(length=255), nullable=False),
        sa.Column("degree", sa.String(length=100), nullable=True),
        sa.Column("field_of_study", sa.String(length=255), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_credential_education"),
    )
    op.create_index("ix_credential_education_user_id", "credential_education", ["user_id"])

    op.create_table(
        "credential_training",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("program_type", sa.String(length=50), nullable=False),
        sa.Column("institution", sa.String(length=255), nullable=False),
        sa.Column("specialty", sa.String(length=255), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("supervisor_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_credential_training"),
    )
    op.create_index("ix_credential_training_user_id", "credential_training", ["user_id"])

    op.create_table(
        "credential_employment",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("employer_name", sa.String(length=255), nullable=False),
        sa.Column("position", sa.String(length=255), nullable=True),
        sa.Column("address_line1", sa.String(length=255), nullable=True),
        sa.Column("address_line2", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=2), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("preceding_gap_explanation", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_credential_employment"),
    )
    op.create_index("ix_credential_employment_user_id", "credential_employment", ["user_id"])

    op.create_table(
        "credential_references",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=True),
        sa.Column("credential", sa.String(length=100), nullable=True),
        sa.Column("organization", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("relationship", sa.String(length=100), nullable=True),
        sa.Column("years_known", sa.SmallInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_credential_references"),
    )
    op.create_index("ix_credential_references_user_id", "credential_references", ["user_id"])

    op.create_table(
        "credential_disclosures",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("question_key", sa.String(length=64), nullable=False),
        sa.Column("question_version", sa.SmallInteger(), nullable=False),
        sa.Column("answer", sa.Boolean(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_credential_disclosures"),
        # An unexplained "yes" is not an answer a payer accepts, and finding
        # that out at submission time costs a review cycle.
        sa.CheckConstraint(
            "answer IS NOT TRUE OR explanation IS NOT NULL",
            name="ck_credential_disclosures_explained",
        ),
        # Two versions of one question coexist: the version is part of what
        # she attested to, not a column that gets overwritten.
        sa.UniqueConstraint(
            "user_id",
            "question_key",
            "question_version",
            name="ux_credential_disclosures_user_key_version",
        ),
    )

    op.create_table(
        "credential_service_locations",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("address_line1", sa.String(length=255), nullable=False),
        sa.Column("address_line2", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=2), nullable=False),
        sa.Column("postal_code", sa.String(length=20), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("fax", sa.String(length=32), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("accepts_new_patients", sa.Boolean(), nullable=False),
        sa.Column("hours", postgresql.JSONB(), nullable=True),
        sa.Column("ada_accessible", sa.Boolean(), nullable=True),
        sa.Column("languages", postgresql.JSONB(), nullable=True),
        sa.Column("telehealth_only", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_credential_service_locations"),
    )
    op.create_index(
        "ix_credential_service_locations_user_id", "credential_service_locations", ["user_id"]
    )
    op.create_index(
        "ux_credential_service_locations_one_primary",
        "credential_service_locations",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )

    op.create_table(
        "credential_bank_accounts",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("account_holder_name", sa.String(length=255), nullable=False),
        sa.Column("routing_number_encrypted", sa.Text(), nullable=False),
        sa.Column("routing_number_last4", sa.String(length=4), nullable=True),
        sa.Column("account_number_encrypted", sa.Text(), nullable=False),
        sa.Column("account_number_last4", sa.String(length=4), nullable=True),
        sa.Column("account_type", sa.String(length=8), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_credential_bank_accounts"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["compliance_documents.id"],
            name="fk_credential_bank_accounts_document_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "account_type IN ('checking', 'savings')",
            name="ck_credential_bank_accounts_account_type",
        ),
    )
    op.create_index("ix_credential_bank_accounts_user_id", "credential_bank_accounts", ["user_id"])

    op.create_table(
        "payer_participations",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("payer_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("credentialed_at", sa.Date(), nullable=True),
        sa.Column("contracted_at", sa.Date(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("termination_date", sa.Date(), nullable=True),
        sa.Column("recredentialing_due_at", sa.Date(), nullable=True),
        sa.Column("provider_id_with_payer", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_payer_participations"),
        sa.ForeignKeyConstraint(
            ["payer_id"],
            ["payers.id"],
            name="fk_payer_participations_payer_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"status IN ({_PARTICIPATION_STATUSES})",
            name="ck_payer_participations_status",
        ),
        # Participation is per clinician against one payer — which is exactly
        # why it cannot be a column on ``payers``.
        sa.UniqueConstraint("user_id", "payer_id", name="ux_payer_participations_user_payer"),
    )
    op.create_index("ix_payer_participations_payer_id", "payer_participations", ["payer_id"])
    op.create_index("ix_payer_participations_user_id", "payer_participations", ["user_id"])

    op.create_table(
        "payer_participation_events",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("participation_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_payer_participation_events"),
        sa.ForeignKeyConstraint(
            ["participation_id"],
            ["payer_participations.id"],
            name="fk_payer_participation_events_participation_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"to_status IN ({_PARTICIPATION_STATUSES})",
            name="ck_payer_participation_events_to_status",
        ),
        sa.CheckConstraint(
            f"from_status IS NULL OR from_status IN ({_PARTICIPATION_STATUSES})",
            name="ck_payer_participation_events_from_status",
        ),
    )
    op.create_index(
        "ix_payer_participation_events_participation_id",
        "payer_participation_events",
        ["participation_id"],
    )
    op.create_index(
        "ix_payer_participation_events_user_id", "payer_participation_events", ["user_id"]
    )


def downgrade() -> None:
    op.drop_table("payer_participation_events")
    op.drop_table("payer_participations")
    op.drop_table("credential_bank_accounts")
    op.drop_table("credential_service_locations")
    op.drop_table("credential_disclosures")
    op.drop_table("credential_references")
    op.drop_table("credential_employment")
    op.drop_table("credential_training")
    op.drop_table("credential_education")
    op.drop_table("credential_liability_policies")
    op.drop_table("credential_licenses")
    op.drop_table("credential_government_ids")
