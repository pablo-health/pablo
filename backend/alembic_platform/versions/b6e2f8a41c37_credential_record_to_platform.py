# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The clinician's credential record moves to the platform schema

Pablo runs credentialing as a concierge service, so the operator has to read a
clinician's licences, education, employment and disclosures in order to file a
panel application on her behalf. Held inside each practice schema that is a scan
of every schema in the database to answer a question about one person — 159 of
them on dev. ``panel_applications`` moved for exactly this reason in
``c4d81e6a2f09``; these eleven are the rest of the same surface.

SAFE NOW AND ONLY NOW. Every one of these tables holds zero rows, in every
schema, in both environments — measured, not assumed. The surface was built but
never used: nothing in the product wrote to it. So this is a create here and a
drop there, with no backfill and no window in which a row could be lost. Once
the concierge pilot files anything the same change becomes a data migration.

ROW SECURITY, not schema separation. Each table is scoped to the clinician who
owns the row, so each gets ENABLE plus FORCE plus an owner policy keyed on the
same ``app.current_user_id`` GUC the practice schemas use. FORCE matters: the
app connects as the table owner, and an owner is exempt from its own policies
unless forced — that is the specific way "we enabled RLS" comes to mean nothing.

The isolation is not decoration. These hold what a clinician would least like a
colleague to browse: a disclosure, a malpractice history, the numbers she files
taxes under. ``credential_government_ids`` in particular holds an encrypted SSN
and date of birth, which is why it is row-secured here rather than relying on
the schema boundary it is leaving.

PHI-free by construction — every row is about a clinician, and no patient
appears in any of them.

THE DOCUMENT REFERENCES LOSE THEIR FOREIGN KEYS. ``credential_licenses``,
``credential_liability_policies`` and ``credential_bank_accounts`` referenced
``compliance_documents``, which stays per-tenant; a platform table cannot
reference one. The column survives and ``practice_id`` says which schema
resolves it. Nothing dangles today because both sides are empty, and the
constraint comes back when the compliance cluster follows (PABLO-g7oe).

Revision ID: b6e2f8a41c37
Revises: c3d9e5f14a80
Create Date: 2026-09-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "b6e2f8a41c37"
down_revision: str | Sequence[str] | None = "c3d9e5f14a80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The eleven, in the order they are created. No foreign keys between them, so
#: the order is alphabetical-ish rather than load-bearing.
_TABLES = (
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
)

#: The operator's reach, applied per table with the name substituted in.
#: Guarded on the role existing so installs without it are untouched.
_OPERATOR_ACCESS = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pablo_credentialing_ops') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE ON platform.<table> '
         || 'TO pablo_credentialing_ops';
    EXECUTE 'DROP POLICY IF EXISTS credential_operator ON platform.<table>';
    EXECUTE 'CREATE POLICY credential_operator ON platform.<table> '
         || 'FOR ALL TO pablo_credentialing_ops USING (true) WITH CHECK (true)';
  END IF;
END
$$
"""


def upgrade() -> None:
    _create_tables()
    _enable_row_security()
    _grant_the_operator()


def _create_tables() -> None:
    """Generated from ``app.db.platform_models`` — see the module docstring.

    ``IF NOT EXISTS`` throughout: the platform template may already carry these
    on a database built from it, and a revision that cannot be re-run is a
    revision that fails the second time somebody needs it to work.
    """
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.credential_government_ids (
            user_id UUID NOT NULL,
            ssn_encrypted TEXT,
            ssn_last4 VARCHAR(4),
            dob_encrypted TEXT,
            tax_id_type VARCHAR(3),
            tax_id_encrypted TEXT,
            tax_id_last4 VARCHAR(4),
            type2_npi VARCHAR(20),
            business_structure VARCHAR(40),
            sole_proprietor BOOLEAN,
            supervision_status VARCHAR(16),
            caqh_id VARCHAR(32),
            medicare_intent BOOLEAN,
            medicaid_intent BOOLEAN,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (user_id),
            CONSTRAINT ck_credential_government_ids_tax_id_type CHECK (
                tax_id_type IS NULL OR tax_id_type IN ('ein', 'ssn')),
            CONSTRAINT ck_credential_government_ids_supervision_status CHECK (
                supervision_status IS NULL OR supervision_status IN ('independent', 'supervised'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.credential_licenses (
            id UUID NOT NULL,
            user_id UUID NOT NULL,
            practice_id VARCHAR(128) NOT NULL,
            license_type VARCHAR(50) NOT NULL,
            license_number VARCHAR(100) NOT NULL,
            state VARCHAR(2) NOT NULL,
            issue_date DATE,
            expiration_date DATE,
            status VARCHAR(16) NOT NULL,
            is_primary BOOLEAN NOT NULL,
            verification_source VARCHAR(8) NOT NULL,
            verified_at TIMESTAMP WITH TIME ZONE,
            document_id UUID,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_credential_licenses_status CHECK (
                status IN ('active', 'inactive', 'expired', 'suspended', 'revoked')),
            CONSTRAINT ck_credential_licenses_verification_source CHECK (
                verification_source IN ('self', 'nppes', 'board')),
            CONSTRAINT ux_credential_licenses_user_state_number UNIQUE (
                user_id, state, license_number)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_credential_licenses_user_id
            ON platform.credential_licenses (user_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_platform_credential_licenses_practice_id
            ON platform.credential_licenses (practice_id)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_credential_licenses_one_primary
            ON platform.credential_licenses (user_id) WHERE is_primary
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.credential_liability_policies (
            id UUID NOT NULL,
            user_id UUID NOT NULL,
            practice_id VARCHAR(128) NOT NULL,
            carrier_name VARCHAR(255) NOT NULL,
            policy_number VARCHAR(100),
            per_occurrence_cents BIGINT,
            aggregate_cents BIGINT,
            effective_date DATE,
            expiration_date DATE,
            is_current BOOLEAN NOT NULL,
            document_id UUID,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_credential_liability_policies_user_id
            ON platform.credential_liability_policies (user_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_platform_credential_liability_policies_practice_id
            ON platform.credential_liability_policies (practice_id)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_credential_liability_policies_one_current
            ON platform.credential_liability_policies (user_id) WHERE is_current
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.credential_education (
            id UUID NOT NULL,
            user_id UUID NOT NULL,
            institution VARCHAR(255) NOT NULL,
            degree VARCHAR(100),
            field_of_study VARCHAR(255),
            start_date DATE,
            end_date DATE,
            country VARCHAR(2),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_credential_education_user_id
            ON platform.credential_education (user_id)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.credential_training (
            id UUID NOT NULL,
            user_id UUID NOT NULL,
            program_type VARCHAR(50) NOT NULL,
            institution VARCHAR(255) NOT NULL,
            specialty VARCHAR(255),
            start_date DATE,
            end_date DATE,
            supervisor_name VARCHAR(255),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_credential_training_user_id
            ON platform.credential_training (user_id)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.credential_employment (
            id UUID NOT NULL,
            user_id UUID NOT NULL,
            employer_name VARCHAR(255) NOT NULL,
            position VARCHAR(255),
            address_line1 VARCHAR(255),
            address_line2 VARCHAR(255),
            city VARCHAR(100),
            state VARCHAR(2),
            postal_code VARCHAR(20),
            start_date DATE NOT NULL,
            end_date DATE,
            preceding_gap_explanation TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_credential_employment_user_id
            ON platform.credential_employment (user_id)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.credential_references (
            id UUID NOT NULL,
            user_id UUID NOT NULL,
            full_name VARCHAR(255) NOT NULL,
            title VARCHAR(100),
            credential VARCHAR(100),
            organization VARCHAR(255),
            email VARCHAR(255),
            phone VARCHAR(32),
            relationship VARCHAR(100),
            years_known SMALLINT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_credential_references_user_id
            ON platform.credential_references (user_id)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.credential_disclosures (
            id UUID NOT NULL,
            user_id UUID NOT NULL,
            question_key VARCHAR(64) NOT NULL,
            question_version SMALLINT NOT NULL,
            answer BOOLEAN NOT NULL,
            explanation TEXT,
            answered_at TIMESTAMP WITH TIME ZONE NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_credential_disclosures_explained CHECK (
                answer IS NOT TRUE OR explanation IS NOT NULL),
            CONSTRAINT ux_credential_disclosures_user_key_version UNIQUE (
                user_id, question_key, question_version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.credential_confirmations (
            id UUID NOT NULL,
            user_id UUID NOT NULL,
            field_key VARCHAR(64) NOT NULL,
            source VARCHAR(32) NOT NULL,
            presented_value TEXT,
            confirmed BOOLEAN NOT NULL,
            correction TEXT,
            confirmed_at TIMESTAMP WITH TIME ZONE NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_credential_confirmations_source CHECK (
                source IN (
                    'nppes', 'pecos_public_file', 'leie_sam', 'clinician_profiles',
                        'practice_billing_profile')),
            CONSTRAINT ck_credential_confirmations_corrected CHECK (
                confirmed OR correction IS NOT NULL),
            CONSTRAINT ux_credential_confirmations_user_field UNIQUE (user_id, field_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.credential_service_locations (
            id UUID NOT NULL,
            user_id UUID NOT NULL,
            name VARCHAR(255),
            address_line1 VARCHAR(255) NOT NULL,
            address_line2 VARCHAR(255),
            city VARCHAR(100) NOT NULL,
            state VARCHAR(2) NOT NULL,
            postal_code VARCHAR(20) NOT NULL,
            phone VARCHAR(32),
            fax VARCHAR(32),
            is_primary BOOLEAN NOT NULL,
            accepts_new_patients BOOLEAN NOT NULL,
            hours JSONB,
            ada_accessible BOOLEAN,
            languages JSONB,
            telehealth_only BOOLEAN NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_credential_service_locations_user_id
            ON platform.credential_service_locations (user_id)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_credential_service_locations_one_primary
            ON platform.credential_service_locations (user_id) WHERE is_primary
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.credential_bank_accounts (
            id UUID NOT NULL,
            user_id UUID NOT NULL,
            practice_id VARCHAR(128) NOT NULL,
            account_holder_name VARCHAR(255) NOT NULL,
            routing_number_encrypted TEXT NOT NULL,
            routing_number_last4 VARCHAR(4),
            account_number_encrypted TEXT NOT NULL,
            account_number_last4 VARCHAR(4),
            account_type VARCHAR(8) NOT NULL,
            document_id UUID,
            verified_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_credential_bank_accounts_account_type CHECK (
                account_type IN ('checking', 'savings'))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_credential_bank_accounts_user_id
            ON platform.credential_bank_accounts (user_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_platform_credential_bank_accounts_practice_id
            ON platform.credential_bank_accounts (practice_id)
        """
    )


def _enable_row_security() -> None:
    """One clinician, her own rows — enforced by the database, not the query."""
    for table in _TABLES:
        qualified = f"platform.{table}"
        op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
        # Without FORCE the owner — which is how the app connects — is exempt.
        op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS rls_credential_owner ON {qualified}")
        op.execute(
            f"CREATE POLICY rls_credential_owner ON {qualified} "
            "USING (user_id::text = current_setting('app.current_user_id', true)) "
            "WITH CHECK (user_id::text = current_setting('app.current_user_id', true))"
        )


def _grant_the_operator() -> None:
    """The concierge operator's way across, where that role exists.

    Guarded on the role being present, so a self-hosted install and CI — which
    have no such role — are unaffected. Mirrors ``c4d81e6a2f09`` and
    ``c3d9e5f14a80``, which do the same for ``panel_applications``.

    SELECT, INSERT and UPDATE but never DELETE: a withdrawn licence or a
    corrected disclosure is history worth keeping, and nothing in the operator
    flow erases one.
    """
    for table in _TABLES:
        # ``replace`` on a constant rather than an f-string: the table name comes
        # from the tuple above and never from a caller, and spelling it this way
        # keeps that obvious to a reader and to the linter alike.
        op.execute(_OPERATOR_ACCESS.replace("<table>", table))


def downgrade() -> None:
    """Drop them here. The tenant-side revision puts them back per schema.

    Safe in the same narrow sense the upgrade is: there is nothing in them.
    """
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS platform.{table}")
