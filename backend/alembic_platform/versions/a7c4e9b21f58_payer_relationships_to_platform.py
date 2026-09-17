# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Her payer relationships move to the platform schema

The rest of the credentialing surface, following the eleven that moved in
``b6e2f8a41c37``. Whether a clinician is on a payer's panel, how that status
got there, what the resulting contract pays, and the authority she signed for
Pablo to pursue it on her behalf — the concierge operator reads all four to do
the work, and held per-tenant that is a scan of every schema in the database to
answer a question about one person.

SAFE NOW AND ONLY NOW. All four hold zero rows in every practice schema, in
both environments. Verified by heap size rather than ``count(*)``: these are
force-RLS'd in 76 of dev's 159 schemas and the app role is ``NOBYPASSRLS``, so
a bare count returns 0 whether or not rows exist — which is the reading that
once reported a tenant holding 2,494 patients as empty. A relation with no heap
pages holds nothing, and row security does not change that.

THE PAYER FOREIGN KEY DOES NOT SURVIVE, AND SHOULD NOT. ``payers`` is properly
per-tenant: it carries the practice's own electronic enrollment state with an
insurer — ``enrollment_status``, ``clearinghouse_payer_id``, which transactions
to file — and two practices hold those differently for the same company. A
platform table cannot reference a per-tenant one, so ``payer_id`` becomes a
plain uuid and ``practice_id`` says which schema resolves it. That is the shape
``panel_applications`` has had in production since ``c4d81e6a2f09``.

WHICH FORCES THE UNIQUE KEY WIDER. ``(user_id, payer_id)`` identified one
participation per clinician per payer while both sides lived in one schema. In
platform it identifies nothing, because the same insurer is a different uuid in
every practice's ``payers`` table — so the key becomes
``(user_id, practice_id, payer_id)``. This is not a relaxation. Panel
participation is contracted per billing entity, so a clinician working in two
practices genuinely holds two statuses against the same insurer, and the old
key could not express that.

``ondelete="CASCADE"`` goes with the foreign key, and its absence is an
improvement: deleting a payer row no longer erases the participation history
beneath it. A panel she was on and a payer the practice stopped filing with are
different facts, and losing the first because of the second silently rewrites
when she was in network.

``contracted_rates`` keeps its ``source_document_id`` and loses the foreign key
to ``compliance_documents`` for the same reason the licences did, and gains
``practice_id`` to resolve it. The events table gains no such column, because
it points at no document.

ROW SECURITY REPLACES THE SCHEMA BOUNDARY, AND TIGHTENS IT. All four were
supposed to be force-RLS'd per-tenant already and were not: ``payer_participations``,
its events and ``contracted_rates`` in 83 of 159 dev schemas, and
``payer_authorizations`` in 158 of 159 — a tenant migration that adds a
row-scoped table gets row security only if the revision says so, and nothing
asserted otherwise. Creating them here with ENABLE, FORCE and a policy closes
that rather than carrying it across. FORCE matters specifically: the app
connects as the table owner, and an owner is exempt from its own policies
without it.

PHI-free by construction — every row is about a clinician and an insurer, and
no patient appears in any of them.

Sits after ``d8e4a6b02f19`` rather than after the eleven it continues:
``d8e4a6b02f19`` landed on the platform chain first, so that is the head this
has to follow. Two revisions naming the same parent is what leaves alembic
with two heads, and git merges both without complaint.

Revision ID: a7c4e9b21f58
Revises: d8e4a6b02f19
Create Date: 2026-09-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "a7c4e9b21f58"
down_revision: str | Sequence[str] | None = "d8e4a6b02f19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Creation order is dependency order: the events and the rates both reference
#: ``payer_participations``, and those two foreign keys DO survive, because
#: both sides of them are moving together.
_TABLES = (
    "payer_authorizations",
    "payer_participations",
    "payer_participation_events",
    "contracted_rates",
)

#: The concierge operator's way across, applied only where the role exists so a
#: self-hosted install and CI are unaffected. SELECT, INSERT and UPDATE but
#: never DELETE: a terminated panel and a superseded rate are history worth
#: keeping, and nothing in the operator flow erases one.
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
    """Generated from ``app.db.platform_models``, not typed by hand.

    The drift gate compares a capture of this chain against the models, so DDL
    copied by eye is exactly how that gate starts failing for a reason nobody
    can see.

    ``IF NOT EXISTS`` throughout: the platform template may already carry these
    on a database built from it, and a revision that cannot be re-run is a
    revision that fails the second time somebody needs it to work.
    """
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.payer_authorizations (
            id UUID NOT NULL,
            user_id UUID NOT NULL,
            kind VARCHAR(40) NOT NULL,
            version VARCHAR(20) NOT NULL,
            full_text TEXT NOT NULL,
            signed_name VARCHAR(200) NOT NULL,
            signed_at TIMESTAMP WITH TIME ZONE NOT NULL,
            revoked_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_payer_authorizations_kind CHECK (
                kind IN ('credentialing_authorization', 'services_agreement'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_payer_authorizations_user_id "
        "ON platform.payer_authorizations (user_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.payer_participations (
            id UUID NOT NULL,
            user_id UUID NOT NULL,
            practice_id VARCHAR(128) NOT NULL,
            payer_id UUID NOT NULL,
            status VARCHAR(24) NOT NULL,
            credentialed_at DATE,
            contracted_at DATE,
            effective_date DATE,
            termination_date DATE,
            recredentialing_due_at DATE,
            provider_id_with_payer VARCHAR(80),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_payer_participations_status CHECK (
                status IN (
                    'out_of_network', 'application_submitted', 'credentialed', 'contracted',
                    'in_network', 'single_case_agreement', 'denied', 'terminated')),
            CONSTRAINT ux_payer_participations_user_practice_payer UNIQUE (
                user_id, practice_id, payer_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_payer_participations_payer_id "
        "ON platform.payer_participations (payer_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_payer_participations_practice_id "
        "ON platform.payer_participations (practice_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_payer_participations_user_id "
        "ON platform.payer_participations (user_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.payer_participation_events (
            id UUID NOT NULL,
            participation_id UUID NOT NULL,
            user_id UUID NOT NULL,
            from_status VARCHAR(24),
            to_status VARCHAR(24) NOT NULL,
            occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
            note TEXT,
            detail JSONB NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_payer_participation_events_to_status CHECK (
                to_status IN (
                    'out_of_network', 'application_submitted', 'credentialed', 'contracted',
                    'in_network', 'single_case_agreement', 'denied', 'terminated')),
            CONSTRAINT ck_payer_participation_events_from_status CHECK (
                from_status IS NULL OR
                from_status IN (
                    'out_of_network', 'application_submitted', 'credentialed', 'contracted',
                    'in_network', 'single_case_agreement', 'denied', 'terminated')),
            FOREIGN KEY(participation_id)
                REFERENCES platform.payer_participations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_payer_participation_events_participation_id "
        "ON platform.payer_participation_events (participation_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_payer_participation_events_user_id "
        "ON platform.payer_participation_events (user_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.contracted_rates (
            id UUID NOT NULL,
            participation_id UUID NOT NULL,
            user_id UUID NOT NULL,
            practice_id VARCHAR(128) NOT NULL,
            cpt VARCHAR(10) NOT NULL,
            modifier VARCHAR(8) NOT NULL,
            basis VARCHAR(16) NOT NULL,
            amount_cents INTEGER,
            percent NUMERIC(7, 3),
            mpfs_amount_cents INTEGER,
            effective_date DATE NOT NULL,
            end_date DATE,
            source_document_id UUID,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_contracted_rates_basis CHECK (basis IN ('fixed', 'percent_of_mpfs')),
            CONSTRAINT ck_contracted_rates_basis_fields CHECK (
                (basis = 'fixed' AND amount_cents IS NOT NULL AND percent IS NULL) OR
                (basis = 'percent_of_mpfs' AND percent IS NOT NULL AND amount_cents IS NULL)),
            CONSTRAINT ck_contracted_rates_amount CHECK (amount_cents IS NULL OR amount_cents >= 0),
            CONSTRAINT ck_contracted_rates_percent CHECK (percent IS NULL OR percent > 0),
            CONSTRAINT ck_contracted_rates_date_order CHECK (
                end_date IS NULL OR end_date >= effective_date),
            CONSTRAINT ux_contracted_rates_participation_code_date UNIQUE (
                participation_id, cpt, modifier, effective_date),
            FOREIGN KEY(participation_id)
                REFERENCES platform.payer_participations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_contracted_rates_participation_id "
        "ON platform.contracted_rates (participation_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_contracted_rates_practice_id "
        "ON platform.contracted_rates (practice_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_contracted_rates_user_id "
        "ON platform.contracted_rates (user_id)"
    )


def _enable_row_security() -> None:
    """One clinician, her own rows — enforced by the database, not the query.

    Every one of the four carries ``user_id`` beside whatever else identifies
    it, which is what lets the events and the rates take their parent's
    predicate without the policy engine learning a join.
    """
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
    """Mirrors ``b6e2f8a41c37`` and ``c4d81e6a2f09``, on the same guard."""
    for table in _TABLES:
        # ``replace`` on a constant rather than an f-string: the table name
        # comes from the tuple above and never from a caller.
        op.execute(_OPERATOR_ACCESS.replace("<table>", table))


def downgrade() -> None:
    """Drop them here. The tenant-side revision puts them back per schema.

    Reversed, so the two tables holding foreign keys go before the one they
    point at.
    """
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS platform.{table}")
